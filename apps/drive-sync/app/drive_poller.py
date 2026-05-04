"""Google Drive incremental poller.

Every POLL_INTERVAL_SECONDS:
  For each active team_drive_mapping:
    1. Load OAuth credentials (decrypt Fernet)
    2. Call changes.list(pageToken=change_token)
    3. Persist newStartPageToken BEFORE processing (idempotent on crash)
    4. For each changed file: export text -> send to ingestion agent
    5. For each removed file: soft-archive in memory-api
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import random
import time
from typing import Any

import asyncpg
import structlog
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings
from app.ingestion_client import send_to_ingestion_agent, soft_archive_drive_file

log = structlog.get_logger(__name__)

MAX_BYTES = 50_000

EXPORTABLE_MIMES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
}

PDF_MIME = "application/pdf"
MD_MIME = "text/markdown"
TEXT_MIME = "text/plain"


def _decrypt_credentials(enc: str) -> dict:
    """Decrypt Fernet-encrypted OAuth credentials. Key must be set via env var."""
    f = Fernet(settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY.encode())
    return json.loads(f.decrypt(enc.encode()).decode())


def _build_drive_service(creds_dict: dict):
    """Build Google Drive API service from credentials dict. Refreshes token if expired."""
    creds = Credentials(
        token=creds_dict.get("access_token"),
        refresh_token=creds_dict.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    # Refresh if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _export_file_text(service, file_id: str, mime: str) -> str | None:
    """Export a Drive file as text. Returns None if unsupported type.

    Supports:
    - Google Docs/Sheets/Slides: export as text/plain
    - PDFs: download + pypdf extract
    - Markdown/plain text: raw download

    File content is capped at MAX_BYTES to prevent OOM on huge files.
    Content is never logged -- only file_id and mime (T-03-11-04).
    """
    try:
        if mime in EXPORTABLE_MIMES:
            content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")[:MAX_BYTES]
            return str(content)[:MAX_BYTES]
        elif mime == PDF_MIME:
            import io

            from googleapiclient.http import MediaIoBaseDownload
            from pypdf import PdfReader

            fh = io.BytesIO()
            request = service.files().get_media(fileId=file_id)
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)
            reader = PdfReader(fh)
            return "\n\n".join(p.extract_text() or "" for p in reader.pages)[:MAX_BYTES]
        elif mime in (MD_MIME, TEXT_MIME):
            content = service.files().get_media(fileId=file_id).execute()
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")[:MAX_BYTES]
        return None
    except HttpError as exc:
        log.warning("poll.export_error", file_id=file_id, status=exc.resp.status)
        return None


def _with_backoff(fn, max_retries: int = 5) -> Any:
    """Exponential backoff for Drive API calls (429, 500, 503).

    Waits up to 64s between retries. Per RESEARCH.md Q4 rate limit strategy.
    """
    for n in range(max_retries):
        try:
            return fn()
        except HttpError as exc:
            if exc.resp.status not in (429, 500, 503):
                raise
            wait = min(2**n + random.random(), 64)
            log.warning("poll.backoff", attempt=n, wait=round(wait, 1), status=exc.resp.status)
            time.sleep(wait)
    raise RuntimeError("Drive API: max retries exceeded")


async def poll_team(conn: asyncpg.Connection, row: asyncpg.Record) -> None:
    """Poll Drive changes for one team mapping and process them.

    After migration 0005 a team can have N folder mappings. poll_team is called
    once per row (i.e. once per folder), passing the specific mapping row so that
    project_scope is carried through to send_to_ingestion_agent.

    Token persist strategy (RISK-04):
      - Persist newStartPageToken BEFORE processing changes.
      - If service crashes mid-batch, same changes are re-processed on restart.
      - Idempotent because memory-api upserts on UNIQUE(source, team_scope).

    Deletion strategy (RESEARCH.md Q4):
      - change.removed == True -> soft_archive_drive_file()
      - Facts at WORKING+ are archived (validation_status='archived')
      - Facts at EPHEMERAL are hard-deleted

    410 handling (RESEARCH.md Q1):
      - Token expired (>30 days unused) -> re-baseline via getStartPageToken()
      - Next tick will full re-sync (idempotent via upsert)
    """
    mapping_id = row["id"]
    team_scope = row["team_scope"]
    project_scope = row["project_scope"]  # None for legacy mappings without project tag
    change_token = row["change_token"]
    creds_enc = row["oauth_credentials_enc"]

    if not creds_enc:
        log.warning("poll.no_credentials", team=team_scope)
        return

    creds_dict = _decrypt_credentials(creds_enc)
    service = _build_drive_service(creds_dict)

    try:
        if not change_token:
            # First run -- get baseline token (full re-sync will follow on next tick)
            resp = _with_backoff(lambda: service.changes().getStartPageToken().execute())
            change_token = resp["startPageToken"]
            log.info("poll.first_run_baseline", team=team_scope)

        changes_resp = _with_backoff(
            lambda: service.changes().list(
                pageToken=change_token,
                includeRemoved=True,
                spaces="drive",
            ).execute()
        )
    except HttpError as exc:
        if exc.resp.status == 410:
            # Token expired (>30 days unused) -- re-baseline
            log.warning("poll.token_expired_410", team=team_scope, mapping_id=str(mapping_id))
            resp = _with_backoff(lambda: service.changes().getStartPageToken().execute())
            new_token = resp["startPageToken"]
            await conn.execute(
                "UPDATE team_drive_mappings SET change_token=$1, updated_at=now() WHERE id=$2",
                new_token,
                mapping_id,
            )
            return  # Will full re-sync on next tick
        raise

    new_token = changes_resp.get("newStartPageToken")
    changes = changes_resp.get("changes", [])
    log.info("poll.changes_fetched", team=team_scope, count=len(changes), new_token=bool(new_token))

    # CRITICAL: Persist token BEFORE processing -- idempotent on crash restart (RISK-04)
    # Use mapping_id (PK) not team_scope — multiple mappings may share the same team_scope.
    if new_token:
        await conn.execute(
            "UPDATE team_drive_mappings SET change_token=$1, updated_at=now() WHERE id=$2",
            new_token,
            mapping_id,
        )

    for change in changes:
        file_id = change.get("fileId")
        if not file_id:
            continue

        if change.get("removed"):
            # File deleted from Drive -- soft archive in memory-api
            log.info("poll.file_deleted", team=team_scope, file_id=file_id)
            await soft_archive_drive_file(file_id, team_scope)
            continue

        file_meta = change.get("file", {})
        mime = file_meta.get("mimeType", "")
        name = file_meta.get("name", file_id)
        # Note: file content is NOT logged (T-03-11-04 — information disclosure mitigation)
        log.info("poll.file_changed", team=team_scope, file_id=file_id, mime=mime)

        text = _export_file_text(service, file_id, mime)
        if text is None:
            log.info("poll.file_skipped", team=team_scope, file_id=file_id, mime=mime)
            continue

        await send_to_ingestion_agent(
            text=text,
            file_id=file_id,
            file_name=name,
            team_scope=team_scope,
            project_scope=project_scope,  # from mapping row — None for legacy mappings
        )


async def register_watch_channel(pool: asyncpg.Pool, mapping_row) -> bool:
    """Register a Google Drive push notification channel for a mapping.

    Calls changes.watch() with address=DRIVE_WEBHOOK_PUBLIC_URL.
    Stores channel_id, resource_id, channel_token, expires_at in drive_watch_channels.
    Returns True on success, False on failure (non-fatal).

    Google channel lifetime: we request 24h (86400s) — max is 7 days.
    Uses ON CONFLICT (mapping_id) DO UPDATE so re-registering is idempotent.
    """
    import datetime as _dt
    import uuid as _uuid

    if not settings.DRIVE_WEBHOOK_PUBLIC_URL:
        log.warning("watch.no_public_url", mapping_id=str(mapping_row["id"]))
        return False

    creds_enc = mapping_row.get("oauth_credentials_enc") if hasattr(mapping_row, "get") else mapping_row["oauth_credentials_enc"]
    if not creds_enc:
        return False

    creds_dict = _decrypt_credentials(creds_enc)
    service = _build_drive_service(creds_dict)
    channel_token = settings.DRIVE_WEBHOOK_SECRET or str(_uuid.uuid4())
    channel_id = str(_uuid.uuid4())

    try:
        resp = _with_backoff(
            lambda: service.changes()
            .watch(
                pageToken=mapping_row["change_token"] or "1",
                body={
                    "id": channel_id,
                    "type": "web_hook",
                    "address": settings.DRIVE_WEBHOOK_PUBLIC_URL,
                    "token": channel_token,
                    "expiration": int(
                        (
                            _dt.datetime.utcnow() + _dt.timedelta(hours=24)
                        ).timestamp()
                        * 1000
                    ),
                },
            )
            .execute()
        )
    except Exception as exc:
        log.warning(
            "watch.register_failed",
            mapping_id=str(mapping_row["id"]),
            error=str(exc),
        )
        return False

    expires_ms = int(resp.get("expiration", 0))
    expires_at = (
        _dt.datetime.utcfromtimestamp(expires_ms / 1000)
        if expires_ms
        else (_dt.datetime.utcnow() + _dt.timedelta(hours=24))
    )

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO drive_watch_channels
                (channel_id, resource_id, mapping_id, channel_token, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (mapping_id) DO UPDATE
            SET channel_id     = EXCLUDED.channel_id,
                resource_id    = EXCLUDED.resource_id,
                channel_token  = EXCLUDED.channel_token,
                expires_at     = EXCLUDED.expires_at
            """,
            channel_id,
            resp.get("resourceId"),
            mapping_row["id"],
            channel_token,
            expires_at,
        )
    log.info(
        "watch.channel_registered",
        channel_id=channel_id,
        team=mapping_row["team_scope"],
    )
    return True


async def run_channel_renewal_loop(pool: asyncpg.Pool) -> None:
    """Renew Drive watch channels that expire within the next 2 hours.

    Runs every 12h. Channels expire after 24h (our setting), so renewal at T-2h
    gives a safe margin. On renewal failure: logs warning, polling fallback covers.
    """
    import datetime as _dt  # noqa: F811

    while True:
        await asyncio.sleep(12 * 3600)
        try:
            async with pool.acquire() as conn:
                # Find channels expiring within 2 hours
                expiring = await conn.fetch(
                    """
                    SELECT dwc.id, dwc.mapping_id, tdm.team_scope,
                           tdm.change_token, tdm.oauth_credentials_enc, tdm.folder_id,
                           tdm.id AS tdm_id
                    FROM drive_watch_channels dwc
                    JOIN team_drive_mappings tdm ON dwc.mapping_id = tdm.id
                    WHERE dwc.expires_at < (now() + interval '2 hours')
                    """
                )
            for row in expiring:
                # Build a dict-like object with the fields register_watch_channel needs
                mapping_like = {
                    "id": row["mapping_id"],
                    "team_scope": row["team_scope"],
                    "change_token": row["change_token"],
                    "oauth_credentials_enc": row["oauth_credentials_enc"],
                    "folder_id": row["folder_id"],
                }
                ok = await register_watch_channel(pool, mapping_like)
                if ok:
                    log.info("watch.channel_renewed", team=row["team_scope"])
                else:
                    log.warning("watch.channel_renewal_failed", team=row["team_scope"])
        except Exception as exc:
            log.error("watch.renewal_loop_error", error=str(exc))


async def run_poll_loop(database_url: str) -> None:
    """Main polling loop -- runs forever, polls all teams every POLL_INTERVAL_SECONDS.

    Resilient: team-level errors are caught and logged; other teams continue.
    Loop-level errors are caught and logged; loop continues after sleep.
    Sentinel file /tmp/drive-sync-alive is touched after each successful tick
    for the docker healthcheck.

    At startup:
    - Registers Drive push webhook channels for all active mappings (non-fatal).
    - Starts a background task to renew channels expiring within 2h (every 12h).
    - Listens for immediate poll triggers from the webhook server via asyncio.Queue.
    """
    # asyncpg requires plain postgresql:// scheme (not postgresql+asyncpg://)
    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    log.info("poll_loop.started", interval=settings.POLL_INTERVAL_SECONDS)

    # Register watch channels for all active mappings (non-fatal if fails)
    try:
        async with pool.acquire() as conn:
            all_mappings = await conn.fetch(
                "SELECT id, team_scope, folder_id, project_scope,"
                " change_token, oauth_credentials_enc"
                " FROM team_drive_mappings"
            )
        for m in all_mappings:
            await register_watch_channel(pool, m)
    except Exception as exc:
        log.warning("poll_loop.watch_registration_failed", error=str(exc))

    # Start channel renewal background task (runs every 12h)
    asyncio.create_task(run_channel_renewal_loop(pool))

    # Get trigger queue from webhook server for immediate poll dispatch
    from app.webhook_server import get_trigger_queue
    trigger_queue = get_trigger_queue()

    while True:
        try:
            async with pool.acquire() as conn:
                mappings = await conn.fetch(
                    "SELECT id, team_scope, folder_id, project_scope,"
                    " change_token, oauth_credentials_enc"
                    " FROM team_drive_mappings"
                )
                for row in mappings:
                    try:
                        await poll_team(conn, row)
                    except Exception as exc:
                        log.error("poll.team_error", team=row["team_scope"], error=str(exc))
            # Update sentinel file for healthcheck after successful tick
            pathlib.Path("/tmp/drive-sync-alive").touch()
            log.info("poll_loop.tick_complete", teams=len(mappings))
        except Exception as exc:
            log.error("poll_loop.error", error=str(exc))

        # Drain webhook triggers — poll triggered teams immediately before sleeping
        triggered_teams: set[str] = set()
        while not trigger_queue.empty():
            try:
                triggered_teams.add(trigger_queue.get_nowait())
            except Exception:
                break
        if triggered_teams:
            try:
                async with pool.acquire() as conn:
                    for team in triggered_teams:
                        rows = await conn.fetch(
                            "SELECT id, team_scope, folder_id, project_scope,"
                            " change_token, oauth_credentials_enc"
                            " FROM team_drive_mappings WHERE team_scope=$1",
                            team,
                        )
                        for row in rows:
                            try:
                                await poll_team(conn, row)
                            except Exception as exc:
                                log.error(
                                    "poll.webhook_trigger_error",
                                    team=team,
                                    error=str(exc),
                                )
            except Exception as exc:
                log.error("poll_loop.trigger_drain_error", error=str(exc))

        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

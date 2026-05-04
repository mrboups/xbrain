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
    """Poll Drive changes for one team and process them.

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
    team_scope = row["team_scope"]
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
            log.warning("poll.token_expired_410", team=team_scope)
            resp = _with_backoff(lambda: service.changes().getStartPageToken().execute())
            new_token = resp["startPageToken"]
            await conn.execute(
                "UPDATE team_drive_mappings SET change_token=$1, updated_at=now() WHERE team_scope=$2",
                new_token,
                team_scope,
            )
            return  # Will full re-sync on next tick
        raise

    new_token = changes_resp.get("newStartPageToken")
    changes = changes_resp.get("changes", [])
    log.info("poll.changes_fetched", team=team_scope, count=len(changes), new_token=bool(new_token))

    # CRITICAL: Persist token BEFORE processing -- idempotent on crash restart (RISK-04)
    if new_token:
        await conn.execute(
            "UPDATE team_drive_mappings SET change_token=$1, updated_at=now() WHERE team_scope=$2",
            new_token,
            team_scope,
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
        )


async def run_poll_loop(database_url: str) -> None:
    """Main polling loop -- runs forever, polls all teams every POLL_INTERVAL_SECONDS.

    Resilient: team-level errors are caught and logged; other teams continue.
    Loop-level errors are caught and logged; loop continues after sleep.
    Sentinel file /tmp/drive-sync-alive is touched after each successful tick
    for the docker healthcheck.
    """
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    log.info("poll_loop.started", interval=settings.POLL_INTERVAL_SECONDS)
    while True:
        try:
            async with pool.acquire() as conn:
                mappings = await conn.fetch(
                    "SELECT team_scope, folder_id, change_token, oauth_credentials_enc "
                    "FROM team_drive_mappings"
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
        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

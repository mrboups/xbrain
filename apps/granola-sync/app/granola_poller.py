"""Granola polling loop — fetches new notes per integrated team AND per user (Phase 8 D2), extracts structured content via Claude (07-05 extractor), posts to memory-api (07-05 memory_client). Auto-triggers meeting-recap agent (Phase 8 D5)."""

import asyncio
import pathlib
import random
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.extractor import extract_from_summary
from app.memory_client import post_agent_invoke, post_ingest

log = structlog.get_logger(__name__)

SENTINEL_PATH = pathlib.Path("/tmp/granola-sync-alive")


def _decrypt_api_key(enc: str) -> str:
    """Decrypt Fernet-encrypted Granola API key. Raises InvalidToken on tamper/wrong key."""
    if not settings.FERNET_KEY:
        raise RuntimeError("FERNET_KEY not set in granola-sync env")
    f = Fernet(settings.FERNET_KEY.encode())
    return f.decrypt(enc.encode()).decode()


async def _fetch_notes(
    api_key: str,
    created_after: datetime | None,
) -> list[dict[str, Any]]:
    """Fetch new notes from Granola API. Returns list of note dicts.

    Handles:
    - Pagination via cursor (hard cap 20 iterations T-07-08-08)
    - 429 rate-limit with exponential backoff (max 5 attempts, max wait 64s)
    - 5xx with exponential backoff (max 5 attempts, max wait 64s)
    - 401/403 (plan insuffisant): raises httpx.HTTPStatusError — caller logs and skips

    Security: Authorization header value is NEVER logged (T-07-08-01).
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    params: dict[str, Any] = {}
    if created_after is not None:
        params["created_after"] = created_after.astimezone(timezone.utc).isoformat()

    notes: list[dict[str, Any]] = []
    cursor: str | None = None

    async with httpx.AsyncClient(timeout=30.0, base_url=settings.GRANOLA_API_BASE) as client:
        for _ in range(20):  # safety cap on pagination (T-07-08-08)
            q = dict(params)
            if cursor:
                q["cursor"] = cursor
            resp = None
            for attempt in range(5):
                try:
                    resp = await client.get("/v1/notes", headers=headers, params=q)
                    if resp.status_code == 429:
                        wait = min(2**attempt + random.random(), 64)
                        log.warning("granola.rate_limited", wait=round(wait, 1))
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (500, 502, 503, 504):
                        wait = min(2**attempt + random.random(), 64)
                        log.warning(
                            "granola.5xx_retry",
                            status=exc.response.status_code,
                            wait=round(wait, 1),
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise
            else:
                raise RuntimeError("Granola API: max retries exceeded")

            if resp is None:
                raise RuntimeError("Granola API: response missing after retry loop")

            data = resp.json()
            notes.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break

    return notes


async def _get_meeting_recap_agent(conn: asyncpg.Connection) -> dict[str, Any] | None:
    """Return meeting-recap agent row if enabled AND auto_trigger, else None.

    Cached per-tick: caller fetches once at top of run_poll_loop iteration.
    Phase 8 D5: granola-sync auto-invokes meeting-recap after each successful ingest
    if the agent has auto_trigger=true and enabled=true.
    """
    row = await conn.fetchrow(
        "SELECT id::text AS id, name, enabled, auto_trigger "
        "FROM agent_definitions WHERE name = 'meeting-recap' LIMIT 1"
    )
    if row is None:
        return None
    if not row["enabled"] or not row["auto_trigger"]:
        return None
    return dict(row)


async def _maybe_invoke_recap(
    recap_agent: dict[str, Any] | None,
    team_scope: str,
    summary: str,
    note_id: str,
) -> None:
    """Fire-and-forget invocation of meeting-recap agent. Fail-soft."""
    if recap_agent is None:
        return
    if not summary or len(summary) < 40:
        return
    try:
        result = await post_agent_invoke(
            agent_id=recap_agent["id"],
            team_scope=team_scope,
            content=summary,
            source_ref=note_id,
        )
        if result:
            log.info(
                "granola.recap_triggered",
                team=team_scope,
                note_id=note_id,
                memory_item_id=result.get("memory_item_id"),
            )
    except Exception as exc:
        log.warning(
            "granola.recap_skipped",
            team=team_scope,
            note_id=note_id,
            error=str(exc),
        )


async def _process_team_integration(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    recap_agent: dict[str, Any] | None,
) -> None:
    """Poll Granola for one team integration and ingest new notes into memory-api."""
    integration_id = row["id"]
    team_scope = row["team_scope"]
    api_key_enc = row["api_key_enc"]
    last_polled_at: datetime | None = row["last_polled_at"]

    try:
        api_key = _decrypt_api_key(api_key_enc)
    except (InvalidToken, RuntimeError) as exc:
        log.error("granola.decrypt_failed", team=team_scope, error=str(exc))
        return

    # CRITICAL: Persist last_polled_at BEFORE processing — idempotent on crash restart (T-07-08-11).
    # On crash, restart picks up from this cursor. Combined with note-level dedup in 07-04 → exactly-once-effective.
    new_polled_at = datetime.now(timezone.utc)
    await conn.execute(
        "UPDATE granola_integrations SET last_polled_at = $1, updated_at = now() WHERE id = $2",
        new_polled_at,
        integration_id,
    )

    try:
        notes = await _fetch_notes(api_key, last_polled_at)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            # Fail-soft: plan Granola insuffisant — log warning only, other teams continue (T-07-08-06)
            log.warning(
                "granola.fetch_unauthorized",
                team=team_scope,
                status=exc.response.status_code,
                hint="Granola Business/Enterprise plan required for API access",
            )
            return
        log.error("granola.fetch_http_error", team=team_scope, status=exc.response.status_code)
        return
    except Exception as exc:
        log.error("granola.fetch_failed", team=team_scope, error=str(exc))
        return

    log.info("granola.poll.fetched", team=team_scope, count=len(notes), since=last_polled_at)

    for note in notes:
        try:
            summary = note.get("summary_text") or note.get("summary_markdown") or ""
            attendees_raw = note.get("attendees") or []
            fallback_participants = [
                {"name": a.get("name"), "email": a.get("email")}
                for a in attendees_raw
                if a.get("name") or a.get("email")
            ]
            extracted = await extract_from_summary(summary, fallback_attendees=fallback_participants)

            payload = {
                "note": {
                    "id": note.get("id"),
                    "title": note.get("title"),
                    "summary_text": note.get("summary_text"),
                    "summary_markdown": note.get("summary_markdown"),
                    "web_url": note.get("web_url"),
                    "created_at": note.get("created_at"),
                    "attendees": fallback_participants,
                },
                "extracted": extracted,
            }
            result = await post_ingest(team_scope, payload)
            if result:
                log.info(
                    "granola.ingested",
                    team=team_scope,
                    note_id=note.get("id"),
                    contacts=result.get("contacts_upserted"),
                    tasks=result.get("tasks_created"),
                )
                # Phase 8 D5 — auto-trigger meeting-recap if agent enabled
                await _maybe_invoke_recap(
                    recap_agent=recap_agent,
                    team_scope=team_scope,
                    summary=summary,
                    note_id=note.get("id") or "",
                )
        except Exception as exc:
            log.error("granola.note_failed", team=team_scope, note_id=note.get("id"), error=str(exc))


async def _process_user_connection(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    recap_agent: dict[str, Any] | None,
) -> None:
    """Phase 8 D2 — Poll Granola for one per-user connection and ingest new notes.

    Mirror of _process_team_integration, but reads from granola_user_connections.
    Same at-most-once-then-exactly-once-effective pattern (UPDATE last_polled_at BEFORE fetch).
    """
    connection_id = row["id"]
    user_id = row["user_id"]
    team_scope = row["team_scope"]
    api_key_enc = row["api_key_enc"]
    last_polled_at: datetime | None = row["last_polled_at"]

    try:
        api_key = _decrypt_api_key(api_key_enc)
    except (InvalidToken, RuntimeError) as exc:
        log.error(
            "granola.user.decrypt_failed",
            user_id=str(user_id),
            team=team_scope,
            error=str(exc),
        )
        return

    # Pitfall 5 RESEARCH.md: UPDATE last_polled_at BEFORE _fetch_notes — at-most-once delivery.
    new_polled_at = datetime.now(timezone.utc)
    await conn.execute(
        "UPDATE granola_user_connections SET last_polled_at = $1, updated_at = now() WHERE id = $2",
        new_polled_at,
        connection_id,
    )

    try:
        notes = await _fetch_notes(api_key, last_polled_at)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            log.warning(
                "granola.user.fetch_unauthorized",
                user_id=str(user_id),
                team=team_scope,
                status=exc.response.status_code,
                hint="User's Granola plan does not allow API access OR key is invalid",
            )
            return
        log.error(
            "granola.user.fetch_http_error",
            user_id=str(user_id),
            team=team_scope,
            status=exc.response.status_code,
        )
        return
    except Exception as exc:
        log.error(
            "granola.user.fetch_failed",
            user_id=str(user_id),
            team=team_scope,
            error=str(exc),
        )
        return

    log.info(
        "granola.user.poll.fetched",
        user_id=str(user_id),
        team=team_scope,
        count=len(notes),
        since=last_polled_at,
    )

    for note in notes:
        try:
            summary = note.get("summary_text") or note.get("summary_markdown") or ""
            attendees_raw = note.get("attendees") or []
            fallback_participants = [
                {"name": a.get("name"), "email": a.get("email")}
                for a in attendees_raw
                if a.get("name") or a.get("email")
            ]
            extracted = await extract_from_summary(summary, fallback_attendees=fallback_participants)

            payload = {
                "note": {
                    "id": note.get("id"),
                    "title": note.get("title"),
                    "summary_text": note.get("summary_text"),
                    "summary_markdown": note.get("summary_markdown"),
                    "web_url": note.get("web_url"),
                    "created_at": note.get("created_at"),
                    "attendees": fallback_participants,
                },
                "extracted": extracted,
            }
            result = await post_ingest(team_scope, payload)
            if result:
                log.info(
                    "granola.user.ingested",
                    user_id=str(user_id),
                    team=team_scope,
                    note_id=note.get("id"),
                    contacts=result.get("contacts_upserted"),
                    tasks=result.get("tasks_created"),
                )
                # Phase 8 D5 — auto-trigger meeting-recap for per-user meetings too
                await _maybe_invoke_recap(
                    recap_agent=recap_agent,
                    team_scope=team_scope,
                    summary=summary,
                    note_id=note.get("id") or "",
                )
        except Exception as exc:
            log.error(
                "granola.user.note_failed",
                user_id=str(user_id),
                team=team_scope,
                note_id=note.get("id"),
                error=str(exc),
            )


async def run_poll_loop(database_url: str) -> None:
    """Main polling loop. Runs until cancelled.

    Two cohabiting loops per tick (Phase 8 D2):
      - Team integrations (granola_integrations) — Phase 7
      - Per-user connections (granola_user_connections) — Phase 8

    Resilient: per-row errors are caught and logged; other rows continue.
    Loop-level errors are caught and logged; loop continues after sleep.
    Sentinel file /tmp/granola-sync-alive is touched after each successful tick.
    """
    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    log.info("poll_loop.started", interval=settings.GRANOLA_POLL_INTERVAL_SECONDS)

    while True:
        try:
            async with pool.acquire() as conn:
                # Lookup meeting-recap agent once per tick (Phase 8 D5)
                recap_agent = await _get_meeting_recap_agent(conn)

                # Loop 1: team integrations (Phase 7, unchanged behavior)
                team_rows = await conn.fetch(
                    "SELECT id, team_scope, api_key_enc, last_polled_at "
                    "FROM granola_integrations ORDER BY last_polled_at NULLS FIRST"
                )
                for row in team_rows:
                    try:
                        await _process_team_integration(conn, row, recap_agent)
                    except Exception as exc:
                        log.error("poll.team_error", team=row["team_scope"], error=str(exc))

                # Loop 2: per-user connections (Phase 8 D2)
                user_rows = await conn.fetch(
                    "SELECT id, user_id, team_scope, api_key_enc, last_polled_at "
                    "FROM granola_user_connections WHERE enabled = true "
                    "ORDER BY last_polled_at NULLS FIRST"
                )
                for row in user_rows:
                    try:
                        await _process_user_connection(conn, row, recap_agent)
                    except Exception as exc:
                        log.error(
                            "poll.user_error",
                            user_id=str(row["user_id"]),
                            team=row["team_scope"],
                            error=str(exc),
                        )
            SENTINEL_PATH.touch()
            log.info(
                "poll_loop.tick_complete",
                teams=len(team_rows),
                users=len(user_rows),
            )
        except Exception as exc:
            log.error("poll_loop.error", error=str(exc))

        await asyncio.sleep(settings.GRANOLA_POLL_INTERVAL_SECONDS)

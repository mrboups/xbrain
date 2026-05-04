"""Drive webhook server — receives Google Drive push notifications.

Exposes FastAPI on port 8200 (separate from the poller loop).
Endpoint: POST /webhook — called by Google when a watched resource changes.

Google sends:
  Headers:
    X-Goog-Channel-ID: <channel_id>
    X-Goog-Channel-Token: <channel_token>    <- must match DB
    X-Goog-Resource-State: sync|change|update|trash|remove|untrash
  Body: empty

Auth: verify X-Goog-Channel-Token against drive_watch_channels.channel_token.
      Reject with 401 if mismatch (T-04-05-SEC-01).

On valid notification: enqueue team_scope into an asyncio.Queue for immediate poll.
The poll loop picks up from the queue and calls poll_team() synchronously.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import asyncpg
import structlog
from fastapi import FastAPI, Header, HTTPException, Response

from app.config import settings

log = structlog.get_logger(__name__)

# Shared queue: webhook handler -> poll loop
_trigger_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)


def get_trigger_queue() -> asyncio.Queue[str]:
    return _trigger_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


webhook_app = FastAPI(title="drive-sync webhook", lifespan=lifespan)


@webhook_app.post("/webhook", status_code=200)
async def drive_webhook(
    x_goog_channel_id: str | None = Header(None, alias="X-Goog-Channel-ID"),
    x_goog_channel_token: str | None = Header(None, alias="X-Goog-Channel-Token"),
    x_goog_resource_state: str | None = Header(None, alias="X-Goog-Resource-State"),
):
    """Receive Google Drive push notification.

    1. Verify X-Goog-Channel-Token against DB.
    2. Ignore 'sync' state (initial handshake from Google — no changes yet).
    3. Lookup team_scope from channel -> enqueue for immediate poll.
    """
    # Initial sync notification from Google — not a real change
    if x_goog_resource_state == "sync":
        log.debug("webhook.sync_handshake", channel_id=x_goog_channel_id)
        return Response(status_code=200)

    if not x_goog_channel_id or not x_goog_channel_token:
        raise HTTPException(400, "Missing required Google headers")

    # Verify token + resolve team_scope from DB
    pg_url = (
        settings.DATABASE_URL
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+asyncpg://", "postgresql://")
    )
    try:
        conn = await asyncpg.connect(pg_url)
        try:
            row = await conn.fetchrow(
                """
                SELECT dwc.channel_token, tdm.team_scope
                FROM drive_watch_channels dwc
                JOIN team_drive_mappings tdm ON dwc.mapping_id = tdm.id
                WHERE dwc.channel_id = $1
                """,
                x_goog_channel_id,
            )
        finally:
            await conn.close()
    except Exception as exc:
        log.error("webhook.db_error", error=str(exc))
        raise HTTPException(500, "Internal error") from exc

    if row is None:
        log.warning("webhook.unknown_channel", channel_id=x_goog_channel_id)
        raise HTTPException(404, "Unknown channel")

    if row["channel_token"] != x_goog_channel_token:
        log.warning("webhook.invalid_token", channel_id=x_goog_channel_id)
        raise HTTPException(401, "Invalid channel token")

    team_scope = row["team_scope"]
    log.info("webhook.change_received", team=team_scope, state=x_goog_resource_state)

    # Enqueue team for immediate poll (non-blocking — queue is maxsize=64)
    try:
        _trigger_queue.put_nowait(team_scope)
    except asyncio.QueueFull:
        log.warning("webhook.queue_full", team=team_scope)

    return Response(status_code=200)


@webhook_app.get("/healthz")
async def healthz():
    return {"status": "ok"}

"""/v1/drive/webhook — Google Drive push notification receiver.

Google Drive calls this endpoint when a watched resource changes.
memory-api acts as the public-facing webhook receiver; it verifies the
notification, then enqueues the team_scope for immediate polling by
forwarding a trigger signal to drive-sync via an internal HTTP call.

Google sends (headers only — body is empty):
  X-Goog-Channel-ID: <channel_id>      — identifies the watch subscription
  X-Goog-Channel-Token: <token>        — must match drive_watch_channels.channel_token
  X-Goog-Resource-State: sync|change   — "sync" = initial handshake, "change" = real event
  X-Goog-Resource-ID: <resource_id>    — Drive resource (folder) ID

Security:
  T-04-05-SEC-01 — verify X-Goog-Channel-Token against DB before processing.
  Reject with 401 if mismatch — prevents spoofed notifications from triggering polls.

nginx route: /v1/drive-webhook → drive-sync:8200/webhook (public-facing)
             /v1/drive/webhook → memory-api:8001 (this endpoint — internal + public-facing)

Both paths are valid: Google can call either. This endpoint is the canonical
public path advertised in DRIVE_WEBHOOK_PUBLIC_URL when drive-sync cannot
be reached directly.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Header, HTTPException, Response

from app.deps import get_session
from fastapi import Depends
import sqlalchemy as sa

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/drive/webhook", status_code=200)
async def drive_webhook(
    x_goog_channel_id: str | None = Header(None, alias="X-Goog-Channel-ID"),
    x_goog_channel_token: str | None = Header(None, alias="X-Goog-Channel-Token"),
    x_goog_resource_state: str | None = Header(None, alias="X-Goog-Resource-State"),
    session=Depends(get_session),
):
    """Receive Google Drive push notification.

    1. Handle 'sync' state (initial handshake) — return 200, no side effects.
    2. Verify X-Goog-Channel-Token against drive_watch_channels.channel_token.
    3. Log the trigger — drive-sync's polling loop will pick up changes on next cycle.
       (drive-sync polls every 5min as fallback; immediate trigger via drive-sync:8200
        is the fast-path but memory-api also accepts notifications as the public URL.)
    """
    # Initial sync notification from Google — not a real change event
    if x_goog_resource_state == "sync":
        log.debug("drive_webhook.sync_handshake", channel_id=x_goog_channel_id)
        return Response(status_code=200)

    if not x_goog_channel_id or not x_goog_channel_token:
        raise HTTPException(400, "Missing required Google Drive notification headers")

    # Verify token + resolve team_scope from DB
    row = (
        await session.execute(
            sa.text(
                """
                SELECT dwc.channel_token, tdm.team_scope
                FROM drive_watch_channels dwc
                JOIN team_drive_mappings tdm ON dwc.mapping_id = tdm.id
                WHERE dwc.channel_id = :channel_id
                """
            ),
            {"channel_id": x_goog_channel_id},
        )
    ).fetchone()

    if row is None:
        log.warning("drive_webhook.unknown_channel", channel_id=x_goog_channel_id)
        raise HTTPException(404, "Unknown channel")

    if row.channel_token != x_goog_channel_token:
        log.warning("drive_webhook.invalid_token", channel_id=x_goog_channel_id)
        raise HTTPException(401, "Invalid channel token")

    team_scope = row.team_scope
    log.info(
        "drive_webhook.change_received",
        team=team_scope,
        state=x_goog_resource_state,
        channel_id=x_goog_channel_id,
    )

    # Notification acknowledged — drive-sync will pick up on next poll cycle (5min max).
    # For immediate sync: drive-sync also runs a webhook server on port 8200 that directly
    # triggers poll_team() when it receives its own notification.
    return Response(status_code=200)

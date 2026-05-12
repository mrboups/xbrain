"""Centrifugo client — issues HS256 JWTs for client connections and posts
server-side publish/presence/history calls via the Centrifugo HTTP API.

Quick task 260512-tcr (team chat realtime). Channels:
  team:<team_id>  — broadcast to all team members
  user:<user_sub> — direct notifications (Phase 2)

Token contract (Centrifugo v6 client.token.hmac_secret_key):
  alg: HS256
  exp: now + CENTRIFUGO_CLIENT_TOKEN_TTL_S
  sub: <user.source_user_id>  (Centrifugo connection identity)
  info: { user_id: str, display_name: str|None, email: str|None }
  channels: [ "team:<id>", ... ]  (allowed subscribe set; presence ties to sub)

Server-side calls are authenticated via the X-API-Key header set to
settings.CENTRIFUGO_API_KEY (must match Centrifugo http_api.key).
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import httpx
import structlog
from authlib.jose import jwt as jose_jwt

from app.config import settings

log = structlog.get_logger(__name__)


def issue_client_token(
    *,
    user_sub: str,
    user_id: UUID | str,
    display_name: str | None,
    email: str | None,
    channels: list[str],
    ttl_s: int | None = None,
) -> dict[str, Any]:
    """Return {token, ws_url, channels, expires_at} for the extension/PWA.

    The token is HS256-signed with CENTRIFUGO_TOKEN_HMAC_SECRET. The `channels`
    claim restricts what the client may subscribe to (Centrifugo enforces this
    server-side — even if the client tries to subscribe to another channel,
    Centrifugo will reject).

    Raises RuntimeError when the HMAC secret isn't configured (deployment misconfig).
    """
    if not settings.CENTRIFUGO_TOKEN_HMAC_SECRET:
        raise RuntimeError(
            "CENTRIFUGO_TOKEN_HMAC_SECRET not configured — cannot issue Centrifugo tokens"
        )
    ttl = ttl_s if ttl_s is not None else settings.CENTRIFUGO_CLIENT_TOKEN_TTL_S
    now = int(time.time())
    payload = {
        "sub": user_sub,
        "exp": now + ttl,
        "iat": now,
        "info": {
            "user_id": str(user_id),
            "display_name": display_name,
            "email": email,
        },
        "channels": channels,
    }
    token = jose_jwt.encode(
        {"alg": "HS256"},
        payload,
        settings.CENTRIFUGO_TOKEN_HMAC_SECRET,
    )
    if isinstance(token, bytes):
        token = token.decode()
    return {
        "token": token,
        "ws_url": settings.CENTRIFUGO_WS_URL_PUBLIC,
        "channels": channels,
        "expires_at": now + ttl,
    }


async def publish(channel: str, data: dict[str, Any]) -> bool:
    """Server-side publish to a Centrifugo channel. Returns True on 2xx.

    Fire-and-forget pattern at call site: callers should NOT block the
    request path on this. Returns False on any failure but never raises —
    a failed publish must not break the persistence write that came before.
    """
    if not settings.CENTRIFUGO_API_KEY:
        log.warning("centrifugo.publish.skipped", reason="no_api_key", channel=channel)
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{settings.CENTRIFUGO_HTTP_URL_INTERNAL}/api/publish",
                headers={
                    "X-API-Key": settings.CENTRIFUGO_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"channel": channel, "data": data},
            )
    except Exception as e:  # noqa: BLE001
        log.warning("centrifugo.publish.error", channel=channel, err=str(e))
        return False
    if 200 <= r.status_code < 300:
        return True
    log.warning(
        "centrifugo.publish.failed",
        channel=channel,
        status=r.status_code,
        body=r.text[:200],
    )
    return False


async def presence(channel: str) -> dict[str, Any] | None:
    """Return Centrifugo presence map for a channel, or None on failure.

    Shape: {"clients": {<client_id>: {user, info, ...}}, "num_clients": N}.
    """
    if not settings.CENTRIFUGO_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{settings.CENTRIFUGO_HTTP_URL_INTERNAL}/api/presence",
                headers={
                    "X-API-Key": settings.CENTRIFUGO_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"channel": channel},
            )
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    return r.json().get("result")


async def history(channel: str, limit: int = 50) -> list[dict[str, Any]] | None:
    """Return Centrifugo channel history (last `limit` publications).

    Useful for clients that reconnect and want to replay missed publications
    without round-tripping to Postgres. We DO still ship every message to
    Postgres so this is best-effort / latency optimization only.
    """
    if not settings.CENTRIFUGO_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{settings.CENTRIFUGO_HTTP_URL_INTERNAL}/api/history",
                headers={
                    "X-API-Key": settings.CENTRIFUGO_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"channel": channel, "limit": limit},
            )
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    result = r.json().get("result") or {}
    pubs = result.get("publications") or []
    return pubs

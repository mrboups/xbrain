"""Client for memory-api endpoints called BY session-bridge (not by users).

Signs a short-lived bridge JWT (HS256, BRIDGE_SHARED_SECRET) with an
`acting_user_sub` claim so memory-api knows which user the upsert is for.
Pattern matches apps/granola-sync/app/memory_client.py.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from authlib.jose import jwt as jose_jwt

from app.config import settings

log = structlog.get_logger(__name__)


def _make_bridge_jwt(acting_user_sub: str) -> str:
    """Sign a service-to-service JWT with the user the bridge is acting on behalf of."""
    now = int(time.time())
    payload = {
        "sub": "session-bridge",
        "iss": "session-bridge",
        "scope": "bridge",
        "acting_user_sub": acting_user_sub,
        "iat": now,
        "exp": now + 3600,
    }
    token = jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    )
    if isinstance(token, bytes):
        token = token.decode()
    return token


async def upsert_external_session(
    user_sub: str,
    provider: str,
    extension_id: str | None,
    metadata: dict[str, Any] | None,
) -> bool:
    """POST /v1/me/external-sessions to upsert this user's extension session.

    Fire-and-forget: returns True on 2xx, False on any failure (including 5xx,
    timeouts, network errors, missing secret). **Never raises** — the caller
    schedules it with asyncio.create_task() and must not block on it.
    """
    if not settings.BRIDGE_SHARED_SECRET:
        log.warning("register.upsert.skipped", reason="no_bridge_secret", sub=user_sub)
        return False

    try:
        token = _make_bridge_jwt(user_sub)
    except Exception as e:  # noqa: BLE001 — never let JWT signing crash the WS
        log.warning("register.upsert.jwt_error", sub=user_sub, err=str(e))
        return False

    body = {
        "provider": provider,
        "extension_id": extension_id,
        "metadata": metadata or {},
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{settings.MEMORY_API_URL}/v1/me/external-sessions",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except Exception as e:  # noqa: BLE001
        log.warning("register.upsert.error", sub=user_sub, provider=provider, err=str(e))
        return False

    if 200 <= r.status_code < 300:
        log.info("register.upsert.ok", sub=user_sub, provider=provider, status=r.status_code)
        return True

    log.warning(
        "register.upsert.failed",
        sub=user_sub,
        provider=provider,
        status=r.status_code,
        body=r.text[:200],
    )
    return False

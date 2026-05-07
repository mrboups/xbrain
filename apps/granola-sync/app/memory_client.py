"""HTTP client posting structured Granola payloads to memory-api /v1/integrations/granola/ingest."""

import time
from typing import Any

import httpx
import structlog
from authlib.jose import jwt as jose_jwt

from app.config import settings

log = structlog.get_logger(__name__)


def _make_bridge_jwt() -> str:
    """Generate a short-lived bridge JWT for service-to-service auth."""
    payload = {
        "sub": "granola-sync",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    ).decode()


async def post_ingest(team_scope: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST a structured Granola payload to memory-api. Fail-soft (returns None on error)."""
    token = _make_bridge_jwt()
    url = f"{settings.MEMORY_API_URL}/v1/integrations/granola/ingest"
    body = {
        "team_scope": team_scope,
        **payload,  # contains "note" + "extracted"
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 201:
            log.error(
                "ingest.non_201",
                team_scope=team_scope,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return None
        return resp.json()
    except Exception as exc:
        log.error("ingest.exception", team_scope=team_scope, error=str(exc))
        return None

"""xbt_ token validation against memory-api /v1/me with a 60s TTL cache.

Pattern mirrors apps/mcp-brain/app/memory_client.get_me, with an in-process cache
so the WS recv loop / chat handler don't hit memory-api on every frame.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings

# token -> (expires_at_monotonic, me_json)
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL: float = settings.TOKEN_TTL_S


def _now() -> float:
    """Indirection so tests can monkeypatch time."""
    return time.monotonic()


async def validate_xbt_token(token: str) -> dict[str, Any]:
    """Resolve an `xbt_` Bearer token to the memory-api user object.

    Raises PermissionError on HTTP != 200, on a non-`user_api_token` kind, or on
    network errors. Caches successful lookups for `TOKEN_TTL_S` seconds.
    """
    if not token:
        raise PermissionError("empty token")

    now = _now()
    cached = _CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{settings.MEMORY_API_URL}/v1/me",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as e:
        raise PermissionError(f"memory-api unreachable: {e}") from e

    if r.status_code != 200:
        raise PermissionError(f"token rejected: {r.status_code}")

    me = r.json()
    if me.get("kind") != "user_api_token":
        raise PermissionError("only xbt_ tokens (kind=user_api_token) are allowed")

    _CACHE[token] = (now + _TTL, me)
    return me


def _reset_cache_for_tests() -> None:
    """Test helper — clears the module-level cache between tests."""
    _CACHE.clear()

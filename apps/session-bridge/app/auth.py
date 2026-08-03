"""Token validation for the two principals the bridge accepts.

  - `xbt_` user tokens, resolved against memory-api /v1/me with a 60s TTL cache.
    Pattern mirrors apps/mcp-brain/app/memory_client.get_me, so the WS recv loop
    and chat handler don't hit memory-api on every frame.
  - bridge JWTs signed with BRIDGE_SHARED_SECRET, carrying the user a backend
    service is acting for.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from authlib.jose import jwt as jose_jwt

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


def resolve_bridge_jwt_sub(token: str) -> str | None:
    """Decode a bridge JWT and return its `acting_user_sub` claim.

    Returns None when the token isn't a valid bridge JWT — wrong signature,
    wrong scope, expired, missing claim, or simply not a JWT shape. Every
    caller treats None as "this principal is not a backend service", so a
    failure to decode can never widen access.

    Lives here rather than in routes_chat because two routes now need it: the
    chat handler that acts on a user's socket, and the status probe that asks
    whether the user has one.
    """
    if not settings.BRIDGE_SHARED_SECRET:
        return None
    # Cheap pre-check: a JWT has exactly two dots.
    if token.count(".") != 2:
        return None
    try:
        claims = jose_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
        claims.validate()
    except Exception:  # noqa: BLE001 — any decode/validate failure → not a bridge JWT
        return None
    if claims.get("scope") != "bridge":
        return None
    # Defense in depth: explicit exp check (validate() already does this but
    # belt-and-braces given the security-sensitive routing decision). A token
    # with no exp at all is refused — an unbounded bridge credential is not a
    # credential, and `validate()` has nothing to check when the claim is absent.
    exp = claims.get("exp")
    if exp is None or int(exp) < int(time.time()):
        return None
    acting_sub = claims.get("acting_user_sub")
    if not acting_sub or not isinstance(acting_sub, str):
        return None
    return acting_sub


def _reset_cache_for_tests() -> None:
    """Test helper — clears the module-level cache between tests."""
    _CACHE.clear()

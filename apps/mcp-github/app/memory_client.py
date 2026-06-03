"""Async client for the small memory-api calls mcp-github needs to authenticate.

Mirrors apps/mcp-brain/app/memory_client.py (trimmed to what github tools need):
- get_me(token): resolve an xbt_ token to its team_scope (TOKEN PATH).
- resolve_team_scope_internal(bridge_jwt, sub): resolve a user's primary team
  slug via the memory-api internal endpoint (EMAIL PATH).
"""
import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)
_BASE = settings.MEMORY_API_URL.rstrip("/")


async def get_me(token: str) -> dict:
    """Resolve an xbt_ token to user + api_token_team_scope via GET /v1/me."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_BASE}/v1/me", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()


async def resolve_team_scope_internal(bridge_jwt: str, sub: str) -> str | None:
    """Resolve a user's primary team slug via memory-api internal endpoint.

    Args:
        bridge_jwt: A valid bridge-scope JWT minted by mint_bridge_jwt.
        sub:        Subject identifier (e.g. "email:user@example.com" or bare email).

    Returns:
        Team slug string, or None if the user has no team / is unknown.
    """
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{_BASE}/v1/internal/resolve-team-scope",
            params={"sub": sub},
            headers={"Authorization": f"Bearer {bridge_jwt}"},
        )
        r.raise_for_status()
        return r.json().get("team_scope")

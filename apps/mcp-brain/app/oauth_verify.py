"""Introspection-based verification for Claude.ai Custom-Connector tokens.

mcp-brain is the OAuth Protected Resource. An ``oat_`` access token presented by
Claude.ai is opaque to mcp-brain, so it is validated by introspecting against
memory-api (the Authorization Server) over the internal Docker network. The
shared ``BRIDGE_SHARED_SECRET`` is presented as the ``X-Internal-Secret`` header
so memory-api's /oauth/introspect (which is otherwise un-credentialed) accepts
the call.

RFC 8707 audience binding: the introspected ``aud`` (resource) MUST equal this
server's configured ``OAUTH_RESOURCE_URL`` (normalized, no trailing slash). A
token minted for a different resource is rejected even if it is otherwise live.
"""
from __future__ import annotations

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def _normalize_resource(url: str) -> str:
    return url.rstrip("/") if url else ""


async def introspect(raw_token: str) -> dict:
    """Validate an oat_ access token via memory-api introspection.

    Returns ``{"sub", "team_scope", "source", "resource"}`` for an active token
    whose audience matches OAUTH_RESOURCE_URL. Raises ``ValueError`` for any
    inactive token, an unreachable AS, or an audience mismatch (fail-closed).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                settings.MEMORY_API_OAUTH_INTROSPECT_URL,
                json={"token": raw_token},
                headers={"X-Internal-Secret": settings.BRIDGE_SHARED_SECRET},
            )
    except httpx.RequestError as exc:
        raise ValueError("introspection endpoint unreachable") from exc

    if r.status_code != 200:
        raise ValueError(f"introspection failed: {r.status_code}")
    data = r.json()
    if not data.get("active"):
        raise ValueError("OAuth token inactive")

    # Audience / resource check (RFC 8707) — mismatch is a hard reject.
    expected = _normalize_resource(settings.OAUTH_RESOURCE_URL)
    got = _normalize_resource(data.get("aud") or "")
    if expected and got != expected:
        log.warning("mcp_brain.oauth.audience_mismatch", expected=expected, got=got)
        raise ValueError("OAuth token audience does not match this resource")

    team_scope = data.get("team_scope")
    if not team_scope:
        raise ValueError("OAuth token has no bound team_scope")

    return {
        "sub": data.get("sub"),
        "team_scope": team_scope,
        "source": data.get("source"),
        "resource": got,
    }

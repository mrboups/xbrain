"""Fire-and-forget audit log to memory-api.

Posts to POST /v1/audit-log using a bridge JWT minted by mcp-gateway.
Errors are swallowed (best-effort) — the tool call response is never blocked
by audit failures.
"""
from __future__ import annotations

import time

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def _make_bridge_token() -> str:
    """Mint a short-lived bridge JWT signed with BRIDGE_SHARED_SECRET."""
    from authlib.jose import jwt as jose_jwt

    payload = {
        "sub": "mcp-gateway",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token_bytes = jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM}, payload, settings.BRIDGE_SHARED_SECRET
    )
    # authlib returns bytes for HMAC algorithms
    if isinstance(token_bytes, bytes):
        return token_bytes.decode()
    return token_bytes


async def log_tool_call(
    tool_name: str,
    user_sub: str,
    team_scope: str,
    result_summary: str,
    method: str | None = None,
) -> None:
    """POST to memory-api /v1/audit-log — best-effort, never blocks the response.

    action format: mcp.tool_call.{tool_name}
    When `method` is provided (JSON-RPC params.name or top-level tool_method),
    action becomes mcp.tool_call.{tool_name}.{method}.

    This is the key mechanism for INT-04: Drive write-back audit actions
    (e.g., mcp.tool_call.write_drive_file) are queryable by action prefix.
    """
    action = f"mcp.tool_call.{tool_name}" if not method else f"mcp.tool_call.{tool_name}.{method}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.MEMORY_API_URL}/v1/audit-log",
                json={
                    "action": action,
                    "team_scope": team_scope,
                    "target_id": tool_name,
                    "payload": {
                        "user_sub": user_sub,
                        "result_summary": result_summary[:500],
                    },
                },
                headers={
                    "Authorization": f"Bearer {_make_bridge_token()}",
                    "X-Team-Scope": team_scope,
                },
            )
    except Exception as exc:
        log.warning("audit.failed", tool=tool_name, error=str(exc))

"""MCP Aggregate Server — exposes all registered gateway tools as a single MCP server.

Runs as a separate process (multiprocessing) inside the mcp-gateway container.
Port 8081 — distinct from the FastAPI gateway (port 8080).

Architecture note: FastMCP cannot be mounted inside a parent FastAPI app (issue #1367).
Running as a separate process avoids the asyncio event-loop conflict.

Tool discovery: polls GET /tools on the gateway (port 8080) at startup with retry.
Tool dispatch: for each tool call, POST /tools/{name}/call on the gateway.
Auth injection: uses BRIDGE_SHARED_SECRET to mint a bridge JWT for each call.
team_scope: defaults to "default" — LibreChat sends X-Team-Scope header which
the aggregate server passes through to the gateway.

Security (T-04-02-SEC-01): team_scope is embedded in the signed bridge JWT by the
aggregate server. The gateway extracts team_scope from the JWT payload, not from the
forwarded header, preventing header spoofing.

Security (T-04-02-SEC-03): tool names are validated against ^[a-z0-9_-]+$ before
constructing the dispatch URL, preventing path traversal.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

GATEWAY_URL = os.environ.get("GATEWAY_INTERNAL_URL", "http://127.0.0.1:8080")
BRIDGE_SHARED_SECRET = os.environ.get("BRIDGE_SHARED_SECRET", "")
HOST = os.environ.get("FASTMCP_AGGREGATE_HOST", "0.0.0.0")
PORT = int(os.environ.get("FASTMCP_AGGREGATE_PORT", "8081"))

# Strict allowlist for tool names — prevents SSRF path traversal (T-04-02-SEC-03)
_TOOL_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _mint_bridge_jwt(team_scope: str = "default") -> str:
    """Mint a short-lived HS256 bridge JWT for gateway calls.

    Uses authlib.jose directly — same library as app.auth.verify_bridge_jwt.
    """
    from authlib.jose import jwt as authlib_jwt

    now = int(time.time())
    payload = {
        "iss": "mcp-aggregate",
        "sub": "mcp-aggregate",
        "scope": "bridge",
        "team_scope": team_scope,
        "iat": now,
        "exp": now + 300,  # 5 minutes — aggregate calls are fast
    }
    secret = BRIDGE_SHARED_SECRET
    token = authlib_jwt.encode({"alg": "HS256"}, payload, secret)
    return token.decode("ascii") if isinstance(token, bytes) else token


def _discover_tools_with_retry() -> list[dict]:
    """Synchronously fetch tool list from gateway with exponential backoff retry.

    Retry schedule: immediate attempt, then 2s, 4s, 8s delays between attempts.
    Returns empty list after all attempts fail — aggregate starts with 0 tools
    rather than crashing (graceful degradation).
    """
    retry_delays = [0, 2, 4, 8]
    for attempt, delay in enumerate(retry_delays):
        if delay > 0:
            log.info("aggregate.discover_retry", attempt=attempt, delay_secs=delay)
            time.sleep(delay)
        try:
            r = httpx.get(f"{GATEWAY_URL}/tools", timeout=5.0)
            r.raise_for_status()
            tools = r.json()
            log.info("aggregate.discover_success", count=len(tools), attempt=attempt)
            return tools
        except Exception as exc:
            log.warning("aggregate.discover_error", attempt=attempt, error=str(exc))
    log.error("aggregate.discover_failed_all_attempts", gateway=GATEWAY_URL)
    return []


def _build_proxy_caller(tool_name: str) -> Any:
    """Return an async callable that dispatches to gateway POST /tools/{name}/call.

    The tool_name is captured in closure — each caller proxies to its own sidecar.
    team_scope is always embedded in the bridge JWT (T-04-02-SEC-01).
    """
    # Validate tool name before building URL — prevents SSRF (T-04-02-SEC-03)
    assert _TOOL_NAME_RE.match(tool_name), f"Invalid tool name: {tool_name!r}"

    async def _proxy(**kwargs: Any) -> str:
        jwt = _mint_bridge_jwt(team_scope="default")
        payload = {
            "name": tool_name,
            "arguments": kwargs,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/tools/{tool_name}/call",
                json=payload,
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "X-Team-Scope": "default",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        # Extract text content from MCP CallToolResult format
        contents = data.get("content", [])
        if contents:
            return "\n".join(c.get("text", str(c)) for c in contents if c)
        return str(data)

    # FastMCP uses __name__ as the tool name when no explicit name= is provided.
    # We set it here so the registered tool appears with the correct name.
    _proxy.__name__ = tool_name
    return _proxy


def build_mcp_server():
    """Build a FastMCP server with proxy tools discovered from gateway.

    Returns the FastMCP instance — call .run() to start serving.
    Import is deferred to subprocess context to avoid event-loop conflicts.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("xbrain-aggregate", host=HOST, port=PORT)

    tools = _discover_tools_with_retry()
    registered = 0
    for tool_info in tools:
        name = tool_info.get("tool_name", "")
        desc = tool_info.get("description") or f"Proxy to MCP sidecar: {name}"
        if not name:
            continue
        if not _TOOL_NAME_RE.match(name):
            log.warning("aggregate.tool_skipped_invalid_name", tool=name)
            continue
        caller = _build_proxy_caller(name)
        caller.__doc__ = desc
        # Register with explicit name= — reliable regardless of __name__ attribute
        mcp.tool(name=name)(caller)
        log.info("aggregate.tool_registered", tool=name)
        registered += 1

    log.info("aggregate.server_ready", tools_registered=registered, host=HOST, port=PORT)
    return mcp


def run_aggregate_server() -> None:
    """Entry point for the subprocess — blocks until process killed.

    This function runs in its own OS process with its own event loop.
    That is why we can safely call mcp.run() (which calls asyncio.run internally)
    without conflicting with the parent FastAPI process event loop.
    """
    # Re-configure structlog for subprocess — parent's handlers do not inherit
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    log.info("aggregate.starting", host=HOST, port=PORT, gateway=GATEWAY_URL)
    try:
        mcp = build_mcp_server()
        mcp.run(transport="streamable-http")
    except Exception as exc:
        log.error("aggregate.fatal_error", error=str(exc))
        raise


if __name__ == "__main__":
    run_aggregate_server()

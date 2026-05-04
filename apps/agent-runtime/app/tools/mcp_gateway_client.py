"""MCP Gateway client for agent-runtime — wraps gateway tools as LangGraph StructuredTools.

Discovers tools from GET /tools on the MCP gateway.
For each tool, generates a LangGraph-compatible StructuredTool that:
  - Injects a bridge JWT and X-Team-Scope header per call.
  - Dispatches POST /tools/{name}/call on the gateway.
  - Returns the text content of the MCP CallToolResult.

Usage in LangGraph graphs:
    from app.tools.mcp_gateway_client import get_mcp_tools
    tools = get_mcp_tools(team_scope="acme")  # returns [] gracefully if gateway down

Refresh: tool list is cached for MCP_TOOL_CACHE_TTL_SECS (default 300s).
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx
import structlog
from langchain_core.tools import StructuredTool

log = structlog.get_logger(__name__)

MCP_GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://mcp-gateway:8080")
MCP_TOOL_CACHE_TTL_SECS = int(os.environ.get("MCP_TOOL_CACHE_TTL_SECS", "300"))

# Simple in-memory cache: {team_scope: (timestamp, list[StructuredTool])}
_tool_cache: dict[str, tuple[float, list[StructuredTool]]] = {}

_TOOL_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _mint_bridge_jwt(sub: str, team_scope: str) -> str:
    """Mint a bridge JWT for gateway authentication."""
    from app.auth import make_bridge_jwt  # lazy import — avoids circular import at module load
    return make_bridge_jwt(sub=sub, team_scope=team_scope, ttl_seconds=300)


def _discover_tools_sync(team_scope: str) -> list[dict]:
    """Synchronously discover tools from gateway GET /tools. Returns [] on error."""
    jwt = _mint_bridge_jwt("agent-runtime", team_scope)
    try:
        r = httpx.get(
            f"{MCP_GATEWAY_URL}/tools",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("mcp_gateway_client.discover_failed", error=str(exc), gateway=MCP_GATEWAY_URL)
        return []


def _make_tool_callable(tool_name: str, team_scope: str):
    """Return a sync callable that dispatches to gateway POST /tools/{name}/call."""
    # Validate tool_name to prevent SSRF path traversal
    if not _TOOL_NAME_RE.match(tool_name):
        raise ValueError(f"Invalid tool_name format: {tool_name!r}")

    def _call(**kwargs: Any) -> str:
        jwt_token = _mint_bridge_jwt("agent-runtime", team_scope)
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool_name, "arguments": kwargs},
        }
        try:
            r = httpx.post(
                f"{MCP_GATEWAY_URL}/tools/{tool_name}/call",
                json=payload,
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "X-Team-Scope": team_scope,
                    "Content-Type": "application/json",
                },
                timeout=60.0,
            )
            r.raise_for_status()
            data = r.json()
            contents = data.get("content", [])
            return "\n".join(c.get("text", str(c)) for c in contents if c) or str(data)
        except Exception as exc:
            log.error("mcp_gateway_client.call_failed", tool=tool_name, error=str(exc))
            return f"Error calling tool '{tool_name}': {exc}"

    _call.__name__ = tool_name
    return _call


def get_mcp_tools(team_scope: str = "default") -> list[StructuredTool]:
    """Return LangGraph-compatible StructuredTools from the MCP gateway.

    Caches results for MCP_TOOL_CACHE_TTL_SECS to avoid gateway polling on every graph invocation.
    Returns [] gracefully if gateway is unreachable (Phase 2 graphs remain functional).

    Args:
        team_scope: The team scope to embed in bridge JWTs for all tool calls.

    Returns:
        List of StructuredTool instances. Empty list if gateway is down or no tools registered.
    """
    now = time.monotonic()
    cached = _tool_cache.get(team_scope)
    if cached is not None:
        ts, tools = cached
        if now - ts < MCP_TOOL_CACHE_TTL_SECS:
            return tools

    raw_tools = _discover_tools_sync(team_scope)
    structured: list[StructuredTool] = []

    for tool_info in raw_tools:
        name = tool_info.get("tool_name", "")
        desc = tool_info.get("description", f"MCP tool: {name}")
        if not name or not _TOOL_NAME_RE.match(name):
            log.warning("mcp_gateway_client.invalid_tool_name", name=name)
            continue
        try:
            fn = _make_tool_callable(name, team_scope)
            tool = StructuredTool.from_function(
                func=fn,
                name=name,
                description=desc,
                # args_schema=None — let LangChain infer from kwargs (generic dict)
            )
            structured.append(tool)
        except Exception as exc:
            log.warning("mcp_gateway_client.tool_build_failed", tool=name, error=str(exc))

    _tool_cache[team_scope] = (now, structured)
    log.info("mcp_gateway_client.tools_loaded", count=len(structured), team_scope=team_scope)
    return structured


def invalidate_cache(team_scope: str | None = None) -> None:
    """Force refresh on next call. Pass team_scope=None to invalidate all."""
    if team_scope is None:
        _tool_cache.clear()
    else:
        _tool_cache.pop(team_scope, None)

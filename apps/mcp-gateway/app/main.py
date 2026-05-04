"""MCP Gateway — routes tool calls to registered sidecars.

NOT a FastMCP host — plain FastAPI proxy that speaks the MCP wire protocol.
Each tool sidecar is a standalone FastMCP service (streamable-http transport).
Gateway is responsible for: auth, team_scope injection, registry lookup, audit logging.

Architecture note: FastMCP cannot be mounted inside a parent FastAPI app due to
RuntimeError: Task group is not initialized (github.com/modelcontextprotocol/python-sdk/issues/1367).
This gateway is deliberately a plain HTTP client-proxy, not a FastMCP host.
"""
from __future__ import annotations

import asyncio
import json as _json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.audit import log_tool_call
from app.auth import verify_bridge_jwt, verify_google_id_token
from app.config import settings
from app.registry import close_registry, get_tool, init_registry, list_tools, register_tool

log = structlog.get_logger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_registry(settings.DATABASE_URL)
    log.info("mcp_gateway.started")
    yield
    await close_registry()


app = FastAPI(title="xbrain MCP Gateway", version="0.1.0", lifespan=lifespan)


# ─── Auth ────────────────────────────────────────────────────────────────────


async def _get_principal(request: Request) -> dict[str, Any]:
    """Verify Google OIDC or bridge JWT. Return {sub, team_scope}.

    team_scope is extracted from the JWT payload first (bridge JWTs may carry it),
    then falls back to the X-Team-Scope request header, then 'default'.
    The client CANNOT override the team_scope that is embedded in a valid JWT.
    (T-03-06-01: prevents X-Team-Scope header spoofing)
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth.removeprefix("Bearer ")

    # Try bridge JWT first (service-to-service)
    try:
        claims = verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)
        # Bridge JWTs may embed team_scope; if not, fall back to header
        team_scope = claims.get("team_scope") or request.headers.get("X-Team-Scope", "default")
        return {"sub": claims.get("sub", "service"), "team_scope": team_scope}
    except Exception:
        pass

    # Fall back to Google OIDC
    try:
        claims = await verify_google_id_token(token, settings.GOOGLE_CLIENT_ID)
        # Google OIDC tokens do not carry team_scope — use the request header
        team_scope = request.headers.get("X-Team-Scope", "default")
        return {"sub": claims["sub"], "team_scope": team_scope}
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


# ─── Routes ──────────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz():
    """Health probe — always returns 200 if the service is up."""
    return {"status": "ok"}


@app.get("/tools")
async def get_tools():
    """List all registered MCP tool sidecars (MCP-01, MCP-07).

    No auth required — tool discovery is public within the Docker network.
    Returns list of {tool_name, sidecar_url, description}.
    """
    return await list_tools()


class RegisterBody(BaseModel):
    tool_name: str
    sidecar_url: str
    description: str = ""


@app.post("/admin/register", status_code=201)
async def admin_register(
    body: RegisterBody,
    principal: dict = Depends(_get_principal),
):
    """Register a new MCP sidecar URL without infrastructure restart (MCP-07).

    Uses DB upsert — calling this with an existing tool_name updates the URL.
    Auth required: any valid JWT (bridge or Google OIDC).
    Phase 4 will add explicit admin-role check (T-03-06-02).
    """
    result = await register_tool(body.tool_name, body.sidecar_url, body.description)
    log.info(
        "registry.tool_registered",
        tool=body.tool_name,
        url=body.sidecar_url,
        by=principal["sub"],
    )
    return result


@app.post("/tools/{tool_name}/call")
async def call_tool(
    tool_name: str,
    request: Request,
    principal: dict = Depends(_get_principal),
):
    """Forward a tool call to the registered sidecar (MCP-01, MCP-02, MCP-03).

    Injects X-Team-Scope and X-User-Sub headers before forwarding.
    Audits every call via memory-api /v1/audit-log (MCP-04).

    MCP wire protocol: POST to {sidecar_url}/mcp with MCP-Protocol-Version header.
    Tool sidecars run FastMCP with streamable-http transport (standalone uvicorn,
    NOT mounted in this app — see module docstring).
    """
    tool = await get_tool(tool_name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not registered or disabled")

    body = await request.body()
    team_scope = principal["team_scope"]
    user_sub = principal["sub"]

    # Extract JSON-RPC method/tool_method for audit action discrimination (INT-04).
    # Sidecars exposing distinct tools (e.g. write_drive_file vs read_drive_file) already
    # give distinct tool_name values. _rpc_method provides an extra level when one sidecar
    # handles multiple methods via JSON-RPC params.name or a top-level tool_method field.
    _rpc_method: str | None = None
    try:
        _body_json = _json.loads(body)
        _rpc_method = (
            (_body_json.get("params") or {}).get("name")
            or _body_json.get("tool_method")
        )
    except Exception:
        pass  # non-JSON body — skip method extraction

    # Forward to sidecar — MCP streamable-http protocol
    sidecar_url = f"{tool['sidecar_url'].rstrip('/')}/mcp"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                sidecar_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    # MCP streamable-http transport requires BOTH application/json AND
                    # text/event-stream in Accept (else server returns 406 Not Acceptable).
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                    "X-Team-Scope": team_scope,
                    "X-User-Sub": user_sub,
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Tool '{tool_name}' sidecar timeout")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"Tool '{tool_name}' sidecar unreachable")

    # Audit fire-and-forget (MCP-04).
    # action = mcp.tool_call.{tool_name} or mcp.tool_call.{tool_name}.{method}
    result_summary = resp.text[:200] if resp.status_code == 200 else f"error:{resp.status_code}"
    asyncio.create_task(
        log_tool_call(tool_name, user_sub, team_scope, result_summary, method=_rpc_method)
    )

    # Return the sidecar response to the caller
    try:
        content = resp.json()
    except Exception:
        content = {"raw": resp.text}
    return JSONResponse(content=content, status_code=resp.status_code)

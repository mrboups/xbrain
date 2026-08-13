"""MCP Gateway — routes tool calls to registered sidecars.

NOT a FastMCP host — plain FastAPI proxy that speaks the MCP wire protocol.
Each tool sidecar is a standalone FastMCP service (streamable-http transport).
Gateway is responsible for: auth, team_scope injection, registry lookup, audit logging.

Architecture note: FastMCP cannot be mounted inside a parent FastAPI app due to
RuntimeError: Task group is not initialized (github.com/modelcontextprotocol/python-sdk/issues/1367).
This gateway is deliberately a plain HTTP client-proxy, not a FastMCP host.

Aggregate server: a separate FastMCP process runs on port 8081 (see app/aggregate.py).
It exposes all registered tools as a single MCP server for LibreChat.
"""
from __future__ import annotations

import asyncio
import multiprocessing
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel

from app.aggregate import run_aggregate_server
from app.audit import log_tool_call
from app.auth import verify_bridge_jwt, verify_google_id_token
from app.config import settings
from app.registry import close_registry, get_tool, init_registry, list_tools, register_tool

log = structlog.get_logger(__name__)

# Module-level handle — kept outside lifespan so it survives across reloads in dev.
_agg_proc: multiprocessing.Process | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agg_proc
    await init_registry(settings.DATABASE_URL)
    log.info("mcp_gateway.started")

    # Launch MCP aggregate subprocess on port 8081.
    # Separate process is required — FastMCP cannot be mounted in a parent FastAPI app
    # (issue #1367: RuntimeError: Task group is not initialized).
    # daemon=True ensures the subprocess is killed automatically when the parent exits.
    _agg_proc = multiprocessing.Process(
        target=run_aggregate_server,
        name="mcp-aggregate",
        daemon=True,
    )
    _agg_proc.start()
    log.info("mcp_gateway.aggregate_subprocess_started", pid=_agg_proc.pid)

    yield

    # Graceful shutdown — terminate then wait up to 5s
    if _agg_proc and _agg_proc.is_alive():
        _agg_proc.terminate()
        _agg_proc.join(timeout=5)
        if _agg_proc.is_alive():
            _agg_proc.kill()
        log.info("mcp_gateway.aggregate_subprocess_stopped", pid=_agg_proc.pid)
    await close_registry()


app = FastAPI(title="xbrain MCP Gateway", version="0.1.0", lifespan=lifespan)


# ─── Auth ────────────────────────────────────────────────────────────────────


async def _assert_user_is_member(token: str, team_scope: str) -> None:
    """403 unless the bearer of *token* belongs to *team_scope*.

    memory-api owns team membership — it is the only component with the
    team_members table, and the same reasoning boards.py states for the board
    server applies here: a component that cannot see membership is not allowed
    to decide it. So the gateway asks, forwarding the caller's OWN token, and
    lets memory-api's authenticated /v1/teams/my-teams answer.

    Fail-closed on every failure mode — a network error, a 5xx, an unparseable
    body. "We could not check" and "you are not a member" get the same 403,
    because a gateway that forwards an unverified scope to the sidecars during
    a memory-api blip is exactly the outage-shaped hole worth avoiding.
    """
    url = f"{settings.MEMORY_API_URL.rstrip('/')}/v1/teams/my-teams"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        slugs = {t.get("slug") for t in r.json()}
    except Exception as exc:  # noqa: BLE001 — every failure is a refusal
        log.warning("gateway.membership_check_failed", team=team_scope, error=str(exc))
        raise HTTPException(
            status_code=403, detail="Could not verify team membership"
        ) from exc
    if team_scope not in slugs:
        raise HTTPException(
            status_code=403, detail=f"Not a member of team '{team_scope}'"
        )


async def _get_principal(request: Request) -> dict[str, Any]:
    """Verify Google OIDC or bridge JWT. Return {sub, team_scope}.

    team_scope comes from the JWT payload when the JWT carries one, otherwise
    from the X-Team-Scope header — and the two cases are NOT equally trusted:

    * bridge JWT — the claim wins, and the header fallback stands only because
      several trusted services (memory-api's URL prefetch, agent-runtime) mint
      claimless service tokens and pass the scope alongside. Holding
      BRIDGE_SHARED_SECRET is already the ability to mint any claim, so the
      fallback concedes nothing that the secret did not already concede.
    * Google OIDC — the header is a bare request header written by whoever is
      calling, and this function used to hand it straight to the sidecars at
      :213 while the docstring claimed "the client CANNOT override team_scope".
      That sentence was only ever true of the bridge branch. A user token now
      has its header verified against real membership before it goes anywhere.

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
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    # Google OIDC tokens carry no team_scope, so the header is the only source —
    # which makes naming the team mandatory rather than defaultable. The old
    # 'default' fallback silently sent a real user into whatever team happens to
    # be called "default"; asking is better than guessing wrong quietly.
    team_scope = request.headers.get("X-Team-Scope")
    if not team_scope:
        raise HTTPException(status_code=400, detail="X-Team-Scope header is required")
    await _assert_user_is_member(token, team_scope)
    return {"sub": claims["sub"], "team_scope": team_scope}


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
    """Forward a tool call to the registered sidecar via MCP Python SDK.

    Request body (caller sends):
      {"name": "<mcp_tool_name>", "arguments": {...}}

    tool_name (URL) = registered sidecar identifier.
    name (body)     = specific tool within that sidecar.

    Uses the MCP Python SDK stateful client pattern:
    streamablehttp_client -> ClientSession -> initialize() -> call_tool()
    This ensures the required MCP handshake (initialize) happens before every call.
    """
    tool = await get_tool(tool_name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not registered or disabled")

    body = await request.json()
    team_scope = principal["team_scope"]
    user_sub = principal["sub"]

    # Tool name within sidecar — supports MCP JSON-RPC format and simplified format
    mcp_tool_name: str = (
        (body.get("params") or {}).get("name")
        or body.get("name")
        or tool_name
    )
    # Arguments for the tool
    mcp_arguments: dict = (
        (body.get("params") or {}).get("arguments")
        or body.get("arguments")
        or {}
    )

    # Unwrap the aggregate's `kwargs` envelope (260603-1vr). The MCP aggregate registers
    # proxy tools whose function signature is `async def _proxy(**kwargs)`. Newer FastMCP
    # exposes such tools with a single generic `kwargs` object parameter, so the calling
    # model (LibreChat) is forced to send {"kwargs": {<real args>}}. Forwarding that verbatim
    # makes the sidecar tool (e.g. scrape(url)) fail with "url field required". Strip exactly
    # one `kwargs` level so the sidecar receives its real parameters. Direct callers
    # (agent-runtime) never wrap in `kwargs`, and no sidecar tool has a sole `kwargs` param,
    # so this is safe.
    if (
        isinstance(mcp_arguments, dict)
        and list(mcp_arguments.keys()) == ["kwargs"]
        and isinstance(mcp_arguments["kwargs"], dict)
    ):
        mcp_arguments = mcp_arguments["kwargs"]

    sidecar_url = tool["sidecar_url"].rstrip("/") + "/mcp"

    try:
        async with streamablehttp_client(
            sidecar_url,
            headers={"X-Team-Scope": team_scope, "X-User-Sub": user_sub},
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    listed = await session.list_tools()
                    available = [t.name for t in listed.tools]
                    if mcp_tool_name not in available and len(available) == 1:
                        log.info(
                            "gateway.tool_name_resolved",
                            requested=mcp_tool_name,
                            actual=available[0],
                            sidecar=tool_name,
                        )
                        mcp_tool_name = available[0]
                except Exception as exc:  # noqa: BLE001
                    log.warning("gateway.list_tools_failed", sidecar=tool_name, error=str(exc))
                result = await session.call_tool(mcp_tool_name, mcp_arguments)
    except Exception as exc:
        log.error("gateway.tool_call_error", tool=tool_name, sidecar=sidecar_url, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Tool '{tool_name}' call failed: {exc}") from exc

    # Audit fire-and-forget
    result_summary = str(result.content)[:200] if result.content else "empty"
    asyncio.create_task(
        log_tool_call(tool_name, user_sub, team_scope, result_summary, method=mcp_tool_name)
    )

    # Serialize MCP CallToolResult
    content_list = []
    for item in (result.content or []):
        if hasattr(item, "model_dump"):
            content_list.append(item.model_dump())
        else:
            content_list.append({"type": "text", "text": str(item)})

    return JSONResponse(
        content={"content": content_list, "isError": bool(result.isError)},
        status_code=200,
    )

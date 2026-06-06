"""mcp-brain — remote MCP sidecar exposing team brain tools.

Port 8104. Two auth paths:
  1. TOKEN PATH (public): Authorization: Bearer xbt_... — validated via GET /v1/me.
  2. EMAIL PATH (LibreChat internal only): X-LibreChat-User-Email + X-Internal-Secret
     matching BRIDGE_SHARED_SECRET. Team resolved server-side via memory-api internal
     endpoint. Unreachable without the correct shared secret (fail-closed).
"""
from __future__ import annotations

import hmac
import json
import structlog
from mcp.server.fastmcp import FastMCP, Context
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from app import memory_client, oauth_verify
from app.bridge_jwt import mint_bridge_jwt
from app.config import settings

log = structlog.get_logger(__name__)
mcp = FastMCP("xbrain-brain", host=settings.FASTMCP_HOST, port=settings.FASTMCP_PORT)


def _protected_resource_metadata() -> dict:
    """RFC 9728 protected-resource metadata (also used by the WWW-Authenticate URL)."""
    return {
        "resource": settings.OAUTH_RESOURCE_URL.rstrip("/"),
        "authorization_servers": [settings.OAUTH_ISSUER_URL.rstrip("/")],
        "scopes_supported": ["brain:read", "brain:write"],
        "bearer_methods_supported": ["header"],
    }


# The protected-resource metadata is served by the custom_route below at the app
# ROOT path /.well-known/oauth-protected-resource (confirmed against mcp 1.27.2:
# FastMCP custom routes mount at root, NOT under the /mcp streamable path; the
# default streamable_http_path is /mcp). The WWW-Authenticate resource_metadata
# URL therefore points at the resource host ROOT, matching what nginx proxies to
# mcp-brain. We derive the host root from OAUTH_RESOURCE_URL via the scheme+netloc
# so the trailing /mcp path segment is dropped correctly.
def _resource_host_root() -> str:
    from urllib.parse import urlparse

    p = urlparse(settings.OAUTH_RESOURCE_URL)
    return f"{p.scheme}://{p.netloc}"


_PROTECTED_RESOURCE_METADATA_URL = (
    _resource_host_root() + "/.well-known/oauth-protected-resource"
)


async def _resolve(ctx: Context) -> tuple[str, str, bool]:
    """Return (token_or_jwt, team_scope, is_connector) from the request context.

    Prefix routing is unambiguous (checked in this order):

    CONNECTOR PATH (new, public — Claude.ai Custom Connector):
      Authorization: Bearer oat_... -> introspected against memory-api. On
      success a bridge JWT is minted for the bound team so downstream
      memory_client calls authenticate normally. is_connector=True.

    TOKEN PATH (unchanged, public):
      Authorization: Bearer xbt_... -> validated via GET /v1/me. is_connector=False.
      An xbt_ token NEVER calls introspect.

    EMAIL PATH (unchanged, LibreChat internal only):
      X-Internal-Secret + X-LibreChat-User-Email -> team resolved server-side,
      returns a bridge JWT. Bypasses the oat_ branch entirely. is_connector=False.

    Raises ValueError on any auth failure (fail-closed).
    """
    headers = ctx.request_context.request.headers
    authorization = headers.get("authorization") or ""
    email = headers.get("x-librechat-user-email") or ""
    x_internal_secret = headers.get("x-internal-secret") or ""

    raw = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""

    # ── CONNECTOR PATH (oat_) ──────────────────────────────────────────────────
    # Checked FIRST so prefix routing is unambiguous. xbt_/email never enter here.
    if raw.startswith("oat_"):
        info = await oauth_verify.introspect(raw)
        team_scope = info["team_scope"]
        secret = settings.BRIDGE_SHARED_SECRET
        # Mint a bridge JWT bound to the resolved team so /v1 auth (which already
        # trusts bridge JWTs via deps.get_team_scope) accepts downstream calls.
        # We do NOT teach /v1 auth about oat_ tokens.
        bridge = mint_bridge_jwt(
            secret=secret, team_scope=team_scope, sub=info.get("sub") or "oauth-connector"
        )
        log.info("mcp_brain.resolve", mode="connector", team=team_scope)
        return bridge, team_scope, True

    # ── TOKEN PATH (xbt_) ──────────────────────────────────────────────────────
    if raw.startswith("xbt_"):
        me = await memory_client.get_me(raw)
        team_scope = me.get("api_token_team_scope") or me.get("team_scope")
        if not team_scope:
            raise ValueError(
                "Token is not associated with a team. Create a token via POST /v1/me/api-token"
            )
        log.info("mcp_brain.resolve", mode="token", team=team_scope)
        return raw, team_scope, False

    # ── EMAIL PATH ────────────────────────────────────────────────────────────
    # Gated: requires correct shared secret (constant-time compare) + non-empty email.
    # Fails closed: empty secret, wrong secret, or missing email → auth error.
    secret = settings.BRIDGE_SHARED_SECRET
    ok = (
        settings.INTERNAL_EMAIL_PATH_ENABLED
        and bool(secret)
        and bool(x_internal_secret)
        and hmac.compare_digest(x_internal_secret, secret)
        and bool(email)
    )
    if not ok:
        raise ValueError(
            "Authentication required: set your xbt_ token, or call from an authorized frontend."
        )

    sub = f"email:{email}"
    # Mint a placeholder JWT (team_scope="default") just to authenticate the resolve call.
    jwt0 = mint_bridge_jwt(secret=secret, team_scope="default", sub=sub)
    team_scope = await memory_client.resolve_team_scope_internal(jwt0, sub)
    if not team_scope:
        # Retry with bare email in case source_user_id is stored without the prefix.
        team_scope = await memory_client.resolve_team_scope_internal(jwt0, email)
    if not team_scope:
        raise ValueError("No team is associated with your account yet.")

    # Mint a second JWT with the real resolved team_scope for all downstream tool calls.
    jwt1 = mint_bridge_jwt(secret=secret, team_scope=team_scope, sub=sub)
    log.info("mcp_brain.resolve", mode="email", team=team_scope)
    return jwt1, team_scope, False


# === Protected Resource wiring (quick-260604-glo) ===========================


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def protected_resource_metadata(request: Request) -> Response:
    """RFC 9728 metadata so Claude.ai can discover the Authorization Server."""
    return JSONResponse(_protected_resource_metadata())


def _has_recognized_auth(headers) -> bool:
    """True if the request carries a recognized credential (oat_/xbt_ bearer or
    the internal email path). Used to decide whether to 401 unauthenticated
    MCP calls WITHOUT seizing token validation from _resolve()."""
    authz = headers.get("authorization") or ""
    raw = authz.removeprefix("Bearer ").strip() if authz.startswith("Bearer ") else ""
    if raw.startswith("oat_") or raw.startswith("xbt_"):
        return True
    # Internal email path: shared secret + email headers both present.
    if headers.get("x-internal-secret") and headers.get("x-librechat-user-email"):
        return True
    return False


class UnauthenticatedMCP401Middleware:
    """ASGI middleware that returns 401 + WWW-Authenticate for unauthenticated
    requests to the /mcp path, pointing Claude.ai at the protected-resource
    metadata (RFC 9728 + the MCP Authorization spec).

    We do NOT pass FastMCP ``auth=`` / ``token_verifier=`` — that would seize ALL
    token validation and break the existing xbt_ + email paths. This middleware
    only emits the unauthenticated 401; ``_resolve()`` still owns credential
    validation for recognized requests. OPTIONS (CORS preflight) and non-/mcp
    paths (e.g. the well-known metadata route) pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if path.rstrip("/") == "/mcp" and method != "OPTIONS":
            headers = Headers(scope=scope)
            if not _has_recognized_auth(headers):
                resp = JSONResponse(
                    {"error": "unauthorized", "error_description": "authentication required"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": (
                            f'Bearer resource_metadata="{_PROTECTED_RESOURCE_METADATA_URL}"'
                        )
                    },
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


@mcp.tool()
async def memory_search(
    query: str,
    limit: int = 10,
    project_scope: str | None = None,
    ctx: Context = None,
) -> str:
    """Search the team's persistent memory store.

    Args:
        query: Natural-language search query.
        limit: Max results (1-20). Default 10.
        project_scope: Optional project slug to narrow search.

    Returns: JSON array of memory items with id, content, truth_level, source, score.
    """
    token, team_scope, is_connector = await _resolve(ctx)  # noqa: F841 (read-only tool)
    results = await memory_client.memory_search(token, team_scope, query, limit, project_scope)
    return json.dumps(results, ensure_ascii=False, default=str)


@mcp.tool()
async def memory_add(
    content: str,
    project_scope: str | None = None,
    truth_level: str = "WORKING",
    ctx: Context = None,
) -> str:
    """Store a new fact or note in the team's memory.

    Args:
        content: The fact, observation, or note to store.
        project_scope: Optional project slug to associate with.
        truth_level: EPHEMERAL | WORKING | VALIDATED | CANONICAL. Default WORKING.

    Returns: JSON with memory item id.
    """
    token, team_scope, is_connector = await _resolve(ctx)
    result = await memory_client.memory_add(
        token, team_scope, content, project_scope, truth_level, is_connector=is_connector
    )
    return json.dumps(result)


@mcp.tool()
async def tasks_list(
    status: str | None = None,
    limit: int = 20,
    ctx: Context = None,
) -> str:
    """List team tasks.

    Args:
        status: Filter by status: todo | in_progress | done | cancelled. Default: all.
        limit: Max results (1-50). Default 20.

    Returns: JSON array of tasks.
    """
    token, team_scope, is_connector = await _resolve(ctx)  # noqa: F841 (read-only tool)
    result = await memory_client.tasks_list(token, team_scope, status, limit)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def task_create(
    title: str,
    description: str | None = None,
    assignee_email: str | None = None,
    ctx: Context = None,
) -> str:
    """Create a new task in the team's task tracker.

    Args:
        title: Short task title (max 512 chars).
        description: Optional longer description.
        assignee_email: Optional email of the assignee.

    Returns: JSON with created task id and details.
    """
    token, team_scope, is_connector = await _resolve(ctx)
    result = await memory_client.task_create(
        token, team_scope, title, description, assignee_email, is_connector=is_connector
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def task_update(
    task_id: str,
    status: str,
    ctx: Context = None,
) -> str:
    """Update a task's status.

    Args:
        task_id: UUID of the task to update.
        status: New status: todo | in_progress | done | cancelled.

    Returns: JSON with updated task.
    """
    token, team_scope, is_connector = await _resolve(ctx)  # noqa: F841 (read-only tool)
    result = await memory_client.task_update(token, team_scope, task_id, status)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def contacts_search(
    query: str | None = None,
    company: str | None = None,
    limit: int = 20,
    ctx: Context = None,
) -> str:
    """Search the team's CRM contacts.

    Args:
        query: Name or keyword search.
        company: Filter by company name.
        limit: Max results (1-50). Default 20.

    Returns: JSON array of contacts.
    """
    token, team_scope, is_connector = await _resolve(ctx)  # noqa: F841 (read-only tool)
    result = await memory_client.contacts_search(token, team_scope, query, company, limit)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def contact_add(
    name: str | None = None,
    email: str | None = None,
    company: str | None = None,
    role: str | None = None,
    ctx: Context = None,
) -> str:
    """Add a contact to the team's CRM.

    Args:
        name: Full name of the contact.
        email: Email address (used as unique key within team).
        company: Company or organization name.
        role: Job title or role.

    Returns: JSON with created contact id.
    """
    if not name and not email:
        return json.dumps({"error": "At least name or email is required"})
    token, team_scope, is_connector = await _resolve(ctx)
    result = await memory_client.contact_add(
        token, team_scope, name, email, company, role, is_connector=is_connector
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def agent_invoke(
    agent_name: str,
    content: str,
    project_scope: str | None = None,
    ctx: Context = None,
) -> str:
    """Invoke a platform AI agent by name and store its response as a memory item.

    Args:
        agent_name: Exact name of the agent (e.g. 'meeting-recap').
        content: Input text for the agent to process.
        project_scope: Optional project slug for the result.

    Returns: JSON with agent recap text and memory_item_id.
    """
    token, team_scope, is_connector = await _resolve(ctx)  # noqa: F841 (read-only tool)
    result = await memory_client.agent_invoke(token, team_scope, agent_name, content, project_scope)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def team_context(ctx: Context = None) -> str:
    """Get team context summary — team name, plan, member count, recent activity.

    Returns: JSON with team info.
    """
    token, team_scope, is_connector = await _resolve(ctx)  # noqa: F841 (read-only tool)
    result = await memory_client.team_context(token, team_scope)
    result["_team_scope"] = team_scope
    return json.dumps(result, ensure_ascii=False, default=str)


def build_app():
    """Build the FastMCP streamable-HTTP app wrapped with the unauthenticated-401
    middleware. Exposed as a function so the run path (and tests) can reuse it.

    The custom_route for /.well-known/oauth-protected-resource is already
    registered on `mcp`, so it is included in streamable_http_app() and is NOT
    affected by the /mcp-only 401 gate."""
    app = mcp.streamable_http_app()
    return UnauthenticatedMCP401Middleware(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        build_app(),
        host=settings.FASTMCP_HOST,
        port=settings.FASTMCP_PORT,
    )

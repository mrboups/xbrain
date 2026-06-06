"""Async client that calls memory-api on behalf of MCP tool calls.

Auth: forwards the caller's Bearer token unchanged.
Team scope: provided at MCP session init via GET /v1/me.
"""
import uuid
from datetime import datetime, timezone

import httpx
import structlog
from app.config import settings

log = structlog.get_logger(__name__)
_BASE = settings.MEMORY_API_URL.rstrip("/")


def _headers(token: str, team_scope: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Team-Scope": team_scope,
        "Content-Type": "application/json",
    }


# Connector write guardrails (quick-260604-glo): Claude.ai-originated writes are
# capped at WORKING truth and tagged with a fixed source. EPHEMERAL/WORKING pass
# through unchanged; any higher level is downgraded to WORKING.
CONNECTOR_SOURCE = "claude.ai-connector"
_CONNECTOR_MAX_TRUTH = {"EPHEMERAL", "WORKING"}


def clamp_truth_level(truth_level: str) -> str:
    """Cap a connector write's truth_level at WORKING.

    VALIDATED / CANONICAL / PUBLIC (and any unrecognized value) are downgraded
    to WORKING. EPHEMERAL and WORKING pass through unchanged.
    """
    return truth_level if truth_level in _CONNECTOR_MAX_TRUTH else "WORKING"


async def get_me(token: str) -> dict:
    """Resolve token to user + api_token_team_scope."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_BASE}/v1/me", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()


async def resolve_team_scope_internal(bridge_jwt: str, sub: str) -> str | None:
    """Resolve a user's primary team slug via memory-api internal endpoint.

    Args:
        bridge_jwt: A valid bridge-scope JWT minted by mint_bridge_jwt.
        sub:        Subject identifier to look up (e.g. "email:user@example.com" or bare email).

    Returns:
        Team slug string if the user has a team, None if unknown or no team.
        Raises httpx.HTTPStatusError on unexpected HTTP errors.
    """
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{_BASE}/v1/internal/resolve-team-scope",
            params={"sub": sub},
            headers={"Authorization": f"Bearer {bridge_jwt}"},
        )
        r.raise_for_status()
        return r.json().get("team_scope")


async def memory_search(token: str, team_scope: str, query: str, limit: int = 10, project_scope: str | None = None) -> list[dict]:
    params = {"q": query, "limit": min(limit, 20)}
    if project_scope:
        params["project_scope"] = project_scope
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{_BASE}/v1/memory/search", params=params, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def memory_add(token: str, team_scope: str, content: str, project_scope: str | None = None, truth_level: str = "WORKING", is_connector: bool = False, source: str | None = None) -> dict:
    # /v1/memory/upsert expects a COMPLETE MemoryItem (260603-29h): `id`,
    # `created_at` and `updated_at` are required fields with no server-side
    # default, so the client must supply them (otherwise 422). Use a fresh
    # uuid4 (explicit saves are not deduped) + now() timestamps.
    now = datetime.now(timezone.utc).isoformat()
    # Connector writes (quick-260604-glo): force source + cap truth at WORKING.
    item_source = source or ("mcp-brain" if not is_connector else CONNECTOR_SOURCE)
    if is_connector:
        item_source = CONNECTOR_SOURCE
        truth_level = clamp_truth_level(truth_level)
    item = {
        "id": str(uuid.uuid4()),
        "team_scope": team_scope,
        "content": content,
        "source": item_source,
        "truth_level": truth_level,
        "confidence": 0.8,
        "visibility": "team",
        "validation_status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    if project_scope:
        item["project_scope"] = project_scope
    payload = {"item": item}
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(f"{_BASE}/v1/memory/upsert", json=payload, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def tasks_list(token: str, team_scope: str, status: str | None = None, limit: int = 20) -> list[dict]:
    params = {"limit": min(limit, 50)}
    if status:
        params["status"] = status
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_BASE}/v1/tasks", params=params, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def task_create(token: str, team_scope: str, title: str, description: str | None = None, assignee_email: str | None = None, is_connector: bool = False, source: str | None = None) -> dict:
    payload = {"title": title}
    if description:
        payload["description"] = description
    if assignee_email:
        payload["assignee_email"] = assignee_email
    # Connector writes (quick-260604-glo): tag the origin. The /v1/tasks route
    # accepts 'claude.ai-connector' as of migration 0023 (column widened to
    # VARCHAR(32) + CHECK extended), so the connector provenance tag lands.
    if is_connector or source:
        payload["source"] = source or CONNECTOR_SOURCE
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{_BASE}/v1/tasks", json=payload, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def task_update(token: str, team_scope: str, task_id: str, status: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.patch(f"{_BASE}/v1/tasks/{task_id}", json={"status": status}, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def contacts_search(token: str, team_scope: str, query: str | None = None, company: str | None = None, limit: int = 20) -> list[dict]:
    params = {"limit": min(limit, 50)}
    if query:
        params["q"] = query
    if company:
        params["company"] = company
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_BASE}/v1/crm/contacts", params=params, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def contact_add(token: str, team_scope: str, name: str | None = None, email: str | None = None, company: str | None = None, role: str | None = None, is_connector: bool = False, source: str | None = None) -> dict:
    payload = {"team_scope": team_scope, "contact_type": "direct", "truth_level": "EPHEMERAL", "confidence": 0.7}
    # Connector writes (quick-260604-glo): the /v1/crm/contacts `source` field is
    # a free-form string, so the connector tag lands directly.
    if is_connector or source:
        payload["source"] = source or CONNECTOR_SOURCE
    if name:
        payload["full_name"] = name
    if email:
        payload["email"] = email
    if company:
        payload["company_name"] = company
    if role:
        payload["role"] = role
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{_BASE}/v1/crm/contacts", json=payload, headers=_headers(token, team_scope))
        r.raise_for_status()
        return r.json()


async def agent_invoke(token: str, team_scope: str, agent_name: str, content: str, project_scope: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        # Find agent by name
        r = await c.get(f"{_BASE}/v1/admin/agents", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        agents = r.json()
        match = next((a for a in agents if a["name"] == agent_name and a["enabled"]), None)
        if match is None:
            raise ValueError(f"Agent '{agent_name}' not found or disabled")
        # Invoke it
        payload = {"content": content, "team_scope": team_scope}
        if project_scope:
            payload["project_scope"] = project_scope
        r2 = await c.post(f"{_BASE}/v1/agents/{match['id']}/invoke", json=payload, headers=_headers(token, team_scope))
        r2.raise_for_status()
        return r2.json()


async def team_context(token: str, team_scope: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{_BASE}/v1/teams/my-team", headers=_headers(token, team_scope))
        if r.status_code == 204:
            return {"team_scope": team_scope, "status": "no_team"}
        r.raise_for_status()
        return r.json()

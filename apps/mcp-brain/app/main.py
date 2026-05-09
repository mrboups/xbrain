"""mcp-brain — remote MCP sidecar exposing team brain tools.

Port 8104. Bearer token (xbt_...) in Authorization header.
Team scope derived from token's registered team_scope via GET /v1/me.
"""
from __future__ import annotations

import json
import structlog
from mcp.server.fastmcp import FastMCP, Context
from app import memory_client
from app.config import settings

log = structlog.get_logger(__name__)
mcp = FastMCP("xbrain-brain", host=settings.FASTMCP_HOST, port=settings.FASTMCP_PORT)


def _get_token(ctx: Context) -> str:
    auth = (ctx.request_context.request.headers.get("authorization") or "")
    if not auth.startswith("Bearer "):
        raise ValueError("Missing Bearer token in Authorization header")
    return auth.removeprefix("Bearer ")


async def _resolve(ctx: Context) -> tuple[str, str]:
    """Return (token, team_scope) from the request context."""
    token = _get_token(ctx)
    me = await memory_client.get_me(token)
    team_scope = me.get("api_token_team_scope") or me.get("team_scope")
    if not team_scope:
        raise ValueError("Token is not associated with a team. Create a token via POST /v1/me/api-token")
    return token, team_scope


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
    token, team_scope = await _resolve(ctx)
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
    token, team_scope = await _resolve(ctx)
    result = await memory_client.memory_add(token, team_scope, content, project_scope, truth_level)
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
    token, team_scope = await _resolve(ctx)
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
    token, team_scope = await _resolve(ctx)
    result = await memory_client.task_create(token, team_scope, title, description, assignee_email)
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
    token, team_scope = await _resolve(ctx)
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
    token, team_scope = await _resolve(ctx)
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
    token, team_scope = await _resolve(ctx)
    result = await memory_client.contact_add(token, team_scope, name, email, company, role)
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
    token, team_scope = await _resolve(ctx)
    result = await memory_client.agent_invoke(token, team_scope, agent_name, content, project_scope)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def team_context(ctx: Context = None) -> str:
    """Get team context summary — team name, plan, member count, recent activity.

    Returns: JSON with team info.
    """
    token, team_scope = await _resolve(ctx)
    result = await memory_client.team_context(token, team_scope)
    result["_team_scope"] = team_scope
    return json.dumps(result, ensure_ascii=False, default=str)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

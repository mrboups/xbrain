"""/v1/admin/agents (CRUD) + /v1/agents/{id}/invoke — Phase 8 D4 platform agents registry.

Endpoints:
  - POST /v1/admin/agents          (admin)   — create agent
  - GET  /v1/admin/agents          (admin)   — list agents
  - PATCH /v1/admin/agents/{id}    (admin)   — update agent
  - DELETE /v1/admin/agents/{id}   (admin)   — hard delete
  - POST /v1/agents/{id}/invoke    (user|bridge) — invoke Claude synchronously, store recap

Notes:
  - tools_json est stocké mais NON passé à Anthropic en Phase 8 (Pitfall 6 RESEARCH.md).
    Phase 9+ ajoutera l'exécution réelle via mcp-gateway.
  - Le recap est stocké dans memory_items (source='agent', truth_level='WORKING').
  - L'invocation accepte user OU bridge — granola-sync (bridge) auto-déclenche meeting-recap (D5).
"""

import asyncio
import json
import types
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import settings
from app.deps import _is_admin, get_current_principal, get_session

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Anthropic client (singleton, lazy-init — pattern memory.py) ─────────────

_anthropic_client: AsyncAnthropic | None = None


def _get_anthropic() -> AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        return None
    _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


# ── Pydantic models ─────────────────────────────────────────────────────────


class AgentCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=4000)
    system_prompt: str = Field(..., min_length=1, max_length=20000)
    model: str = Field(default="claude-haiku-4-5-20251001", min_length=1, max_length=128)
    tools_json: list[dict[str, Any]] | None = None
    enabled: bool = True
    auto_trigger: bool = True


class AgentPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(None, max_length=4000)
    system_prompt: str | None = Field(None, min_length=1, max_length=20000)
    model: str | None = Field(None, min_length=1, max_length=128)
    tools_json: list[dict[str, Any]] | None = None
    enabled: bool | None = None
    auto_trigger: bool | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None
    system_prompt: str
    model: str
    tools_json: list[dict[str, Any]] | None
    enabled: bool
    auto_trigger: bool
    created_at: datetime
    updated_at: datetime


class AgentInvokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(..., min_length=1, max_length=200000)
    team_scope: str = Field(..., min_length=1, max_length=64)
    source_ref: str | None = Field(None, max_length=256)
    project_scope: str | None = Field(None, max_length=64)


class AgentInvokeOut(BaseModel):
    memory_item_id: UUID
    recap: str
    agent_name: str


# ═══════════════════════════════════════════════════════════════════════════
# Admin CRUD
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/admin/agents", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Create a platform agent. Admin only."""
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    user = principal.get("user")
    created_by = user.id if user else None
    tools_payload = json.dumps(body.tools_json) if body.tools_json is not None else None

    try:
        row = (await session.execute(sa.text("""
            INSERT INTO agent_definitions (
                name, description, system_prompt, model, tools_json,
                enabled, auto_trigger, created_by
            ) VALUES (
                :name, :desc, :prompt, :model, :tools::jsonb,
                :enabled, :auto, :cb
            )
            RETURNING id, name, description, system_prompt, model, tools_json,
                      enabled, auto_trigger, created_at, updated_at
        """), {
            "name": body.name,
            "desc": body.description,
            "prompt": body.system_prompt,
            "model": body.model,
            "tools": tools_payload,
            "enabled": body.enabled,
            "auto": body.auto_trigger,
            "cb": str(created_by) if created_by else None,
        })).mappings().fetchone()
    except sa.exc.IntegrityError as exc:
        # UNIQUE(name) violation
        raise HTTPException(409, f"Agent name '{body.name}' already exists") from exc

    await write_audit(
        session,
        actor_user_id=created_by,
        team_scope="_platform",
        action="agent.created",
        target_id=str(row["id"]),
        payload={"name": body.name, "model": body.model},
    )
    await session.commit()
    return AgentOut(**dict(row))


@router.get("/admin/agents", response_model=list[AgentOut])
async def list_agents(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """List all agents (enabled and disabled). Admin only."""
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    rows = (await session.execute(sa.text("""
        SELECT id, name, description, system_prompt, model, tools_json,
               enabled, auto_trigger, created_at, updated_at
        FROM agent_definitions
        ORDER BY name ASC
    """))).mappings().all()
    return [AgentOut(**dict(r)) for r in rows]


@router.patch("/admin/agents/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: UUID,
    body: AgentPatchBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Patch an agent. Admin only. Only sent fields are updated."""
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    fields: dict[str, Any] = {}
    sets: list[str] = []
    if body.description is not None:
        sets.append("description = :description")
        fields["description"] = body.description
    if body.system_prompt is not None:
        sets.append("system_prompt = :system_prompt")
        fields["system_prompt"] = body.system_prompt
    if body.model is not None:
        sets.append("model = :model")
        fields["model"] = body.model
    if body.tools_json is not None:
        sets.append("tools_json = :tools_json::jsonb")
        fields["tools_json"] = json.dumps(body.tools_json)
    if body.enabled is not None:
        sets.append("enabled = :enabled")
        fields["enabled"] = body.enabled
    if body.auto_trigger is not None:
        sets.append("auto_trigger = :auto_trigger")
        fields["auto_trigger"] = body.auto_trigger

    if not sets:
        raise HTTPException(400, "No fields provided")

    sets.append("updated_at = now()")
    fields["agent_id"] = str(agent_id)
    sql = f"""
        UPDATE agent_definitions SET {', '.join(sets)}
        WHERE id = :agent_id
        RETURNING id, name, description, system_prompt, model, tools_json,
                  enabled, auto_trigger, created_at, updated_at
    """
    row = (await session.execute(sa.text(sql), fields)).mappings().fetchone()
    if row is None:
        raise HTTPException(404, "Agent not found")

    user = principal.get("user")
    await write_audit(
        session,
        actor_user_id=user.id if user else None,
        team_scope="_platform",
        action="agent.updated",
        target_id=str(agent_id),
        payload={"changed_fields": list(set(fields.keys()) - {"agent_id"})},
    )
    await session.commit()
    return AgentOut(**dict(row))


@router.delete("/admin/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Hard-delete an agent. Admin only."""
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    row = (await session.execute(
        sa.text("DELETE FROM agent_definitions WHERE id = :id RETURNING name"),
        {"id": str(agent_id)},
    )).fetchone()
    if row is None:
        raise HTTPException(404, "Agent not found")

    user = principal.get("user")
    await write_audit(
        session,
        actor_user_id=user.id if user else None,
        team_scope="_platform",
        action="agent.deleted",
        target_id=str(agent_id),
        payload={"name": row.name},
    )
    await session.commit()
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Invoke (user OR bridge)
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/agents/{agent_id}/invoke", response_model=AgentInvokeOut)
async def invoke_agent(
    agent_id: UUID,
    body: AgentInvokeBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Invoke an agent synchronously via Anthropic. Stores result as memory_item.

    Auth: user OR bridge (granola-sync uses bridge JWT to auto-trigger meeting-recap).
    Pitfall 1 RESEARCH.md: team_scope is read from body, NOT enforced by header.
    Pitfall 6 RESEARCH.md: tools_json is stored but NEVER passed to Anthropic in Phase 8.
    """
    # Bridge cross-team check (Pitfall 1) — if bridge JWT carries team_scope, must match body
    if principal.get("kind") == "bridge":
        bridge_team = principal.get("team_scope")
        if bridge_team and bridge_team != body.team_scope:
            raise HTTPException(
                403,
                f"Bridge JWT team_scope '{bridge_team}' does not match body team_scope '{body.team_scope}'",
            )

    # Fetch agent definition
    agent_row = (await session.execute(sa.text("""
        SELECT id, name, system_prompt, model, enabled
        FROM agent_definitions
        WHERE id = :id
    """), {"id": str(agent_id)})).mappings().fetchone()

    if agent_row is None:
        raise HTTPException(404, "Agent not found")
    if not agent_row["enabled"]:
        raise HTTPException(409, f"Agent '{agent_row['name']}' is disabled")

    # Anthropic call
    client = _get_anthropic()
    if client is None:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    try:
        msg = await client.messages.create(
            model=agent_row["model"],
            max_tokens=4096,
            system=agent_row["system_prompt"],
            # tools_json NOT passed — Phase 8 stores but does not execute (Pitfall 6)
            messages=[{"role": "user", "content": body.content[:50000]}],
        )
    except Exception as exc:
        log.error("agent.invoke.anthropic_failed", agent_id=str(agent_id), error=str(exc))
        raise HTTPException(502, f"Anthropic call failed: {exc}") from exc

    recap_text = ""
    if msg.content:
        # Concatenate all TextBlock items (Anthropic SDK returns list of blocks)
        for block in msg.content:
            if hasattr(block, "text"):
                recap_text += block.text
    if not recap_text:
        recap_text = "(empty agent response)"

    # Store as memory_item (7-field tagging contract — D4 + project tagging contract)
    memory_item_id = uuid4()
    metadata_payload = {
        "agent_id": str(agent_id),
        "agent_name": agent_row["name"],
        "agent_model": agent_row["model"],
    }
    if agent_row.get("auto_trigger"):
        # auto_trigger agents (e.g. meeting-recap) produce structured output with
        # action items — flag so _maybe_create_task_from_action picks them up.
        metadata_payload["contains_action"] = True
    await session.execute(sa.text("""
        INSERT INTO memory_items (
            id, team_scope, project_scope, content, source, source_ref,
            truth_level, confidence, visibility, validation_status, metadata
        ) VALUES (
            :id, :ts, :ps, :content, 'agent', :sref,
            'WORKING', 0.85, 'team', 'pending', :meta::jsonb
        )
    """), {
        "id": str(memory_item_id),
        "ts": body.team_scope,
        "ps": body.project_scope,
        "content": recap_text,
        "sref": body.source_ref,
        "meta": json.dumps(metadata_payload),
    })

    user = principal.get("user")
    await write_audit(
        session,
        actor_user_id=user.id if user else None,
        team_scope=body.team_scope,
        action="agent.invoked",
        target_id=str(agent_id),
        payload={
            "agent_name": agent_row["name"],
            "memory_item_id": str(memory_item_id),
            "source_ref": body.source_ref,
        },
    )
    await session.commit()

    # Fire background hooks on agent output — same as memory/upsert does after a write.
    # Lazy import avoids a module-level circular dependency (memory imports nothing from agents).
    if recap_text:
        from app.routes.memory import _extract_crm_contacts, _maybe_create_task_from_action, _enrich_with_graphiti  # noqa: PLC0415
        _item = types.SimpleNamespace(
            content=recap_text,
            metadata=metadata_payload,
            source="agent",
            id=memory_item_id,
        )
        asyncio.create_task(_extract_crm_contacts(recap_text, body.team_scope, "agent"))
        asyncio.create_task(_maybe_create_task_from_action(_item, body.team_scope))
        asyncio.create_task(_enrich_with_graphiti(recap_text, body.team_scope))

    log.info(
        "agent.invoked",
        agent_id=str(agent_id),
        agent_name=agent_row["name"],
        team_scope=body.team_scope,
        memory_item_id=str(memory_item_id),
    )
    return AgentInvokeOut(
        memory_item_id=memory_item_id,
        recap=recap_text,
        agent_name=agent_row["name"],
    )

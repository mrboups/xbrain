"""/v1/memory/* — backend-agnostic memory endpoints (Phase 2)."""

import asyncio
import os
import re
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryItem, MemoryProvider, SearchHit, TruthLevel

from app.audit import write_audit
from app.config import settings
from app.db.session import async_session_factory
from app.deps import (
    get_current_principal,
    get_memory_provider,
    get_session,
    get_team_scope,
)
from app.models.neo4j_outbox import NeoOutboxEntry

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Graphiti enrichment — fail-soft (plan 05-01)
# ---------------------------------------------------------------------------
_GRAPHITI_URL = os.environ.get("GRAPHITI_SERVICE_URL", "http://graphiti-service:8300")


async def _enrich_with_graphiti(content: str, group_id: str) -> None:
    """Optional call to graphiti-service after successful memory upsert.

    Fail-soft: if graphiti-service is down or returns an error, memory-api
    continues normally without affecting the HTTP response to the caller.

    Threat T-05-01-02: graphiti-service is internal-only (no public ports).
    group_id is provided by memory-api (trusted source) — no external input.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{_GRAPHITI_URL}/v1/ingest",
                json={"content": content, "group_id": group_id, "source": "memory-api"},
            )
            r.raise_for_status()
    except Exception as exc:
        log.warning("graphiti.enrich_skipped", error=str(exc), group_id=group_id)


# ── Phase 7 — auto contact extraction (D1) ──────────────────────────────

from anthropic import AsyncAnthropic  # noqa: E402 — placed here after stdlib imports

_anthropic_client: AsyncAnthropic | None = None
_ACTION_RE = re.compile(
    r"\b(TODO|à faire|action required|action requise|to do)\b", re.IGNORECASE
)


def _get_anthropic() -> AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        return None
    _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


async def _extract_crm_contacts(content: str, team_scope: str, source: str) -> None:
    """Extract person mentions from content, upsert into contacts. Fail-soft."""
    try:
        if not content or len(content) < 50:
            return
        client = _get_anthropic()
        if client is None:
            log.warning("crm.extract.no_anthropic_key", team_scope=team_scope)
            return

        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": (
                        "Extract distinct person mentions from the text. "
                        "Return STRICT JSON array (no prose, no markdown fence): "
                        '[{"name": "...", "email": "...-or-null"}]. '
                        "If no people, return []."
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content[:10000]}],
        )
        text = msg.content[0].text.strip() if msg.content else "[]"
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        import json as _json

        people = _json.loads(text)
        if not isinstance(people, list):
            return

        import sqlalchemy as sa

        # Use a fresh DB connection (the request session is gone)
        async with async_session_factory() as session:
            for person in people[:20]:  # cap to 20 per item
                if not isinstance(person, dict):
                    continue
                name = person.get("name")
                email = person.get("email")
                if not (name or email):
                    continue
                contact_source = "agent" if source.startswith("agent") else "chat"
                if email:
                    await session.execute(
                        sa.text("""
                            INSERT INTO contacts (
                                team_scope, contact_type, full_name, email, source,
                                truth_level, confidence, interaction_count, last_interaction_at
                            ) VALUES (
                                :ts, 'direct', :fn, :em, :sr, 'EPHEMERAL', 0.6, 1, now()
                            )
                            ON CONFLICT (team_scope, email) WHERE email IS NOT NULL DO UPDATE
                            SET interaction_count = contacts.interaction_count + 1,
                                last_interaction_at = now(),
                                full_name = COALESCE(EXCLUDED.full_name, contacts.full_name),
                                updated_at = now()
                        """),
                        {"ts": team_scope, "fn": name, "em": email, "sr": contact_source},
                    )
                else:
                    await session.execute(
                        sa.text("""
                            INSERT INTO contacts (
                                team_scope, contact_type, full_name, email, source,
                                truth_level, confidence, interaction_count, last_interaction_at
                            ) VALUES (
                                :ts, 'direct', :fn, NULL, :sr, 'EPHEMERAL', 0.4, 1, now()
                            )
                        """),
                        {"ts": team_scope, "fn": name, "sr": contact_source},
                    )
            await session.commit()
        log.info("crm.extract.done", team_scope=team_scope, count=len(people))
    except Exception as exc:
        log.warning("crm.extract_skipped", error=str(exc), team_scope=team_scope)


# ── Phase 7 — auto task creation (D5 trigger 2) ─────────────────────────


async def _maybe_create_task_from_action(item: MemoryItem, team_scope: str) -> None:
    """Auto-create a task if memory_item flags an action (metadata or regex). Fail-soft."""
    try:
        content = item.content or ""
        meta = item.metadata or {}
        flagged = (meta.get("contains_action") is True) or bool(_ACTION_RE.search(content))
        if not flagged:
            return

        # Short-circuit: bridge task_intent_detector already called Claude and stored
        # the extracted fields in metadata — reuse them to avoid a redundant second call.
        pre_title = meta.get("task_intent_title")
        pre_assignee = meta.get("task_intent_assignee_email")
        if pre_title:
            title = str(pre_title)[:512]
            description = None
            assignee_email = pre_assignee or None
        else:
            client = _get_anthropic()
            if client is None:
                log.warning("tasks.auto.no_anthropic_key", team_scope=team_scope)
                return

            msg = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=[
                    {
                        "type": "text",
                        "text": (
                            "Extract a single task definition from the text. Return STRICT JSON "
                            '(no markdown): {"title": "<concise title <80 chars>", '
                            '"description": "<details or null>", "assignee_email": "<email or null>"}. '
                            'If no clear actionable task, return {"title": null}.'
                        ),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": content[:8000]}],
            )
            text = msg.content[0].text.strip() if msg.content else "{}"
            if text.startswith("```"):
                text = text.split("```", 2)[1].lstrip("json").strip()
            import json as _json

            parsed = _json.loads(text)
            title = parsed.get("title")
            if not title:
                return
            description = parsed.get("description")
            assignee_email = parsed.get("assignee_email")

        source_kind = "agent" if (item.source or "").startswith("agent") else "chat"

        import sqlalchemy as sa

        async with async_session_factory() as session:
            # Auto-generated tasks: created_by = NULL (system-attribution, migration 0010 nullable).
            # NULL distinguishes system-generated tasks (agent, chat extraction) from user creates.
            assigned_to = None
            assignee_email_resolved = None
            if assignee_email:
                # Phase 11 (BMO-07) — never resolve a soft-deleted contact
                # as the assignee for an auto-generated task. A soft-deleted
                # contact often signals the human asked to be removed; we
                # must not silently re-attach them via background ingest.
                contact_row = (
                    await session.execute(
                        sa.text(
                            "SELECT id, email FROM contacts "
                            "WHERE team_scope = :ts AND email = :em "
                            "  AND deleted_at IS NULL"
                        ),
                        {"ts": team_scope, "em": assignee_email},
                    )
                ).fetchone()
                if contact_row:
                    assigned_to = contact_row.id
                    assignee_email_resolved = contact_row.email

            item_id_str = str(item.id) if hasattr(item, "id") and item.id else None
            row = (
                await session.execute(
                    sa.text("""
                        INSERT INTO tasks (
                            team_scope, title, description, status, priority,
                            assigned_to, created_by, source, source_ref
                        ) VALUES (
                            :ts, :ti, :de, 'todo', 'normal',
                            :at, NULL, :src, :sref
                        )
                        RETURNING id
                    """),
                    {
                        "ts": team_scope,
                        "ti": title[:512],
                        "de": description,
                        "at": str(assigned_to) if assigned_to else None,
                        "src": source_kind,
                        "sref": item_id_str,
                    },
                )
            ).fetchone()
            await session.commit()

        log.info(
            "tasks.auto_created",
            team_scope=team_scope,
            task_id=str(row.id),
            source=source_kind,
            assignee=assignee_email_resolved,
        )

        # Send notification email if assignee resolved
        if assignee_email_resolved:
            from app.services.notifications import send_task_notification_email

            asyncio.create_task(
                send_task_notification_email(
                    recipient_email=assignee_email_resolved,
                    task_title=title,
                    task_id=str(row.id),
                    team_scope=team_scope,
                    dashboard_url=None,
                )
            )
    except Exception as exc:
        log.warning("tasks.auto_skipped", error=str(exc), team_scope=team_scope)


router = APIRouter()


class UpsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: MemoryItem


class UpsertOut(BaseModel):
    id: str


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: dict[str, Any]


@router.post("/memory/upsert", status_code=201, response_model=UpsertOut)
async def upsert_item(
    body: UpsertBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    if body.item.team_scope != team_scope:
        raise HTTPException(400, "item.team_scope must match X-Team-Scope header")
    item_id = await provider.upsert(body.item)

    # Neo4j outbox INSERT — enqueue Cypher rows for each entity in metadata.entities.
    # Runs in the same SQLAlchemy transaction as the audit log (committed together below).
    # Threat T-03-05-02: entity_name and team_scope are passed as $params — never
    # interpolated into the Cypher string.
    entities = (body.item.metadata or {}).get("entities", [])
    if entities:
        for entity in entities:
            entity_name = entity.get("name", "")
            entity_type = entity.get("type", "concept")
            if not entity_name:
                continue
            # MERGE Entity node
            session.add(NeoOutboxEntry(
                cypher=(
                    "MERGE (e:Entity {name: $name, team_scope: $team_scope}) "
                    "ON CREATE SET e.type = $type"
                ),
                params={"name": entity_name, "type": entity_type, "team_scope": team_scope},
            ))
            # MERGE Fact node + MENTIONS edge
            session.add(NeoOutboxEntry(
                cypher=(
                    "MERGE (f:Fact {id: $fact_id, team_scope: $team_scope}) "
                    "MERGE (e:Entity {name: $entity_name, team_scope: $team_scope}) "
                    "MERGE (f)-[:MENTIONS]->(e)"
                ),
                params={
                    "fact_id": item_id,
                    "entity_name": entity_name,
                    "team_scope": team_scope,
                },
            ))

    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="memory.upsert",
        target_id=item_id,
        payload={"source": body.item.source, "truth_level": body.item.truth_level.value},
    )
    await session.commit()

    # Phase 5 plan 05-01: Enrich Graphiti graph with the new memory item.
    # create_task so we don't block the HTTP response — _enrich_with_graphiti is fail-soft.
    if body.item.content:
        asyncio.create_task(
            _enrich_with_graphiti(body.item.content, team_scope)
        )
        # Phase 7 — auto-extract contacts from text (D1)
        asyncio.create_task(
            _extract_crm_contacts(body.item.content, team_scope, body.item.source or "memory")
        )
        # Phase 7 — auto-create task from action items (D5 trigger 2)
        asyncio.create_task(
            _maybe_create_task_from_action(body.item, team_scope)
        )

    return UpsertOut(id=item_id)


@router.get("/memory/search", response_model=list[SearchHit])
async def search_memory(
    q: str = Query(..., min_length=1, max_length=500),
    project_scope: str | None = Query(default=None, max_length=64),
    truth_level_min: TruthLevel | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    return await provider.search(
        q,
        team_scope=team_scope,
        project_scope=project_scope,
        truth_level_min=truth_level_min,
        limit=limit,
    )


@router.get("/memory/{item_id}", response_model=MemoryItem)
async def get_item(
    item_id: str,
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    item = await provider.get(item_id, team_scope=team_scope)
    if item is None:
        raise HTTPException(404, "memory item not found in this team")
    return item


@router.patch("/memory/{item_id}", response_model=MemoryItem)
async def update_item(
    item_id: str,
    body: UpdateBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    # Phase 2 invariant: truth_level CANNOT be patched directly here — must go through promotions
    if "truth_level" in body.patch:
        raise HTTPException(
            405,
            "truth_level can only be changed via /v1/promotions workflow (4-eyes principle)",
        )
    try:
        updated = await provider.update(item_id, team_scope=team_scope, patch=body.patch)
    except KeyError:
        raise HTTPException(404, "memory item not found in this team")
    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="memory.update",
        target_id=item_id,
        payload={"patched_fields": list(body.patch.keys())},
    )
    await session.commit()
    return updated


@router.delete("/memory/{item_id}", status_code=204)
async def delete_item(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    await provider.delete(item_id, team_scope=team_scope)
    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="memory.delete",
        target_id=item_id,
        payload={},
    )
    await session.commit()


@router.get("/memory/{item_id}/history", response_model=list[MemoryItem])
async def get_history(
    item_id: str,
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    history = await provider.history(item_id, team_scope=team_scope)
    if not history:
        raise HTTPException(404, "memory item not found in this team")
    return history

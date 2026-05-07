"""/v1/admin/granola-integration + /v1/integrations/granola/ingest — D3 Granola integration."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from cryptography.fernet import Fernet, InvalidToken  # noqa: F401 (InvalidToken kept for callers)
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import settings
from app.deps import _is_admin, get_current_principal, get_session

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Helpers ─────────────────────────────────────────────────────────────

def _is_bridge(principal: dict[str, Any]) -> bool:
    """Identify internal service callers (granola-sync).

    Distinct from _is_admin: bridge check is for /ingest (service-to-service),
    not for admin configuration endpoints.
    """
    return principal.get("kind") == "bridge"


def _require_granola_fernet() -> Fernet:
    """Return a Fernet instance using FERNET_KEY, falling back to OAUTH_CREDENTIALS_ENCRYPTION_KEY.

    Raises HTTP 500 if both are empty — silent plaintext storage is never acceptable.
    """
    key = settings.FERNET_KEY or settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        raise HTTPException(500, "FERNET_KEY (or OAUTH_CREDENTIALS_ENCRYPTION_KEY) not configured")
    return Fernet(key.encode())


# ── Pydantic models ─────────────────────────────────────────────────────

class GranolaIntegrationCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_scope: str = Field(..., min_length=1, max_length=64)
    api_key: str = Field(..., min_length=8, max_length=512)


class GranolaIntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    team_scope: str
    last_polled_at: datetime | None
    configured: bool = True


class GranolaParticipantIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    email: str | None = None


class GranolaActionItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(..., min_length=1, max_length=512)
    description: str | None = None
    assignee_email: str | None = None
    due_date: str | None = None  # ISO date


class GranolaNoteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str | None = None
    summary_text: str | None = None
    summary_markdown: str | None = None
    web_url: str | None = None
    created_at: datetime | None = None
    attendees: list[GranolaParticipantIn] = Field(default_factory=list)


class GranolaExtractedIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    participants: list[GranolaParticipantIn] = Field(default_factory=list)
    action_items: list[GranolaActionItemIn] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    project_scope: str | None = Field(None, max_length=64)


class GranolaIngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_scope: str = Field(..., min_length=1, max_length=64)
    note: GranolaNoteIn
    extracted: GranolaExtractedIn


class GranolaIngestOut(BaseModel):
    memory_item_id: UUID
    contacts_upserted: int
    tasks_created: int
    decisions_logged: int


# ═══════════════════════════════════════════════════════════════════════
# Admin CRUD endpoints
# ═══════════════════════════════════════════════════════════════════════

@router.post("/admin/granola-integration", response_model=GranolaIntegrationOut, status_code=201)
async def create_granola_integration(
    body: GranolaIntegrationCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Create or replace a Granola API key integration for a team.

    The api_key is Fernet-encrypted before storage (T-07-04-01 mitigated).
    Uses UPSERT so a second call replaces the existing key atomically.
    Admin-only (T-07-04-05 mitigated).
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    fernet = _require_granola_fernet()
    encrypted = fernet.encrypt(body.api_key.encode()).decode()
    result = (await session.execute(sa.text("""
        INSERT INTO granola_integrations (team_scope, api_key_enc)
        VALUES (:ts, :key)
        ON CONFLICT (team_scope) DO UPDATE
        SET api_key_enc = EXCLUDED.api_key_enc,
            updated_at = now()
        RETURNING id, team_scope, last_polled_at
    """), {"ts": body.team_scope, "key": encrypted})).mappings().fetchone()
    user = principal.get("user")
    await write_audit(
        session,
        actor_user_id=user.id if user else None,
        team_scope=body.team_scope,
        action="granola.integration.upserted",
        target_id=str(result["id"]),
        payload={"team_scope": body.team_scope},
    )
    await session.commit()
    return GranolaIntegrationOut(
        id=result["id"],
        team_scope=result["team_scope"],
        last_polled_at=result["last_polled_at"],
        configured=True,
    )


@router.get("/admin/granola-integration", response_model=list[GranolaIntegrationOut])
async def list_granola_integrations(
    team_scope: str | None = Query(None, max_length=64),
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """List Granola integrations. Optionally filter by team_scope.

    Never returns api_key_enc — response model excludes it (T-07-04-02 mitigated).
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    sql = "SELECT id, team_scope, last_polled_at FROM granola_integrations"
    params: dict[str, Any] = {}
    if team_scope:
        sql += " WHERE team_scope = :ts"
        params["ts"] = team_scope
    sql += " ORDER BY created_at DESC"
    rows = (await session.execute(sa.text(sql), params)).mappings().all()
    return [GranolaIntegrationOut(**dict(r), configured=True) for r in rows]


@router.delete("/admin/granola-integration/{integration_id}", status_code=204)
async def delete_granola_integration(
    integration_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Delete a Granola integration by id. Admin-only."""
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    row = (await session.execute(
        sa.text("DELETE FROM granola_integrations WHERE id = :id RETURNING team_scope"),
        {"id": str(integration_id)},
    )).fetchone()
    if row is None:
        raise HTTPException(404, "integration not found")
    user = principal.get("user")
    await write_audit(
        session,
        actor_user_id=user.id if user else None,
        team_scope=row.team_scope,
        action="granola.integration.deleted",
        target_id=str(integration_id),
        payload={},
    )
    await session.commit()
    return None


# ═══════════════════════════════════════════════════════════════════════
# Ingest endpoint (called by granola-sync bridge service)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/integrations/granola/ingest", response_model=GranolaIngestOut, status_code=201)
async def ingest_granola_note(
    body: GranolaIngestBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Atomic ingest of a Granola meeting note.

    Called exclusively by the granola-sync bridge service (kind='bridge').
    Creates in a single transaction (atomicity):
      1. memory_item — meeting summary (source='granola', truth_level='WORKING')
      2. N contacts — meeting participants (upsert on email)
      3. M tasks — action items (source_ref → memory_item.id, created_by = NULL)

    Security:
      - Bridge JWT required (T-07-04-03 mitigated)
      - Cross-team forgery blocked: bridge JWT team_scope must match body (T-07-04-04)
      - Dedup: duplicate note_id skipped idempotently (T-07-04-12 mitigated)
      - Audit: granola.ingest action logged with counts (T-07-04-11 mitigated)
    """
    if not _is_bridge(principal):
        raise HTTPException(403, "Bridge JWT required (granola-sync only)")

    # Bridge JWT cross-team check (T-07-04-04)
    bridge_team = principal.get("team_scope")
    if bridge_team and bridge_team != body.team_scope:
        raise HTTPException(
            403,
            f"Bridge JWT team_scope '{bridge_team}' does not match payload team_scope '{body.team_scope}'",
        )

    note = body.note
    extracted = body.extracted

    # 0) Note-level idempotency check — deduplicate retries / polling overlaps (T-07-04-12)
    existing = (await session.execute(sa.text("""
        SELECT id FROM memory_items
        WHERE source_ref = :note_id AND team_scope = :ts AND source = 'granola'
        LIMIT 1
    """), {"note_id": note.id, "ts": body.team_scope})).fetchone()
    if existing is not None:
        log.info(
            "granola.ingest.duplicate_skipped",
            team_scope=body.team_scope,
            note_id=note.id,
            existing_memory_item=str(existing.id),
        )
        return GranolaIngestOut(
            memory_item_id=existing.id,
            contacts_upserted=0,
            tasks_created=0,
            decisions_logged=0,
        )

    # 1) Insert memory_item (Granola meeting summary)
    summary_content = note.summary_text or note.summary_markdown or note.title or "(empty Granola note)"
    metadata = {
        "granola_note_id": note.id,
        "title": note.title,
        "web_url": note.web_url,
        "attendees": [{"name": a.name, "email": a.email} for a in note.attendees],
        "decisions": extracted.decisions,
    }
    memory_item_id = uuid4()
    await session.execute(sa.text("""
        INSERT INTO memory_items (
            id, team_scope, project_scope, content, source, source_ref,
            truth_level, confidence, visibility, validation_status, metadata
        ) VALUES (
            :id, :ts, :ps, :content, 'granola', :sref,
            'WORKING', 0.9, 'team', 'pending', :meta::jsonb
        )
    """), {
        "id": str(memory_item_id),
        "ts": body.team_scope,
        "ps": extracted.project_scope,
        "content": summary_content,
        "sref": note.id,
        "meta": json.dumps(metadata),
    })

    # 2) Upsert contacts (meeting participants)
    contacts_upserted = 0
    contact_email_to_id: dict[str, UUID] = {}
    for p in extracted.participants:
        if not (p.email or p.name):
            continue
        if p.email:
            # Upsert on (team_scope, email) — increment interaction count (active contact signal)
            row = (await session.execute(sa.text("""
                INSERT INTO contacts (
                    team_scope, contact_type, full_name, email, source,
                    truth_level, confidence, project_scope, interaction_count, last_interaction_at
                ) VALUES (
                    :ts, 'direct', :fn, :em, 'granola',
                    'WORKING', 0.9, :ps, 1, now()
                )
                ON CONFLICT (team_scope, email) WHERE email IS NOT NULL DO UPDATE
                SET interaction_count = contacts.interaction_count + 1,
                    last_interaction_at = now(),
                    full_name = COALESCE(EXCLUDED.full_name, contacts.full_name),
                    truth_level = CASE
                        WHEN contacts.truth_level = 'EPHEMERAL' THEN 'WORKING'
                        ELSE contacts.truth_level
                    END,
                    updated_at = now()
                RETURNING id, email
            """), {
                "ts": body.team_scope,
                "fn": p.name,
                "em": p.email,
                "ps": extracted.project_scope,
            })).fetchone()
            contact_email_to_id[p.email] = row.id
        else:
            # Name-only participant — no email, lower confidence, no dedup possible
            await session.execute(sa.text("""
                INSERT INTO contacts (
                    team_scope, contact_type, full_name, email, source,
                    truth_level, confidence, project_scope, interaction_count, last_interaction_at
                ) VALUES (
                    :ts, 'direct', :fn, NULL, 'granola',
                    'WORKING', 0.5, :ps, 1, now()
                )
            """), {"ts": body.team_scope, "fn": p.name, "ps": extracted.project_scope})
        contacts_upserted += 1

    # 3) Insert tasks (action items)
    # created_by = NULL: migration 0010 made this column nullable specifically for system-generated tasks.
    # NULL signals auto-generation (Granola ingest, agent output) vs real user creates.
    # Audit trail preserved via source='granola' + source_ref + audit_log entry below.
    tasks_created = 0
    for ai in extracted.action_items:
        assignee_id = contact_email_to_id.get(ai.assignee_email or "")
        await session.execute(sa.text("""
            INSERT INTO tasks (
                team_scope, project_scope, title, description, status, priority,
                assigned_to, created_by, source, source_ref
            ) VALUES (
                :ts, :ps, :title, :desc, 'todo', 'normal',
                :at, NULL, 'granola', :sref
            )
        """), {
            "ts": body.team_scope,
            "ps": extracted.project_scope,
            "title": ai.title,
            "desc": ai.description,
            "at": str(assignee_id) if assignee_id else None,
            "sref": str(memory_item_id),
        })
        tasks_created += 1

    # 4) Audit — bridge actor (no user_id for system-generated content)
    await write_audit(
        session,
        actor_user_id=None,
        team_scope=body.team_scope,
        action="granola.ingest",
        target_id=note.id,
        payload={
            "memory_item_id": str(memory_item_id),
            "contacts_upserted": contacts_upserted,
            "tasks_created": tasks_created,
            "decisions_logged": len(extracted.decisions),
        },
    )

    # Single commit — all writes atomic (memory_item + contacts + tasks)
    await session.commit()

    return GranolaIngestOut(
        memory_item_id=memory_item_id,
        contacts_upserted=contacts_upserted,
        tasks_created=tasks_created,
        decisions_logged=len(extracted.decisions),
    )

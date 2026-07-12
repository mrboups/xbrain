"""/v1/crm — CRM contacts CRUD (D1, D2). Core in every edition."""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.deps import _user_id_from_principal, get_current_principal, get_session, require_paid_tier

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────

class ContactCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_type: str = Field(..., pattern=r"^(direct|mass)$")
    full_name: str | None = Field(None, max_length=256)
    email: str | None = Field(None, max_length=256)
    company: str | None = Field(None, max_length=256)
    role: str | None = Field(None, max_length=256)
    project_scope: str | None = Field(None, max_length=64)
    source: str = Field(..., min_length=1, max_length=128)
    truth_level: str = Field(default="EPHEMERAL", pattern=r"^(EPHEMERAL|WORKING|VALIDATED|CANONICAL|PUBLIC)$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    list_source: str | None = Field(None, max_length=256)
    opt_in_status: str | None = Field(default="unknown", pattern=r"^(unknown|opted_in|opted_out)$")


class ContactPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = None
    email: str | None = None
    company: str | None = None
    role: str | None = None
    project_scope: str | None = None
    truth_level: str | None = Field(None, pattern=r"^(EPHEMERAL|WORKING|VALIDATED|CANONICAL|PUBLIC)$")
    opt_in_status: str | None = Field(None, pattern=r"^(unknown|opted_in|opted_out)$")


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: UUID
    team_scope: str
    contact_type: str
    full_name: str | None
    email: str | None
    company: str | None
    role: str | None
    project_scope: str | None
    truth_level: str
    confidence: float
    source: str
    list_source: str | None
    opt_in_status: str | None
    interaction_count: int


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/crm/contacts", response_model=list[ContactOut])
async def list_contacts(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
    contact_type: str | None = Query(None, pattern=r"^(direct|mass)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    # Phase 11 (BMO-07) — hide soft-deleted contacts from the default list.
    # The Brain Monitor (11-04 `GET /v1/brain/events`) is the single surface
    # that opts into deleted rows via `?include_deleted=true`.
    sql = "SELECT * FROM contacts WHERE team_scope = :ts AND deleted_at IS NULL"
    params: dict[str, Any] = {"ts": team_scope}
    if contact_type:
        sql += " AND contact_type = :ct"
        params["ct"] = contact_type
    sql += " ORDER BY last_interaction_at DESC NULLS LAST, created_at DESC LIMIT :lim OFFSET :off"
    params["lim"] = limit
    params["off"] = offset
    rows = (await session.execute(sa.text(sql), params)).mappings().all()
    return [ContactOut(**dict(r)) for r in rows]


@router.get("/crm/contacts/{contact_id}", response_model=ContactOut)
async def get_contact(
    contact_id: UUID,
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    # Phase 11 (BMO-07) — soft-deleted contacts 404 here. The brain monitor
    # surfaces them via `/v1/brain/events/contact/{id}` instead.
    row = (await session.execute(
        sa.text(
            "SELECT * FROM contacts "
            "WHERE id = :id AND team_scope = :ts AND deleted_at IS NULL"
        ),
        {"id": str(contact_id), "ts": team_scope},
    )).mappings().fetchone()
    if row is None:
        raise HTTPException(404, "contact not found in this team")
    return ContactOut(**dict(row))


@router.post("/crm/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    body: ContactCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    user_id = _user_id_from_principal(principal)
    if body.email:
        # Upsert on (team_scope, email) — increments interaction_count on conflict
        result = (await session.execute(sa.text("""
            INSERT INTO contacts (
                team_scope, contact_type, full_name, email, company, role,
                project_scope, truth_level, confidence, source,
                list_source, opt_in_status, interaction_count, last_interaction_at
            ) VALUES (
                :ts, :ct, :fn, :em, :co, :ro,
                :ps, :tl, :cf, :sr,
                :ls, :ois, 1, now()
            )
            ON CONFLICT (team_scope, email) WHERE email IS NOT NULL DO UPDATE
            SET interaction_count = contacts.interaction_count + 1,
                last_interaction_at = now(),
                full_name = COALESCE(EXCLUDED.full_name, contacts.full_name),
                company = COALESCE(EXCLUDED.company, contacts.company),
                role = COALESCE(EXCLUDED.role, contacts.role),
                truth_level = CASE
                    WHEN EXCLUDED.truth_level = 'WORKING' AND contacts.truth_level = 'EPHEMERAL'
                    THEN 'WORKING' ELSE contacts.truth_level
                END,
                updated_at = now()
            RETURNING *
        """), {
            "ts": team_scope, "ct": body.contact_type, "fn": body.full_name,
            "em": body.email, "co": body.company, "ro": body.role,
            "ps": body.project_scope, "tl": body.truth_level, "cf": body.confidence,
            "sr": body.source, "ls": body.list_source, "ois": body.opt_in_status,
        })).mappings().fetchone()
    else:
        # No email — straight INSERT (no dedup key available in v1)
        result = (await session.execute(sa.text("""
            INSERT INTO contacts (
                team_scope, contact_type, full_name, email, company, role,
                project_scope, truth_level, confidence, source,
                list_source, opt_in_status, interaction_count, last_interaction_at
            ) VALUES (
                :ts, :ct, :fn, NULL, :co, :ro,
                :ps, :tl, :cf, :sr,
                :ls, :ois, 1, now()
            )
            RETURNING *
        """), {
            "ts": team_scope, "ct": body.contact_type, "fn": body.full_name,
            "co": body.company, "ro": body.role, "ps": body.project_scope,
            "tl": body.truth_level, "cf": body.confidence, "sr": body.source,
            "ls": body.list_source, "ois": body.opt_in_status,
        })).mappings().fetchone()

    await write_audit(
        session,
        actor_user_id=user_id,
        team_scope=team_scope,
        action="crm.contact.created",
        target_id=str(result["id"]),
        payload={"source": result["source"], "type": result["contact_type"]},
    )
    await session.commit()
    return ContactOut(**dict(result))


@router.patch("/crm/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: UUID,
    body: ContactPatchBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = str(contact_id)
    fields["ts"] = team_scope
    result = (await session.execute(sa.text(
        f"UPDATE contacts SET {set_clause}, updated_at = now() "
        f"WHERE id = :id AND team_scope = :ts RETURNING *"
    ), fields)).mappings().fetchone()
    if result is None:
        raise HTTPException(404, "contact not found in this team")
    await write_audit(
        session,
        actor_user_id=_user_id_from_principal(principal),
        team_scope=team_scope,
        action="crm.contact.updated",
        target_id=str(contact_id),
        payload={"fields": list(body.model_dump(exclude_unset=True).keys())},
    )
    await session.commit()
    return ContactOut(**dict(result))


@router.delete("/crm/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    result = await session.execute(sa.text(
        "DELETE FROM contacts WHERE id = :id AND team_scope = :ts RETURNING id"
    ), {"id": str(contact_id), "ts": team_scope})
    if result.fetchone() is None:
        raise HTTPException(404, "contact not found in this team")
    await write_audit(
        session,
        actor_user_id=_user_id_from_principal(principal),
        team_scope=team_scope,
        action="crm.contact.deleted",
        target_id=str(contact_id),
        payload={},
    )
    await session.commit()
    return None

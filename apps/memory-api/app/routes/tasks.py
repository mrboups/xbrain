"""/v1/tasks — task tracking CRUD (paid tier only — D4, D6)."""

import asyncio
from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.deps import _user_id_from_principal, get_current_principal, get_session, require_paid_tier
from app.services.notifications import send_task_notification_email

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────

class TaskCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=512)
    description: str | None = None
    status: str = Field(default="todo", pattern=r"^(todo|in_progress|done|cancelled)$")
    priority: str = Field(default="normal", pattern=r"^(low|normal|high|urgent)$")
    due_date: date | None = None
    assigned_to: UUID | None = None
    source: str = Field(..., pattern=r"^(granola|agent|chat|manual)$")
    source_ref: UUID | None = None
    project_scope: str | None = Field(None, max_length=64)


class TaskPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=512)
    description: str | None = None
    status: str | None = Field(None, pattern=r"^(todo|in_progress|done|cancelled)$")
    priority: str | None = Field(None, pattern=r"^(low|normal|high|urgent)$")
    due_date: date | None = None
    assigned_to: UUID | None = None
    project_scope: str | None = Field(None, max_length=64)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: UUID
    team_scope: str
    project_scope: str | None
    title: str
    description: str | None
    status: str
    priority: str
    due_date: date | None
    assigned_to: UUID | None
    created_by: UUID
    source: str
    source_ref: UUID | None
    created_at: datetime
    updated_at: datetime


# ── Helpers ─────────────────────────────────────────────────────────────
# (note: `_user_id_from_principal` is imported from app.deps — defined there in 07-02 Task 1)


async def _validate_assignee(
    session: AsyncSession, contact_id: UUID | None, team_scope: str
) -> None:
    if contact_id is None:
        return
    row = (await session.execute(
        sa.text("SELECT 1 FROM contacts WHERE id = :id AND team_scope = :ts"),
        {"id": str(contact_id), "ts": team_scope},
    )).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"assigned_to contact {contact_id} not found in team {team_scope}",
        )


# ── Endpoints ───────────────────────────────────────────────────────────

@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
    status: str | None = Query(None, pattern=r"^(todo|in_progress|done|cancelled)$"),
    assigned_to: UUID | None = Query(None),
    project_scope: str | None = Query(None, max_length=64),
    since: datetime | None = Query(None, description="ISO 8601 — return tasks with updated_at > since"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    sql = "SELECT * FROM tasks WHERE team_scope = :ts"
    params: dict[str, Any] = {"ts": team_scope}
    if status:
        sql += " AND status = :st"
        params["st"] = status
    if assigned_to:
        sql += " AND assigned_to = :at"
        params["at"] = str(assigned_to)
    if project_scope:
        sql += " AND project_scope = :ps"
        params["ps"] = project_scope
    if since:
        sql += " AND updated_at > :since"
        params["since"] = since
    sql += " ORDER BY updated_at DESC LIMIT :lim OFFSET :off"
    params["lim"] = limit
    params["off"] = offset
    rows = (await session.execute(sa.text(sql), params)).mappings().all()
    return [TaskOut(**dict(r)) for r in rows]


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    row = (await session.execute(
        sa.text("SELECT * FROM tasks WHERE id = :id AND team_scope = :ts"),
        {"id": str(task_id), "ts": team_scope},
    )).mappings().fetchone()
    if row is None:
        raise HTTPException(404, "task not found in this team")
    return TaskOut(**dict(row))


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    user_id = _user_id_from_principal(principal)
    if user_id is None:
        # Bridge JWT (e.g. granola-sync) — must provide created_by via principal kind=service
        # For now, reject; bridge-originated tasks should use a dedicated internal endpoint
        raise HTTPException(401, "user identity required for task creation")
    await _validate_assignee(session, body.assigned_to, team_scope)
    result = (await session.execute(sa.text("""
        INSERT INTO tasks (
            team_scope, project_scope, title, description, status, priority,
            due_date, assigned_to, created_by, source, source_ref
        ) VALUES (
            :ts, :ps, :title, :desc, :status, :prio,
            :due, :at, :cb, :src, :sref
        )
        RETURNING *
    """), {
        "ts": team_scope, "ps": body.project_scope, "title": body.title,
        "desc": body.description, "status": body.status, "prio": body.priority,
        "due": body.due_date,
        "at": str(body.assigned_to) if body.assigned_to else None,
        "cb": str(user_id),
        "src": body.source,
        "sref": str(body.source_ref) if body.source_ref else None,
    })).mappings().fetchone()
    await write_audit(
        session,
        actor_user_id=user_id,
        team_scope=team_scope,
        action="task.created",
        target_id=str(result["id"]),
        payload={
            "source": body.source,
            "assigned_to": str(body.assigned_to) if body.assigned_to else None,
            "title": body.title,
        },
    )
    await session.commit()

    # Phase 7 — notify assignee (D6, fail-soft via asyncio.create_task)
    if body.assigned_to:
        contact = (
            await session.execute(
                sa.text("SELECT email FROM contacts WHERE id = :id AND team_scope = :ts"),
                {"id": str(body.assigned_to), "ts": team_scope},
            )
        ).fetchone()
        if contact and contact.email:
            asyncio.create_task(
                send_task_notification_email(
                    recipient_email=contact.email,
                    task_title=body.title,
                    task_id=str(result["id"]),
                    team_scope=team_scope,
                    dashboard_url=None,
                )
            )

    return TaskOut(**dict(result))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: UUID,
    body: TaskPatchBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "no fields to update")

    # Read current to compare status and assigned_to
    current = (await session.execute(
        sa.text("SELECT status, assigned_to FROM tasks WHERE id = :id AND team_scope = :ts"),
        {"id": str(task_id), "ts": team_scope},
    )).fetchone()
    if current is None:
        raise HTTPException(404, "task not found in this team")

    # Validate assignee if changing
    if "assigned_to" in fields:
        await _validate_assignee(session, fields["assigned_to"], team_scope)
        if fields["assigned_to"] is not None:
            fields["assigned_to"] = str(fields["assigned_to"])

    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = str(task_id)
    fields["ts"] = team_scope

    result = (await session.execute(sa.text(
        f"UPDATE tasks SET {set_clause}, updated_at = now() "
        f"WHERE id = :id AND team_scope = :ts RETURNING *"
    ), fields)).mappings().fetchone()

    new_status = body.status
    audit_action = (
        "task.status_changed"
        if (new_status is not None and new_status != current.status)
        else "task.updated"
    )
    await write_audit(
        session,
        actor_user_id=_user_id_from_principal(principal),
        team_scope=team_scope,
        action=audit_action,
        target_id=str(task_id),
        payload={
            "fields": list(body.model_dump(exclude_unset=True).keys()),
            "from_status": current.status if audit_action == "task.status_changed" else None,
            "to_status": new_status if audit_action == "task.status_changed" else None,
        },
    )
    await session.commit()

    # Phase 7 — notify new assignee if assigned_to changed (D6, fail-soft)
    body_assigned = body.model_dump(exclude_unset=True).get("assigned_to")
    if body_assigned is not None and str(body_assigned) != str(current.assigned_to or ""):
        contact = (
            await session.execute(
                sa.text("SELECT email FROM contacts WHERE id = :id AND team_scope = :ts"),
                {"id": str(body_assigned), "ts": team_scope},
            )
        ).fetchone()
        if contact and contact.email:
            asyncio.create_task(
                send_task_notification_email(
                    recipient_email=contact.email,
                    task_title=result["title"],
                    task_id=str(task_id),
                    team_scope=team_scope,
                    dashboard_url=None,
                )
            )

    return TaskOut(**dict(result))


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),
):
    result = await session.execute(sa.text(
        "DELETE FROM tasks WHERE id = :id AND team_scope = :ts RETURNING id"
    ), {"id": str(task_id), "ts": team_scope})
    if result.fetchone() is None:
        raise HTTPException(404, "task not found in this team")
    await write_audit(
        session,
        actor_user_id=_user_id_from_principal(principal),
        team_scope=team_scope,
        action="task.deleted",
        target_id=str(task_id),
        payload={},
    )
    await session.commit()
    return None

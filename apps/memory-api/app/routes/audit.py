"""/v1/audit — list audit entries scoped to a team (admin only)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import is_admin
from app.deps import get_current_principal, get_session, get_team_scope
from app.models.audit import AuditLog

router = APIRouter()


class AuditOut(BaseModel):
    id: int
    ts: str
    actor_user_id: str | None
    team_scope: str | None
    action: str
    target_id: str | None
    payload: dict[str, Any]


@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    limit: int = Query(default=100, le=1000),
):
    if principal["kind"] != "user" or not is_admin(principal["user"].source_user_id):
        raise HTTPException(403, "admin-only endpoint")
    stmt = (
        select(AuditLog)
        .where(AuditLog.team_scope == team_scope)
        .order_by(desc(AuditLog.ts))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        AuditOut(
            id=row.id,
            ts=row.ts.isoformat(),
            actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
            team_scope=row.team_scope,
            action=row.action,
            target_id=row.target_id,
            payload=row.payload,
        )
        for row in result.scalars().all()
    ]

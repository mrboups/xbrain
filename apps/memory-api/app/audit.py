"""Audit log helper — writes to audit_log table for every mutation."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    actor_user_id: UUID | None,
    team_scope: str | None,
    action: str,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert an audit_log row. Caller is responsible for commit."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        team_scope=team_scope,
        action=action,
        target_id=target_id,
        payload=payload or {},
    )
    session.add(entry)

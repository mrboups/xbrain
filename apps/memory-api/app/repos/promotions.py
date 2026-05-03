"""Promotions repository — all queries go through this layer.

team_scope is a REQUIRED kwarg on every read/write — there is no overload
that allows omission. This is the team-isolation invariant from Phase 1
applied to the new Phase 2 promotions table.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.promotion import Promotion


async def get_by_id(
    session: AsyncSession, *, promotion_id: UUID, team_scope: str
) -> Promotion | None:
    stmt = select(Promotion).where(
        Promotion.id == promotion_id,
        Promotion.team_scope == team_scope,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_team(
    session: AsyncSession,
    *,
    team_scope: str,
    status: str | None = None,
    limit: int = 50,
) -> list[Promotion]:
    stmt = select(Promotion).where(Promotion.team_scope == team_scope)
    if status is not None:
        stmt = stmt.where(Promotion.status == status)
    stmt = stmt.order_by(Promotion.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def list_for_item(
    session: AsyncSession, *, item_id: UUID, team_scope: str
) -> list[Promotion]:
    stmt = (
        select(Promotion)
        .where(Promotion.memory_item_id == item_id, Promotion.team_scope == team_scope)
        .order_by(Promotion.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())

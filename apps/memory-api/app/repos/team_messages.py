"""team_messages repository — insert + history pagination.

Quick task 260512-tcr. Read-side queries always filter by team_id +
deleted_at IS NULL so soft-deleted messages stay hidden until Phase 2
exposes them via a `?include_deleted=true` admin flag.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team_message import TeamMessage


async def insert_user_message(
    session: AsyncSession,
    *,
    team_id: UUID,
    author_user_id: UUID,
    content: str,
    parent_message_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> TeamMessage:
    """Insert a user-authored message. Caller is responsible for committing."""
    msg = TeamMessage(
        team_id=team_id,
        author_user_id=author_user_id,
        kind="user",
        content=content,
        parent_message_id=parent_message_id,
        metadata_=metadata or {},
    )
    session.add(msg)
    await session.flush()
    await session.refresh(msg)
    return msg


async def insert_agent_message(
    session: AsyncSession,
    *,
    team_id: UUID,
    agent_name: str,
    content: str,
    routed_via: str,
    parent_message_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> TeamMessage:
    """Insert an agent-authored message (Claude / future GPT, Grok, etc.).

    routed_via must be 'user_promax' or 'team_api' to surface provenance
    in the UI bubble.
    """
    msg = TeamMessage(
        team_id=team_id,
        author_user_id=None,
        agent_name=agent_name,
        kind="agent",
        content=content,
        routed_via=routed_via,
        parent_message_id=parent_message_id,
        metadata_=metadata or {},
    )
    session.add(msg)
    await session.flush()
    await session.refresh(msg)
    return msg


async def list_messages(
    session: AsyncSession,
    *,
    team_id: UUID,
    before_created_at: datetime | None = None,
    limit: int = 50,
) -> list[TeamMessage]:
    """Return the latest `limit` messages for a team, newest first.

    When `before_created_at` is set, returns the latest `limit` messages
    older than that timestamp — used by the extension's scroll-up loader.
    """
    stmt = select(TeamMessage).where(
        and_(
            TeamMessage.team_id == team_id,
            TeamMessage.deleted_at.is_(None),
        )
    )
    if before_created_at is not None:
        stmt = stmt.where(TeamMessage.created_at < before_created_at)
    stmt = stmt.order_by(desc(TeamMessage.created_at)).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_recent_messages_chronological(
    session: AsyncSession,
    *,
    team_id: UUID,
    limit: int = 20,
) -> list[TeamMessage]:
    """Return the latest `limit` messages oldest-first.

    Used by the agent context bundle so Claude sees the chat in natural
    reading order (oldest at top, the user's question at the bottom).
    """
    stmt = (
        select(TeamMessage)
        .where(
            and_(
                TeamMessage.team_id == team_id,
                TeamMessage.deleted_at.is_(None),
            )
        )
        .order_by(desc(TeamMessage.created_at))
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()  # newest-first → oldest-first
    return rows

"""Conversation repo. team_scope is ALWAYS a required parameter — never optional."""

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


async def create_conversation(
    session: AsyncSession,
    *,
    team_scope: str,
    project_scope: str | None,
    owner_user_id: UUID,
    title: str | None,
    source: str,
) -> Conversation:
    conv = Conversation(
        team_scope=team_scope,
        project_scope=project_scope,
        owner_user_id=owner_user_id,
        title=title,
        source=source,
    )
    session.add(conv)
    await session.flush()
    return conv


async def list_conversations(
    session: AsyncSession,
    *,
    team_scope: str,
    owner_user_id: UUID | None = None,
    limit: int = 50,
) -> list[Conversation]:
    """List conversations scoped to a team. team_scope is REQUIRED — no default."""
    stmt = select(Conversation).where(Conversation.team_scope == team_scope)
    if owner_user_id is not None:
        stmt = stmt.where(Conversation.owner_user_id == owner_user_id)
    stmt = stmt.order_by(desc(Conversation.created_at)).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    team_scope: str,
) -> Conversation | None:
    """Return a conversation only if it belongs to team_scope. team_scope REQUIRED."""
    result = await session.execute(
        select(Conversation).where(
            (Conversation.id == conversation_id) & (Conversation.team_scope == team_scope)
        )
    )
    return result.scalar_one_or_none()

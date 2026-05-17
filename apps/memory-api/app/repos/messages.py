"""Message repo. team_scope is ALWAYS a required parameter — never optional."""

from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.models.tagging import TaggingContract


async def create_message(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    tagging: TaggingContract,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        team_scope=tagging.team_scope,
        project_scope=tagging.project_scope,
        visibility=tagging.visibility.value,
        confidence=tagging.confidence,
        truth_level=tagging.truth_level.value,
        source=tagging.source,
        validation_status=tagging.validation_status.value,
        role=role,
        content=content,
        msg_metadata=metadata or {},
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_messages(
    session: AsyncSession,
    *,
    team_scope: str,
    conversation_id: UUID | None = None,
    q: str | None = None,
    limit: int = 50,
) -> list[Message]:
    """Filter messages by team_scope (REQUIRED). Optional convo + full-text search.

    SRCH-01/02 satisfied via Postgres tsvector on `content_tsv` (full-text, language-agnostic
    'simple' analyzer — Phase 2 will move to Qdrant vector search via mem0).

    Phase 11 (BMO-07): soft-deleted messages are hidden from this list. The
    Brain Monitor (`GET /v1/brain/events?entity_type=message`) is the only
    surface that may opt-in via `?include_deleted=true`. Plan-check Iter 1
    blocker B-3 — adding `routes/messages.py` coverage closes the regression
    surface flagged in 11-RESEARCH §Q8 as "Likely missing, MEDIUM risk".
    """
    stmt = select(Message).where(
        Message.team_scope == team_scope,
        Message.deleted_at.is_(None),
    )
    if conversation_id is not None:
        stmt = stmt.where(Message.conversation_id == conversation_id)
    if q:
        # tsvector search via to_tsquery — escape with plainto_tsquery for user-supplied input
        stmt = stmt.where(
            func.plainto_tsquery("simple", q).op("@@")(Message.content_tsv)
            if hasattr(Message, "content_tsv")
            else Message.content.ilike(f"%{q}%")
        )
    stmt = stmt.order_by(desc(Message.created_at)).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_message(
    session: AsyncSession,
    *,
    message_id: UUID,
    team_scope: str,
) -> Message | None:
    """Return message only if team_scope matches. team_scope REQUIRED.

    Phase 11 (BMO-07): soft-deleted messages return None here. The brain
    monitor reaches them through `app.repos.brain.fetch_event_row`.
    """
    result = await session.execute(
        select(Message).where(
            (Message.id == message_id)
            & (Message.team_scope == team_scope)
            & (Message.deleted_at.is_(None))
        )
    )
    return result.scalar_one_or_none()

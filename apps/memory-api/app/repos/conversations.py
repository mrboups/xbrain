"""Conversation repo. team_scope is ALWAYS a required parameter — never optional."""

import sqlalchemy as sa
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
    """List conversations scoped to a team. team_scope is REQUIRED — no default.

    Phase 11 (BMO-07): soft-deleted conversations are hidden from the default
    list. The Brain Monitor (`GET /v1/brain/events`) is the single surface
    that may opt-in via `?include_deleted=true`. Read-paths outside
    `routes/brain.py` must stay default-clean.
    """
    stmt = select(Conversation).where(
        Conversation.team_scope == team_scope,
        Conversation.deleted_at.is_(None),
    )
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
    """Return a conversation only if it belongs to team_scope. team_scope REQUIRED.

    Phase 11 (BMO-07): soft-deleted conversations return None here so the
    caller (`POST /v1/messages` upsert path; future GET endpoints) treats
    them as gone. The brain monitor mutation endpoints reach soft-deleted
    rows through `app.repos.brain.fetch_event_row` instead.
    """
    result = await session.execute(
        select(Conversation).where(
            (Conversation.id == conversation_id)
            & (Conversation.team_scope == team_scope)
            & (Conversation.deleted_at.is_(None))
        )
    )
    return result.scalar_one_or_none()


async def upsert_conversation_silent(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    team_scope: str,
    owner_user_id: UUID,
    source: str,
    project_scope: str | None = None,
) -> None:
    """INSERT conversation row silently — ON CONFLICT (id) DO NOTHING.

    Called by POST /v1/messages when conversation_id is unknown (pipeline OWUI pattern).
    Idempotent: concurrent upserts with the same UUID v5 result in exactly one row.
    title is intentionally NULL — the pipeline does not know the conversation title
    at message-send time. Admin or LLM summary can backfill later.
    """
    await session.execute(
        sa.text(
            """
            INSERT INTO conversations (id, team_scope, project_scope, owner_user_id, source, title, created_at)
            VALUES (:id, :team_scope, :project_scope, :owner_user_id, :source, NULL, now())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": conversation_id,
            "team_scope": team_scope,
            "project_scope": project_scope,
            "owner_user_id": owner_user_id,
            "source": source,
        },
    )

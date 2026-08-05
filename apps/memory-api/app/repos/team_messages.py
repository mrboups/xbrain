"""team_messages repository — insert + history pagination + soft delete.

Quick task 260512-tcr. Read-side queries always filter by team_id +
deleted_at IS NULL so soft-deleted messages stay hidden until Phase 2
exposes them via a `?include_deleted=true` admin flag.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, func, select, update
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
    after_created_at: datetime | None = None,
    limit: int = 50,
) -> list[TeamMessage]:
    """Return the latest `limit` messages for a team, newest first.

    When `before_created_at` is set, returns the latest `limit` messages
    older than that timestamp — used by the extension's scroll-up loader.

    When `after_created_at` is set, returns messages NEWER than that timestamp
    (`created_at > after_created_at`) — the "since last visit" window used by
    Catch me up (Phase 23, D-23-02); symmetric to `before_created_at`. Ordering
    is UNCHANGED (still newest-first); callers that need chronological order
    reverse client-side, as get_recent_messages_chronological does. `before_`
    and `after_` may be combined to bound both ends of a window.
    """
    stmt = select(TeamMessage).where(
        and_(
            TeamMessage.team_id == team_id,
            TeamMessage.deleted_at.is_(None),
        )
    )
    if before_created_at is not None:
        stmt = stmt.where(TeamMessage.created_at < before_created_at)
    if after_created_at is not None:
        stmt = stmt.where(TeamMessage.created_at > after_created_at)
    stmt = stmt.order_by(desc(TeamMessage.created_at)).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_live_message(
    session: AsyncSession,
    *,
    team_id: UUID,
    message_id: UUID,
) -> TeamMessage | None:
    """Fetch ONE not-yet-deleted message, scoped to `team_id`.

    The team predicate is not a convenience — it is the isolation boundary. A
    message id is a bare UUID in the URL, and without `team_id` in the WHERE
    clause a member of team A holding an id from team B would reach that row.
    Returning None for "wrong team", "no such id" and "already deleted" alike
    keeps the route's 404 free of an existence oracle.
    """
    stmt = select(TeamMessage).where(
        and_(
            TeamMessage.id == message_id,
            TeamMessage.team_id == team_id,
            TeamMessage.deleted_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def soft_delete_message(
    session: AsyncSession,
    *,
    team_id: UUID,
    message_id: UUID,
    deleted_by: UUID | None,
) -> datetime | None:
    """Set `deleted_at` + `deleted_by` on one live message. Returns the stamp.

    Returns None when nothing was updated — the row belongs to another team, does
    not exist, or a concurrent request deleted it first. The caller turns that
    into a 404 rather than reporting a success that did not happen.

    `deleted_at IS NULL` in the WHERE clause is what makes a double DELETE
    idempotent-but-honest: the second one changes nothing and says so, instead of
    overwriting the first deleter's identity and timestamp.

    The stamp comes from the PYTHON clock, not `NOW()`, for the reason
    `repos/brain.py:soft_delete_entity` documents: the same wall-clock value is
    handed to Qdrant for the linked memory items, and a PG/Qdrant skew on the
    soft-delete moment is what leaves the janitor chasing ghost rows.

    Caller commits.
    """
    now = datetime.now(tz=timezone.utc)
    stmt = (
        update(TeamMessage)
        .where(
            and_(
                TeamMessage.id == message_id,
                TeamMessage.team_id == team_id,
                TeamMessage.deleted_at.is_(None),
            )
        )
        .values(deleted_at=now, deleted_by=deleted_by)
        .returning(TeamMessage.id)
    )
    result = await session.execute(stmt)
    return now if result.first() is not None else None


#: What a starred message row carries. `team_messages.truth_level` already exists
#: (migration 0017) with the canonical five-value CHECK, so a star needs no new
#: column and no migration — the level IS the star.
STAR_LEVEL = "CANONICAL"
#: Where an un-starred message goes back to. Nothing else writes this column, so
#: WORKING is where every message starts and where every un-star returns it.
UNSTAR_LEVEL = "WORKING"


async def set_message_star(
    session: AsyncSession,
    *,
    team_id: UUID,
    message_id: UUID,
    starred: bool,
) -> bool:
    """Star or un-star one live message. True when the row actually moved.

    Gated on the CURRENT level, so a repeated star changes nothing and says so.
    The caller turns that False into "no audit row, no broadcast": a second tap
    on an already-starred message is not an event, and a trail that records it as
    one cannot be read.

    `team_id` in the WHERE clause is the isolation boundary, for the same reason
    `get_live_message` carries it — a message id is a bare UUID in a URL.

    Caller commits.
    """
    # Star anything not already starred; un-star ONLY something that is. Stated
    # as one predicate so the two directions cannot drift apart.
    current_guard = (
        TeamMessage.truth_level != STAR_LEVEL
        if starred
        else TeamMessage.truth_level == STAR_LEVEL
    )
    stmt = (
        update(TeamMessage)
        .where(
            and_(
                TeamMessage.id == message_id,
                TeamMessage.team_id == team_id,
                TeamMessage.deleted_at.is_(None),
                current_guard,
            )
        )
        .values(truth_level=STAR_LEVEL if starred else UNSTAR_LEVEL)
        .returning(TeamMessage.id)
    )
    return (await session.execute(stmt)).first() is not None


async def count_unread_since(
    session: AsyncSession,
    *,
    team_id: UUID,
    after_created_at: datetime | None,
    exclude_user_id: UUID,
) -> int:
    """Count the "unread that matters" for one member since their read cursor.

    D-23-02: counts team messages created after `after_created_at` that the
    member still needs to catch up on — i.e. OTHER members' user messages.
    Excludes:
      - the caller's OWN messages (`author_user_id != exclude_user_id`) — you
        don't need to catch up on what you sent;
      - ALL agent frames (`kind == 'user'`; agent rows have kind='agent' and a
        NULL author_user_id);
      - soft-deleted rows (`deleted_at IS NULL`).
    A NULL `after_created_at` (first-time visitor, never read) counts every
    other member's user message. Bounded to the single `team_id` — no
    cross-team predicate (T-23-02). Team-scope / membership gating is the
    route's job (Plan 23-02); this repo carries no authZ.
    """
    stmt = select(func.count()).select_from(TeamMessage).where(
        and_(
            TeamMessage.team_id == team_id,
            TeamMessage.deleted_at.is_(None),
            TeamMessage.kind == "user",
            TeamMessage.author_user_id != exclude_user_id,
        )
    )
    if after_created_at is not None:
        stmt = stmt.where(TeamMessage.created_at > after_created_at)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


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

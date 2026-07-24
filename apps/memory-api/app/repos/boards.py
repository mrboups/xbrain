"""Boards repo — team board metadata + the Y.Doc blob (Phase 26a BOARD-01).

Conventions mirror repos/team_invite_codes.py: async functions, ``session`` first,
keyword-only args, the repo FLUSHES and the route owns the commit.

Two disciplines this module owns:

  * The blob NEVER touches a list query. ``list_boards`` selects from ``Board`` only —
    that separation is the entire reason ``board_docs`` is a sibling table rather than
    a ``bytea`` column (a blob in the metadata row drags through TOAST on every list).
  * ``upsert_doc`` stores the Y.Doc update VERBATIM. No base64, no JSON, no re-encoding
    of any kind: the Hocuspocus database extension requires ``fetch()`` to return
    exactly the bytes ``store()`` was handed, or the document history is silently
    corrupted. Overwriting the single row IS the compaction strategy.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.board import Board, BoardDoc


async def get_default_board(session: AsyncSession, *, team_id: UUID) -> Board | None:
    """Return the team's live default board, or None."""
    result = await session.execute(
        select(Board).where(
            (Board.team_id == team_id)
            & (Board.is_default.is_(True))
            & (Board.deleted_at.is_(None))
        )
    )
    return result.scalar_one_or_none()


async def create_default_board(
    session: AsyncSession,
    *,
    team_id: UUID,
    created_by_user_id: UUID | None,
    title: str,
) -> Board:
    """Insert a new default board for the team. The repo flushes; the route commits."""
    row = Board(
        team_id=team_id,
        title=title,
        is_default=True,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def get_or_create_default_board(
    session: AsyncSession,
    *,
    team_id: UUID,
    created_by_user_id: UUID | None,
    title: str,
) -> Board:
    """Return the team's default board, creating it on first call.

    This is what makes ``POST /v1/teams/{team_id}/boards`` IDEMPOTENT: calling it twice
    returns the same board rather than accumulating duplicates.

    Race-safe: two members opening the board simultaneously both miss the SELECT and
    both attempt the INSERT. The partial unique index ``ux_boards_team_default`` lets
    exactly one win; the loser's INSERT is contained in a SAVEPOINT so only that
    statement is rolled back (the caller's transaction survives), and it then re-reads
    the winner's row. Without the savepoint an IntegrityError would poison the whole
    session and turn a benign race into a 500.
    """
    existing = await get_default_board(session, team_id=team_id)
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            return await create_default_board(
                session,
                team_id=team_id,
                created_by_user_id=created_by_user_id,
                title=title,
            )
    except IntegrityError:
        winner = await get_default_board(session, team_id=team_id)
        if winner is None:
            raise
        return winner


async def list_boards(session: AsyncSession, *, team_id: UUID) -> list[Board]:
    """Return a team's live boards, newest first.

    Selects from ``Board`` ONLY — it never joins ``board_docs``, which is the whole
    reason the blob lives in a sibling table.
    """
    result = await session.execute(
        select(Board)
        .where((Board.team_id == team_id) & (Board.deleted_at.is_(None)))
        .order_by(Board.created_at.desc())
    )
    return list(result.scalars().all())


async def get_board(session: AsyncSession, *, board_id: UUID) -> Board | None:
    """Resolve a board by id (live rows only). Returns None if unknown or soft-deleted.

    The route uses the None case to answer a generic 404 — identical to the answer a
    non-member gets — so a board id is never confirmed to exist by an outsider.
    """
    result = await session.execute(
        select(Board).where((Board.id == board_id) & (Board.deleted_at.is_(None)))
    )
    return result.scalar_one_or_none()


async def get_doc(session: AsyncSession, *, board_id: UUID) -> BoardDoc | None:
    """Return the stored Y.Doc row for a board, or None when nothing was stored yet."""
    result = await session.execute(
        select(BoardDoc).where(BoardDoc.board_id == board_id)
    )
    return result.scalar_one_or_none()


async def upsert_doc(session: AsyncSession, *, board_id: UUID, state: bytes) -> None:
    """Store the compacted Y.Doc update for a board, overwriting any previous state.

    ``state`` is written VERBATIM as ``bytea``. There is no encoding step anywhere on
    this path — ``get_doc`` must hand back exactly these bytes. The repo flushes; the
    route commits.
    """
    stmt = (
        pg_insert(BoardDoc)
        .values(
            board_id=board_id,
            state=state,
            size_bytes=len(state),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=[BoardDoc.board_id],
            set_={
                "state": state,
                "size_bytes": len(state),
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(stmt)
    await session.flush()

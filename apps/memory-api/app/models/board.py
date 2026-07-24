"""boards + board_docs ORM — collaborative Excalidraw board (Phase 26a BOARD-01).

Schema lives in alembic 0028_boards.py; the column names and types here mirror that
DDL exactly. `Board` is the cheap metadata row (listed, soft-deleted, and used to
resolve `board_id -> team_id` at token-mint time); `BoardDoc` is the sibling table
holding ONE compacted Y.Doc update as raw bytes so a board list never drags the blob
through TOAST.

`BoardDoc.state` is opaque binary produced by Yjs. It is stored and returned VERBATIM —
never base64-encoded, never wrapped in JSON. The Hocuspocus database extension requires
`fetch()` to return exactly the bytes `store()` was handed, or the document history is
silently corrupted.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        Text, nullable=False, default="Team board", server_default="Team board"
    )
    # At most ONE live default per team (partial unique index ux_boards_team_default).
    # 26a ships exactly that one; the schema deliberately allows more boards per team.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft delete — Phase-11 convention. Every read path filters `deleted_at IS NULL`.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BoardDoc(Base):
    __tablename__ = "board_docs"

    board_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("boards.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # The compacted Y.Doc update, verbatim. Overwriting this single row IS the
    # compaction strategy (Yjs garbage-collects deleted content internally).
    state: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Stored so the admin storage view can surface a runaway board.
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

"""Conversation ORM — parent of messages, scoped to a team."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        # Phase 11 (migration 0017): truth_level constraint matches the DDL CHECK
        # 'conversations_truth_level_check' — must stay in sync with the canonical
        # 5-value enum (CLAUDE.md).
        CheckConstraint(
            "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="conversations_truth_level_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    team_scope: Mapped[str] = mapped_column(String(64), ForeignKey("teams.slug"), nullable=False, index=True)
    project_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Phase 11 (migration 0017) — universal tagging contract + soft-delete.
    # `truth_level` is NOT NULL with DDL DEFAULT 'EPHEMERAL'; Python-side default
    # mirrors the server_default so SQLAlchemy-generated INSERTs emit the value.
    truth_level: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="EPHEMERAL",
        server_default="EPHEMERAL",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    deleted_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

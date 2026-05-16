"""team_messages ORM — quick task 260512-tcr.

Schema lives in alembic 0015_team_messages.py. Forward-compat fields
(parent_message_id, edited_at, deleted_at) land nullable for Phase 2.
Phase 11 (migration 0017) adds `truth_level` + `deleted_by` and the
matching CHECK constraint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TeamMessage(Base):
    __tablename__ = "team_messages"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('user', 'agent')",
            name="ck_team_messages_kind",
        ),
        CheckConstraint(
            "(kind = 'user'  AND author_user_id IS NOT NULL) "
            "OR (kind = 'agent' AND agent_name IS NOT NULL)",
            name="ck_team_messages_author_required",
        ),
        # Phase 11 (migration 0017) — must match DDL CHECK
        # 'team_messages_truth_level_check'. Canonical 5-value enum (CLAUDE.md).
        CheckConstraint(
            "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="team_messages_truth_level_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    routed_via: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    parent_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("team_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Phase 11 (migration 0017) — universal tagging contract + soft-delete.
    # `truth_level` defaults to 'WORKING' for team chat (agent + human contributions
    # are working knowledge until promoted). Python default mirrors DDL
    # server_default so SQLAlchemy-side INSERTs also fill the value.
    truth_level: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="WORKING",
        server_default="WORKING",
    )
    deleted_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

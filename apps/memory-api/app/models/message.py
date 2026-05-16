"""Message ORM — carries the 7-field tagging contract in DB columns (NOT NULL)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private','team','org','public')",
            name="messages_visibility_check",
        ),
        CheckConstraint(
            "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="messages_truth_level_check",
        ),
        CheckConstraint(
            "validation_status IN ('pending','validated','rejected','n/a')",
            name="messages_validation_status_check",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="messages_confidence_range_check",
        ),
        CheckConstraint(
            "role IN ('user','assistant','system','tool')",
            name="messages_role_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 7 tagging contract fields (all NOT NULL except project_scope) — DB-level enforcement
    team_scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    truth_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False)

    # Payload
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    msg_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Phase 11 (migration 0017) — soft-delete columns (truth_level already
    # present above from migration 0001 with CHECK 'messages_truth_level_check').
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    deleted_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

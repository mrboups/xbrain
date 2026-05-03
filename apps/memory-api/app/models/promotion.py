"""Promotion ORM — truth-level promotion workflow rows."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Promotion(Base):
    __tablename__ = "promotions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','auto')",
            name="promotions_status_check",
        ),
        CheckConstraint(
            "from_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="promotions_from_check",
        ),
        CheckConstraint(
            "to_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="promotions_to_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    memory_item_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    team_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    from_level: Mapped[str] = mapped_column(String(16), nullable=False)
    to_level: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    approved_by_1: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_by_2: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

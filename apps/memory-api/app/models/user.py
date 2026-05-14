"""User ORM — maps OIDC `sub` claim to internal user id."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_user_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Phase 5 plan 05-02 — GitHub account linking (optional, nullable)
    # Populated via POST /v1/me/link-github or on first GitHub OAuth login.
    github_username: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    # Phase 10 — orphan-row soft-merge target. Set when this row was the orphan
    # that got merged into another user (Google ↔ GitHub auto-merge, GHA-06).
    # Sign-in flow follows the pointer via repos.users.follow_merge_pointer.
    merged_into_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

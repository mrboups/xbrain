"""push_subscriptions ORM — one browser push mailbox per device (Phase 27 PUSH-01).

Schema lives in alembic 0029_push_subscriptions.py; the column names and types here
mirror that DDL exactly.

The uniqueness that matters is NOT declared here as a column flag by accident — it is
the standalone unique index `ux_push_subscriptions_endpoint` on `endpoint` alone. A
push endpoint identifies a BROWSER, not an account, so a shared device presents the
same endpoint for successive users and the subscribe path must TRANSFER ownership
rather than add a second row. `unique=True` on the mapped column expresses the same
constraint the migration creates; see the migration docstring for why it is not
composite with `user_id`.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    # CASCADE: a deleted account must not leave a live delivery channel behind.
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Unique on its own — the mailbox identity. See the class docstring.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Client key material for aes128gcm payload encryption. Not secrets of ours: they
    # are minted by, and only useful to, the browser that owns this endpoint.
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    # Human label for a "your devices" list; never used to route a message.
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Stamped by the send path (plan 27-04) so a stale device is visible.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

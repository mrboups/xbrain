"""Installation ORM — one row per GitHub App installation (per org).

Source of truth is GitHub; this table is populated and updated via the
/v1/webhooks/github/installation handler (Plan 12-05). Reads are scoped
by github_org_login (active rows only via the partial unique index) or by
installation_id (PK).

The row is NEVER hard-deleted: installation.deleted webhooks set revoked_at
so historical references (audit, future FKs from users etc.) survive. The
partial unique index `idx_installations_org_login_active` enforces "one active
install per org" while permitting any number of revoked rows for the same
org_login — matches GitHub's re-install behaviour (new installation_id is
issued each time, the prior row stays as audit).

Mirrors `apps/memory-api/alembic/versions/0019_github_app_install.py`. Any
ALTER to either side requires the other side to track.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Installation(Base):
    """One row per GitHub App installation. PK = GitHub's installation_id."""

    __tablename__ = "installations"
    __table_args__ = (
        CheckConstraint(
            "github_account_type IN ('Organization', 'User')",
            name="installations_account_type_check",
        ),
        # Partial unique index — "one active install per org" — see module
        # docstring. Re-installs create a new row; the old one stays revoked.
        Index(
            "idx_installations_org_login_active",
            "github_org_login",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )

    # GitHub's numeric installation_id. autoincrement=False because the value
    # is supplied by GitHub via the install webhook, not generated locally.
    installation_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    github_org_login: Mapped[str] = mapped_column(Text, nullable=False)
    github_account_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Organization", server_default="Organization"
    )
    installed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    # NULL when the install was triggered out-of-band (e.g. CLI). Populated
    # from webhook payload `sender.id` when present.
    installed_by_github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Set on installation.suspend webhook; cleared on installation.unsuspend.
    suspended_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Set on installation.deleted webhook. Soft-delete only — see docstring.
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Last webhook payload snapshot, for debugging / replay.
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

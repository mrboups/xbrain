"""Phase 12 — installations table + users token storage columns.

Foundation for the GitHub App migration (GHAPP-03 + GHAPP-05). This migration
adds:

  1. A new `installations` table — source-of-truth for "which orgs have
     installed the xbrain GitHub App". Populated and updated via the
     /v1/webhooks/github/installation handler (Plan 12-05). Rows are never
     hard-deleted: `installation.deleted` webhooks set `revoked_at` so
     historical references (audit, future FKs) survive.

  2. Four new nullable columns on `users` for user-to-server token storage:
     `github_access_token_enc`, `github_refresh_token_enc`,
     `github_token_expires_at`, `github_refresh_expires_at`. The `_enc` suffix
     is mandatory — these store Fernet-encrypted bytes, never raw token text.
     Plan 12-06 adds the encrypt/decrypt helpers + refresh flow + the 5th
     column `github_access_token_hash` (M-5 fix from plan-check).

A partial unique index `idx_installations_org_login_active` enforces "only
one active install per org" — matches GitHub's behaviour (re-installs delete
then create with a new installation_id; the old row remains as audit).

Why a single migration instead of two:

  - Both changes are tightly coupled — Plan 12-02 onwards consume the
    settings AND the schema together. Splitting would force an intermediate
    deploy with half the schema in place.
  - The columns are nullable + the table is empty after upgrade, so the
    migration is non-blocking and instant on the current data volumes
    (single user mrboups, zero installs pre-Phase-12).

Forward note: Plan 12-06 EXTENDS this migration with `github_access_token_hash
CHAR(64)` + partial index for O(log n) lookup by HMAC-SHA256(token). The diff
is small and localised; see Plan 12-06 Task 1b for the exact edit.

Revision ID: 0019_github_app_install
Revises: 0018_brain_events_view
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_github_app_install"
down_revision: Union[str, None] = "0018_brain_events_view"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. installations table — GHAPP-03 ────────────────────────────────
    # PK is installation_id (GitHub's numeric installation id, fits in
    # BIGINT). autoincrement=False because the value is supplied by GitHub
    # via the install webhook, not generated locally.
    op.create_table(
        "installations",
        sa.Column("installation_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("github_org_login", sa.Text(), nullable=False),
        sa.Column(
            "github_account_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'Organization'"),
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("installed_by_github_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "github_account_type IN ('Organization', 'User')",
            name="installations_account_type_check",
        ),
    )

    # Partial unique index — "only one active install per org". Re-installs
    # produce a new installation_id; the old row stays with revoked_at set,
    # preserving audit history. WHERE clause requires PG 7.2+ (we pin PG 17).
    op.execute(
        "CREATE UNIQUE INDEX idx_installations_org_login_active "
        "ON installations (github_org_login) "
        "WHERE revoked_at IS NULL"
    )

    # ── 2. users — 4 new nullable columns for user-to-server tokens ──────
    # GHAPP-05. Encrypted at rest via Fernet (RESEARCH §Security V8); the
    # _enc suffix is mandatory naming convention so future readers can tell
    # at a glance the column is encrypted bytes, not raw token text.
    # Plan 12-06 implements the encrypt/decrypt helpers + refresh flow.
    op.add_column(
        "users",
        sa.Column("github_access_token_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("github_refresh_token_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("github_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("github_refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Reverse order: drop the users columns first, then the installations
    # table. CASCADE on the table drop covers the partial unique index
    # automatically (it's owned by the table).
    op.drop_column("users", "github_refresh_expires_at")
    op.drop_column("users", "github_token_expires_at")
    op.drop_column("users", "github_refresh_token_enc")
    op.drop_column("users", "github_access_token_enc")

    op.execute("DROP INDEX IF EXISTS idx_installations_org_login_active")
    op.drop_table("installations")

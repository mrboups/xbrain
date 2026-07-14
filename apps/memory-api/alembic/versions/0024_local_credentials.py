"""local_credentials — email/password auth data layer (Phase 18 LAUTH-01, D-18-03).

Dedicated table for local (email/password) credentials, kept OFF the already
GitHub-column-heavy `users` table per D-18-03. Holds the argon2id hash and the
DB-backed lockout counters (failed_attempts/locked_until) that D-18-06 requires
to survive restarts and be shared across all uvicorn workers — unlike the
in-process rate limiter (Plan 02's rate_limit.py).

This migration adds no additional uniqueness guard on the users address
column (research Pitfall 4): forcing one would break outright on any
deployment with pre-existing duplicate addresses. D-18-05's register-collision
correctness is enforced at the app layer
(app/repos/local_credentials.py::get_by_email + repos/users.py::get_user_by_email),
not by a database-level constraint.

Revision ID: 0024_local_credentials
Revises: 0023_tasks_source_connector
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024_local_credentials"
down_revision: Union[str, None] = "0023_tasks_source_connector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS local_credentials (
            user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            password_hash   TEXT NOT NULL,
            algo            VARCHAR(32) NOT NULL DEFAULT 'argon2id',
            failed_attempts INT NOT NULL DEFAULT 0,
            locked_until    TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS local_credentials")

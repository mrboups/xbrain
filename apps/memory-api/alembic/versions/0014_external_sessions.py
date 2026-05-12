"""user_external_sessions table for Phase 9 session-bridge.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_external_sessions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider      VARCHAR(32) NOT NULL,
            extension_id  VARCHAR(64),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata      JSONB,
            CONSTRAINT uq_external_sessions_user_provider UNIQUE (user_id, provider)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_sessions_user "
        "ON user_external_sessions(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_external_sessions_user")
    op.execute("DROP TABLE IF EXISTS user_external_sessions")

"""user_api_tokens table for personal API keys.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_api_tokens (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  TEXT NOT NULL UNIQUE,
            team_scope  TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT 'default',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ,
            revoked_at  TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON user_api_tokens(token_hash) WHERE revoked_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON user_api_tokens(user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_api_tokens_user")
    op.execute("DROP INDEX IF EXISTS idx_api_tokens_hash")
    op.execute("DROP TABLE IF EXISTS user_api_tokens")

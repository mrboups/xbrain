"""github_users — add github_username + github_id to users table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-06

Schema: nullable columns on users — a Google-only user has NULLs here until they
        optionally link their GitHub account via POST /v1/me/link-github.
        github_id is BIGINT (GitHub numeric user IDs are large integers).
        Unique index on github_id enforces one xbrain user per GitHub account.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_username", sa.String(256), nullable=True))
    op.add_column("users", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.create_index("idx_users_github_username", "users", ["github_username"])
    op.create_index("idx_users_github_id", "users", ["github_id"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_users_github_id", table_name="users")
    op.drop_index("idx_users_github_username", table_name="users")
    op.drop_column("users", "github_id")
    op.drop_column("users", "github_username")

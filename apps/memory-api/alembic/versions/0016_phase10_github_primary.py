"""Phase 10 — GitHub-primary auth: member block, org pre-block, user merge pointer.

Adds:
  - team_members.blocked_at / blocked_by (per-member soft-block, GHA-04).
  - team_org_blocks (pre-block table for GitHub logins not yet signed up, GHA-05).
  - users.merged_into_user_id (orphan-row soft-merge pointer, GHA-02 / GHA-06).
  - idx_users_active partial index for fast "live users" filtering.

Revision ID: 0016_phase10_github_primary
Revises: 0015
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_phase10_github_primary"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. team_members.blocked_at
    op.add_column(
        "team_members",
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 2. team_members.blocked_by (FK → users.id, SET NULL on delete)
    op.add_column(
        "team_members",
        sa.Column(
            "blocked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 3. team_org_blocks — pre-block for GitHub logins not yet signed up.
    op.create_table(
        "team_org_blocks",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("github_login", sa.String(256), nullable=False),
        sa.Column(
            "blocked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "blocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("team_id", "github_login", name="team_org_blocks_pkey"),
    )

    # 4. users.merged_into_user_id — soft-delete pointer for orphan-row merge.
    op.add_column(
        "users",
        sa.Column(
            "merged_into_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 5. Partial index for fast "live users" filtering.
    op.create_index(
        "idx_users_active",
        "users",
        ["id"],
        postgresql_where=sa.text("merged_into_user_id IS NULL"),
    )


def downgrade() -> None:
    # Reverse in exact reverse order.
    op.drop_index("idx_users_active", table_name="users")
    op.drop_column("users", "merged_into_user_id")
    op.drop_table("team_org_blocks")
    op.drop_column("team_members", "blocked_by")
    op.drop_column("team_members", "blocked_at")

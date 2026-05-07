"""team_onboarding — visibility, github_org, description on teams + api_keys + join_requests

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Extend teams table ---
    op.add_column(
        "teams",
        sa.Column("visibility", sa.String(16), nullable=False, server_default="closed"),
    )
    op.create_check_constraint(
        "teams_visibility_check",
        "teams",
        "visibility IN ('open', 'closed')",
    )
    op.add_column(
        "teams",
        sa.Column("github_org", sa.String(256), nullable=True),
    )
    op.add_column(
        "teams",
        sa.Column("description", sa.Text, nullable=True),
    )

    # --- team_api_keys ---
    op.create_table(
        "team_api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("key_enc", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("team_id", "provider", name="team_api_keys_team_provider_uniq"),
    )
    op.create_index("idx_team_api_keys_team", "team_api_keys", ["team_id"])

    # --- team_join_requests ---
    op.create_table(
        "team_join_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="join_requests_status_check",
        ),
        sa.UniqueConstraint("team_id", "user_id", name="join_requests_team_user_uniq"),
    )
    op.create_index("idx_join_requests_user", "team_join_requests", ["user_id"])
    op.create_index("idx_join_requests_team_status", "team_join_requests", ["team_id", "status"])


def downgrade() -> None:
    # Indexes
    op.drop_index("idx_join_requests_team_status", table_name="team_join_requests")
    op.drop_index("idx_join_requests_user", table_name="team_join_requests")
    op.drop_index("idx_team_api_keys_team", table_name="team_api_keys")

    # Tables
    op.drop_table("team_join_requests")
    op.drop_table("team_api_keys")

    # teams columns (drop constraint first, then column)
    op.drop_constraint("teams_visibility_check", "teams", type_="check")
    op.drop_column("teams", "description")
    op.drop_column("teams", "github_org")
    op.drop_column("teams", "visibility")

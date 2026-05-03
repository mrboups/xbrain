"""initial schema: users, teams, team_members, conversations, messages, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-05-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Required extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # for gen_random_uuid()

    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source_user_id", sa.String(256), nullable=False, unique=True),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_source_user_id", "users", ["source_user_id"])

    # teams
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_teams_slug", "teams", ["slug"])

    # team_members
    op.create_table(
        "team_members",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('admin','member')", name="team_members_role_check"),
    )

    # conversations
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("team_scope", sa.String(64), sa.ForeignKey("teams.slug"), nullable=False),
        sa.Column("project_scope", sa.String(64), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_conv_team", "conversations", ["team_scope"])

    # messages
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        # 7 tagging contract fields
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("project_scope", sa.String(64), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("truth_level", sa.String(16), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("validation_status", sa.String(16), nullable=False),
        # Payload
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("visibility IN ('private','team','org','public')", name="messages_visibility_check"),
        sa.CheckConstraint("truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')", name="messages_truth_level_check"),
        sa.CheckConstraint("validation_status IN ('pending','validated','rejected','n/a')", name="messages_validation_status_check"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="messages_confidence_range_check"),
        sa.CheckConstraint("role IN ('user','assistant','system','tool')", name="messages_role_check"),
    )
    # content_tsv generated column for full-text search (SRCH-01/02)
    op.execute(
        "ALTER TABLE messages ADD COLUMN content_tsv TSVECTOR "
        "GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED"
    )
    op.create_index("idx_msg_team", "messages", ["team_scope"])
    op.create_index("idx_msg_conv", "messages", ["conversation_id"])
    op.create_index("idx_msg_truth", "messages", ["truth_level"])
    op.execute("CREATE INDEX idx_msg_tsv ON messages USING GIN(content_tsv)")

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("team_scope", sa.String(64), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_id", sa.String(256), nullable=True),
        sa.Column("payload", postgresql.JSONB, server_default="{}", nullable=False),
    )
    op.create_index("idx_audit_ts", "audit_log", [sa.text("ts DESC")])
    op.create_index("idx_audit_team", "audit_log", ["team_scope"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.execute("DROP INDEX IF EXISTS idx_msg_tsv")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("users")

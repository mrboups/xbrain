"""tasks — task tracking table — D4 model + D5 source provenance

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        # Tagging contract
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("project_scope", sa.String(64), nullable=True),
        sa.Column(
            "visibility",
            sa.String(16),
            nullable=False,
            server_default="team",
        ),
        sa.Column(
            "validation_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        # Contenu
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="todo"),
        sa.Column("priority", sa.String(8), nullable=False, server_default="normal"),
        sa.Column("due_date", sa.Date, nullable=True),
        # Assignments
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,  # NULL = system-generated task (Granola ingest, agent output, chat auto-extraction)
        ),
        # Provenance (D5)
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_ref", postgresql.UUID(as_uuid=True), nullable=True),
        # Standard
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('todo','in_progress','done','cancelled')",
            name="tasks_status_check",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','urgent')",
            name="tasks_priority_check",
        ),
        sa.CheckConstraint(
            "source IN ('granola','agent','chat','manual')",
            name="tasks_source_check",
        ),
        sa.CheckConstraint(
            "visibility IN ('team', 'project', 'public', 'private')",
            name="tasks_visibility_check",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'validated', 'rejected', 'expired')",
            name="tasks_validation_status_check",
        ),
    )
    op.create_index("idx_tasks_team", "tasks", ["team_scope"])
    op.create_index("idx_tasks_assigned", "tasks", ["assigned_to"])
    op.create_index("idx_tasks_status", "tasks", ["team_scope", "status"])
    op.create_index("idx_tasks_due_date", "tasks", ["due_date"])


def downgrade() -> None:
    op.drop_index("idx_tasks_due_date", table_name="tasks")
    op.drop_index("idx_tasks_status", table_name="tasks")
    op.drop_index("idx_tasks_assigned", table_name="tasks")
    op.drop_index("idx_tasks_team", table_name="tasks")
    op.drop_table("tasks")

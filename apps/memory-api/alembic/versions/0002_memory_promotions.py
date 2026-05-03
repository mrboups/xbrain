"""memory_items + history + promotions workflow tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-03

Phase 2 schema additions:
- memory_items : facts persistents (Phase 2 native backend)
- memory_items_history : append-only versioning
- promotions : truth-level promotion workflow with 4-eyes for CANONICAL
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === memory_items — facts persistents (Phase 2 NativeProvider backend) ===
    op.create_table(
        "memory_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("project_scope", sa.String(64), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB, server_default="{}", nullable=False
        ),
        sa.Column(
            "visibility", sa.String(16), nullable=False, server_default="team"
        ),
        sa.Column(
            "truth_level", sa.String(16), nullable=False, server_default="EPHEMERAL"
        ),
        sa.Column(
            "validation_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(128), nullable=False),
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
        sa.CheckConstraint(
            "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="memory_items_truth_check",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending','validated','rejected','n/a')",
            name="memory_items_validation_check",
        ),
        sa.CheckConstraint(
            "visibility IN ('private','team','org','public')",
            name="memory_items_visibility_check",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="memory_items_confidence_range_check",
        ),
    )
    op.create_index("idx_memory_team", "memory_items", ["team_scope"])
    op.create_index("idx_memory_truth", "memory_items", ["truth_level"])
    op.create_index(
        "idx_memory_team_truth", "memory_items", ["team_scope", "truth_level"]
    )

    # === memory_items_history — append-only versioning ===
    op.create_table(
        "memory_items_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB, server_default="{}", nullable=False
        ),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("truth_level", sa.String(16), nullable=False),
        sa.Column("validation_status", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_history_item", "memory_items_history", ["item_id"])
    op.create_index("idx_history_team", "memory_items_history", ["team_scope"])

    # === promotions — workflow state machine with 4-eyes for CANONICAL ===
    op.create_table(
        "promotions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("memory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("from_level", sa.String(16), nullable=False),
        sa.Column("to_level", sa.String(16), nullable=False),
        sa.Column(
            "proposed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "approved_by_1",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_2",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','auto')",
            name="promotions_status_check",
        ),
        sa.CheckConstraint(
            "from_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="promotions_from_check",
        ),
        sa.CheckConstraint(
            "to_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="promotions_to_check",
        ),
    )
    op.create_index("idx_promotions_item", "promotions", ["memory_item_id"])
    op.create_index(
        "idx_promotions_pending",
        "promotions",
        ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("idx_promotions_team", "promotions", ["team_scope"])


def downgrade() -> None:
    op.drop_table("promotions")
    op.drop_table("memory_items_history")
    op.drop_table("memory_items")

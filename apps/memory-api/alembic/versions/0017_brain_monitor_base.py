"""Phase 11 — universal truth_level + soft-delete columns.

Extends the xbrain tagging contract to every brain-tracked entity:

  - conversations  : add truth_level (DEFAULT 'EPHEMERAL') + deleted_at + deleted_by
  - tasks          : add truth_level (DEFAULT 'WORKING')    + deleted_at + deleted_by
  - team_messages  : add truth_level (DEFAULT 'WORKING')    + deleted_by
                     (deleted_at already exists from 0015)
  - memory_items   : add deleted_at + deleted_by
                     (truth_level already exists from 0002)
  - messages       : add deleted_at + deleted_by
                     (truth_level already exists from 0001)
  - contacts       : add deleted_at + deleted_by
                     (truth_level already exists from 0009 with DEFAULT 'EPHEMERAL';
                      we deliberately keep it — see Risks in 11-01-PLAN.md.)

Each new truth_level column gets a CHECK constraint allowing the canonical
5-value enum from CLAUDE.md: ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC').

Each table gets one new composite index on (team_*, deleted_at) to back the
"recent + not deleted" retrieval path that Phase 11's read-side patches and
the v_brain_events view (migration 0018, plan 11-02) will hit.

NOTE on concurrent index creation: this migration runs CREATE INDEX inside the
default Alembic transaction. Acceptable today because Phase 11 row counts are
< 10k per table (see 11-RESEARCH.md Q9). If this migration is ever re-run on a
materially larger DB, switch each create_index call to
`postgresql_concurrently=True` inside `with op.get_context().autocommit_block():`.

Revision ID: 0017_brain_monitor_base
Revises: 0016_phase10_github_primary
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_brain_monitor_base"
down_revision: Union[str, None] = "0016_phase10_github_primary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Canonical truth-level enum — kept in one place for the three new CHECK
# constraints this migration adds. Must stay in sync with CLAUDE.md and with
# the existing checks on memory_items (0002), messages (0001), contacts (0009).
_TRUTH_LEVEL_CHECK_EXPR = (
    "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')"
)


def upgrade() -> None:
    # ── 1. memory_items: deleted_at + deleted_by + index ─────────────────
    # truth_level already exists from 0002 with DEFAULT 'EPHEMERAL' and CHECK
    # 'memory_items_truth_check' — do NOT redefine.
    op.add_column(
        "memory_items",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_memory_team_deleted",
        "memory_items",
        ["team_scope", "deleted_at"],
    )

    # ── 2. conversations: truth_level (NEW) + deleted_at + deleted_by ────
    op.add_column(
        "conversations",
        sa.Column(
            "truth_level",
            sa.String(16),
            nullable=False,
            server_default="EPHEMERAL",
        ),
    )
    op.create_check_constraint(
        "conversations_truth_level_check",
        "conversations",
        _TRUTH_LEVEL_CHECK_EXPR,
    )
    op.add_column(
        "conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_conv_team_deleted",
        "conversations",
        ["team_scope", "deleted_at"],
    )

    # ── 3. messages: deleted_at + deleted_by + index ─────────────────────
    # truth_level already exists from 0001 with CHECK 'messages_truth_level_check'.
    op.add_column(
        "messages",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_msg_team_deleted",
        "messages",
        ["team_scope", "deleted_at"],
    )

    # ── 4. team_messages: truth_level (NEW) + deleted_by + index ─────────
    # deleted_at already exists from 0015 (Phase 2 forward-compat). team_messages
    # uses team_id (UUID), not team_scope (slug) — index is on team_id.
    op.add_column(
        "team_messages",
        sa.Column(
            "truth_level",
            sa.String(16),
            nullable=False,
            server_default="WORKING",
        ),
    )
    op.create_check_constraint(
        "team_messages_truth_level_check",
        "team_messages",
        _TRUTH_LEVEL_CHECK_EXPR,
    )
    op.add_column(
        "team_messages",
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_team_messages_deleted",
        "team_messages",
        ["team_id", "deleted_at"],
    )

    # ── 5. tasks: truth_level (NEW) + deleted_at + deleted_by + index ────
    op.add_column(
        "tasks",
        sa.Column(
            "truth_level",
            sa.String(16),
            nullable=False,
            server_default="WORKING",
        ),
    )
    op.create_check_constraint(
        "tasks_truth_level_check",
        "tasks",
        _TRUTH_LEVEL_CHECK_EXPR,
    )
    op.add_column(
        "tasks",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_tasks_team_deleted",
        "tasks",
        ["team_scope", "deleted_at"],
    )

    # ── 6. contacts: deleted_at + deleted_by + index ─────────────────────
    # truth_level already exists from 0009 with DEFAULT 'EPHEMERAL' and CHECK
    # 'contacts_truth_level_check' — DO NOT redefine (the CONTEXT.md target
    # default 'WORKING' is enforced at the API/Pydantic layer in a later plan,
    # not via DDL change here).
    op.add_column(
        "contacts",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_contacts_team_deleted",
        "contacts",
        ["team_scope", "deleted_at"],
    )


def downgrade() -> None:
    # Reverse-order drops: indexes → CHECK constraints → columns.

    # contacts
    op.drop_index("idx_contacts_team_deleted", table_name="contacts")
    op.drop_column("contacts", "deleted_by")
    op.drop_column("contacts", "deleted_at")

    # tasks
    op.drop_index("idx_tasks_team_deleted", table_name="tasks")
    op.drop_column("tasks", "deleted_by")
    op.drop_column("tasks", "deleted_at")
    op.drop_constraint("tasks_truth_level_check", "tasks", type_="check")
    op.drop_column("tasks", "truth_level")

    # team_messages
    op.drop_index("idx_team_messages_deleted", table_name="team_messages")
    op.drop_column("team_messages", "deleted_by")
    op.drop_constraint(
        "team_messages_truth_level_check", "team_messages", type_="check"
    )
    op.drop_column("team_messages", "truth_level")

    # messages
    op.drop_index("idx_msg_team_deleted", table_name="messages")
    op.drop_column("messages", "deleted_by")
    op.drop_column("messages", "deleted_at")

    # conversations
    op.drop_index("idx_conv_team_deleted", table_name="conversations")
    op.drop_column("conversations", "deleted_by")
    op.drop_column("conversations", "deleted_at")
    op.drop_constraint(
        "conversations_truth_level_check", "conversations", type_="check"
    )
    op.drop_column("conversations", "truth_level")

    # memory_items
    op.drop_index("idx_memory_team_deleted", table_name="memory_items")
    op.drop_column("memory_items", "deleted_by")
    op.drop_column("memory_items", "deleted_at")

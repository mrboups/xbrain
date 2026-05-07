"""crm_contacts — contacts table + granola_integrations table — D1 CRM + D3 Granola storage

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Table: contacts (D1) ─────────────────────────────────────────────
    op.create_table(
        "contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        # Tagging contract (7 fields: team_scope, project_scope, visibility,
        # confidence, truth_level, source, validation_status)
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("contact_type", sa.String(8), nullable=False),
        sa.Column("project_scope", sa.String(64), nullable=True),
        sa.Column("truth_level", sa.String(16), nullable=False, server_default="EPHEMERAL"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(128), nullable=False),
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
        # Identité
        sa.Column("full_name", sa.String(256), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("company", sa.String(256), nullable=True),
        sa.Column("role", sa.String(256), nullable=True),
        # Mass contact specific
        sa.Column("list_source", sa.String(256), nullable=True),
        sa.Column("opt_in_status", sa.String(16), nullable=True, server_default="unknown"),
        sa.Column("list_added_at", sa.DateTime(timezone=True), nullable=True),
        # Interaction tracking
        sa.Column("interaction_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
        # Standard
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "contact_type IN ('direct','mass')",
            name="contacts_type_check",
        ),
        sa.CheckConstraint(
            "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
            name="contacts_truth_level_check",
        ),
        sa.CheckConstraint(
            "opt_in_status IN ('unknown','opted_in','opted_out')",
            name="contacts_opt_in_check",
        ),
        sa.CheckConstraint(
            "visibility IN ('team', 'project', 'public', 'private')",
            name="contacts_visibility_check",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'validated', 'rejected', 'expired')",
            name="contacts_validation_status_check",
        ),
    )
    op.create_index("idx_contacts_team", "contacts", ["team_scope"])
    op.create_index("idx_contacts_type", "contacts", ["contact_type"])
    # Partial unique index — only enforce uniqueness when email IS NOT NULL
    op.execute(
        "CREATE UNIQUE INDEX idx_contacts_team_email "
        "ON contacts(team_scope, email) WHERE email IS NOT NULL"
    )

    # ── Table: granola_integrations (D3) ─────────────────────────────────
    op.create_table(
        "granola_integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_granola_integrations_team",
        "granola_integrations",
        ["team_scope"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_granola_integrations_team", table_name="granola_integrations")
    op.drop_table("granola_integrations")
    op.execute("DROP INDEX IF EXISTS idx_contacts_team_email")
    op.drop_index("idx_contacts_type", table_name="contacts")
    op.drop_index("idx_contacts_team", table_name="contacts")
    op.drop_table("contacts")

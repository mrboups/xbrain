"""neo4j_outbox + team_drive_mappings + memory_items source uniqueness

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-04

Phase 3 schema additions:
- neo4j_outbox      : outbox table for async Neo4j sync (drained by background worker in memory-api)
- team_drive_mappings : admin config — one Drive folder per team, persists change_token
- memory_items      : add UNIQUE(source, team_scope) + idx_memory_source for drive-sync idempotence
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === neo4j_outbox — rows enqueued by memory-api writes, drained to Neo4j async ===
    op.create_table(
        "neo4j_outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("cypher", sa.Text, nullable=False),
        sa.Column("params", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("processed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("error", sa.Text, nullable=True),
    )
    # Partial index on unprocessed rows only — worker SELECT ... WHERE processed=false LIMIT 50
    op.create_index(
        "idx_outbox_unprocessed",
        "neo4j_outbox",
        ["created_at"],
        postgresql_where=sa.text("processed = false"),
    )

    # === team_drive_mappings — one Drive folder per team (admin configurable) ===
    op.create_table(
        "team_drive_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("folder_id", sa.String(256), nullable=False),
        # Google Drive changes.list page token — persisted immediately after each poll tick
        sa.Column("change_token", sa.Text, nullable=True),
        # Encrypted OAuth credentials (Fernet or base64 AES) — NEVER store plaintext refresh_token
        sa.Column("oauth_credentials_enc", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Partial unique index: one active mapping per team (team_scope must be unique)
    op.create_index(
        "idx_drive_mapping_team_unique",
        "team_drive_mappings",
        ["team_scope"],
        unique=True,
    )
    op.create_index(
        "idx_drive_mapping_folder",
        "team_drive_mappings",
        ["folder_id"],
    )

    # === memory_items — add UNIQUE(source, team_scope) for drive-sync idempotence ===
    # Drive-sync calls upsert with source="drive:{file_id}" — without this constraint
    # a cold restart (410 Gone token) would INSERT duplicates instead of updating.
    # The UNIQUE constraint lets drive-sync do ON CONFLICT(source, team_scope) DO UPDATE.
    op.create_index(
        "idx_memory_source_team_unique",
        "memory_items",
        ["source", "team_scope"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_memory_source_team_unique", table_name="memory_items")
    op.drop_index("idx_drive_mapping_folder", table_name="team_drive_mappings")
    op.drop_index("idx_drive_mapping_team_unique", table_name="team_drive_mappings")
    op.drop_table("team_drive_mappings")
    op.drop_index("idx_outbox_unprocessed", table_name="neo4j_outbox")
    op.drop_table("neo4j_outbox")

"""drive_watch_channels — table for Google Drive push notification channels

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-05

Schema: one channel per team_drive_mapping. Stores Google channel_id, resource_id,
        channel_token (for X-Goog-Channel-Token verification), and expiration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drive_watch_channels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        # Google-assigned channel ID (UUID string returned by changes.watch)
        sa.Column("channel_id", sa.String(256), nullable=False, unique=True),
        # Drive resource ID (the folder being watched)
        sa.Column("resource_id", sa.String(256), nullable=True),
        # FK to team_drive_mappings.id — one channel per mapping
        sa.Column(
            "mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_drive_mappings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # one active channel per mapping
        ),
        # Token sent back by Google in X-Goog-Channel-Token — used for auth verification
        sa.Column("channel_token", sa.String(256), nullable=False),
        # Google Drive channels expire after at most 7 days (we use 24h)
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_watch_channel_expires",
        "drive_watch_channels",
        ["expires_at"],
    )
    op.create_index(
        "idx_watch_channel_mapping",
        "drive_watch_channels",
        ["mapping_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_watch_channel_mapping", table_name="drive_watch_channels")
    op.drop_index("idx_watch_channel_expires", table_name="drive_watch_channels")
    op.drop_table("drive_watch_channels")

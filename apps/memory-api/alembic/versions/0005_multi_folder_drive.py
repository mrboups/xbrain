"""team_drive_mappings — multi-folder per team + project_scope column

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-05

Changes:
- Drop UNIQUE(team_scope) constraint (was idx_drive_mapping_team_unique)
- Add UNIQUE(team_scope, folder_id) — a team can map the same folder only once
- Add project_scope VARCHAR(64) nullable — allows per-folder project tagging
- Existing rows keep their team_scope, folder_id, change_token and
  oauth_credentials_enc unchanged. The project_scope column defaults to NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old UNIQUE(team_scope) — enforced 1-folder-per-team constraint
    op.drop_index("idx_drive_mapping_team_unique", table_name="team_drive_mappings")

    # Add project_scope column (nullable — existing rows get NULL)
    op.add_column(
        "team_drive_mappings",
        sa.Column("project_scope", sa.String(64), nullable=True),
    )

    # New composite unique: (team_scope, folder_id) — one mapping per (team, folder) pair
    op.create_index(
        "idx_drive_mapping_team_folder_unique",
        "team_drive_mappings",
        ["team_scope", "folder_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_drive_mapping_team_folder_unique", table_name="team_drive_mappings")
    op.drop_column("team_drive_mappings", "project_scope")
    op.create_index(
        "idx_drive_mapping_team_unique",
        "team_drive_mappings",
        ["team_scope"],
        unique=True,
    )

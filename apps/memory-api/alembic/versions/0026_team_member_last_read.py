"""team_member_last_read — per-member read cursor on team_members (Phase 23 CATCHUP-01, D-23-01).

Adds a single nullable `last_read_at` TIMESTAMPTZ column to `team_members`. It is a
PRIVATE per-member read cursor for the "Catch me up" feature: NULL means the member
has never read the team chat (a first-time visitor); a timestamp marks where they
last caught up. It is set to now() by `repos.teams.set_last_read` (a Python datetime,
not the DDL server_default) whenever the client marks the thread read. This is NOT a
"seen by" social read-receipt — no cross-member exposure.

Additive, forward-only (Phase-17 pattern): no data backfill, no default value, and
NO branch on the install-edition flag — the column is created unconditionally so the
schema is identical for every edition (asserted by
tests/test_migration_editions.py::test_no_migration_branches_on_edition). The
`downgrade()` is present for symmetry only; release validation never invokes it.

Revision ID: 0026_team_member_last_read
Revises: 0025_team_agent_aliases
Create Date: 2026-07-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0026_team_member_last_read"
down_revision: Union[str, None] = "0025_team_agent_aliases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE team_members ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE team_members DROP COLUMN IF EXISTS last_read_at")

"""team_agent_aliases — per-team custom agent mention aliases (Phase 21 ALIAS-01, D-21-03).

Adds a single nullable `agent_aliases` TEXT column to `teams`. It holds a
comma-separated list of a team's CUSTOM mention aliases (no leading '@'); NULL or
empty means the team falls back to the env/`AGENT_MENTION_ALIASES` defaults only.

Additive, forward-only (Phase-17 pattern): no data backfill, no default value,
and NO branch on the install-edition flag — the column is created unconditionally
so the schema is identical for every edition (asserted by
tests/test_migration_editions.py::test_no_migration_branches_on_edition). The
`downgrade()` is present for symmetry only; release validation never invokes it.

Revision ID: 0025_team_agent_aliases
Revises: 0024_local_credentials
Create Date: 2026-07-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0025_team_agent_aliases"
down_revision: Union[str, None] = "0024_local_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS agent_aliases TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS agent_aliases")

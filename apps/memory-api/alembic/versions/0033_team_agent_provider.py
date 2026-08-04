"""team_agent_provider — which provider a team's agent FALLS BACK to.

Adds `teams.agent_provider`, a NOT NULL column defaulting to 'anthropic'. The
default is the whole point of the migration: every team that exists when this
runs keeps today's behaviour byte for byte, because today's behaviour IS
Anthropic. Nobody is opted into a provider they did not choose.

WHAT THIS COLUMN IS NOT. It does not govern the subscription. A live browser
bridge answers through the user's Claude Pro/Max session, costs the team
nothing, and is preferred whatever this column says. This is the FALLBACK — what
gets spent in the moments no bridge exists.

The accepted set is enforced by a CHECK constraint rather than left to the
application. The routes validate too, but a column that can hold free text is a
column that eventually holds free text, and the value is read on the path that
decides which vendor to bill.

Additive, forward-only (Phase-17 pattern): no backfill beyond the DEFAULT, and
NO branch on the install-edition flag — the column is created unconditionally so
the schema is identical for every edition (asserted by
tests/test_migration_editions.py::test_no_migration_branches_on_edition). Both
statements are idempotent; `downgrade()` is present for symmetry only.

Revision ID: 0033_team_agent_provider
Revises: 0032_transcript_imports
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0033_team_agent_provider"
down_revision: Union[str, None] = "0032_transcript_imports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept in lockstep with app.services.team_keys.SUPPORTED_PROVIDERS. A value the
# code can select but the constraint rejects would fail at write time, in an
# admin's face, with a database error — so the two lists are asserted equal by
# tests/test_migration_0033.py.
_ACCEPTED = ("anthropic", "openai", "xai")
_CHECK = "teams_agent_provider_check"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE teams ADD COLUMN IF NOT EXISTS agent_provider "
        "VARCHAR(32) NOT NULL DEFAULT 'anthropic'"
    )
    accepted = ", ".join(f"'{p}'" for p in _ACCEPTED)
    # ADD CONSTRAINT has no IF NOT EXISTS in Postgres; the catalogue check gives
    # the same re-runnable property without one.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_CHECK}'
            ) THEN
                ALTER TABLE teams ADD CONSTRAINT {_CHECK}
                    CHECK (agent_provider IN ({accepted}));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE teams DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS agent_provider")

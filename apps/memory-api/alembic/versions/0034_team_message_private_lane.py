"""team_messages.private_to_user_id — the brain tag's chat-surface flag.

WHAT THE COLUMN MEANS. NULL is "the team sees this", which is every row that
exists when this runs — so the migration changes nothing retroactively and no
message is hidden from anyone by the act of upgrading. Non-NULL names the one
person who sees the row in a chat surface.

WHY NOT `visibility`. The 2026-08-05 backlog entry assumed the tagging
contract's `visibility` field would carry this. It cannot, for two reasons that
are facts about this table rather than preferences:

  1. `team_messages` has no `visibility` column at all. The column with the
     'private'/'team'/'org'/'public' CHECK lives on a DIFFERENT table,
     `messages` (app/models/message.py:18).
  2. `visibility = 'private'` names no owner, and an agent's reply row has
     `author_user_id` NULL by construction (app/models/team_message.py:48) — so
     the owner would need a second column anyway, and two fields meaning one
     thing disagree the first time either changes.

WHAT IT IS NOT. It is not privacy from the team's brain. The note still lands in
the team's memory and every member can recall it; this flag governs the chat
surface only. That is the owner's decision of 2026-08-05, and it is asserted by
a test so a later "fix" breaks loudly rather than quietly.

The FK is ON DELETE SET NULL to match `author_user_id`. That choice has a
consequence worth stating: deleting a user turns their hidden notes back into
ordinary team-visible rows. It is the safe direction for a chat surface — the
alternative, rows nobody can ever read, is unreachable data — but it is a real
behaviour, not an accident.

Additive, forward-only (Phase-17 pattern), no backfill, and NO branch on the
install-edition flag — the schema is identical for every edition (asserted by
tests/test_migration_editions.py::test_no_migration_branches_on_edition). Both
statements are idempotent; `downgrade()` is present for symmetry only.

Revision ID: 0034_team_message_private_lane
Revises: 0033_team_agent_provider
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034_team_message_private_lane"
down_revision: Union[str, None] = "0033_team_agent_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_team_messages_private_to_user_id"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE team_messages ADD COLUMN IF NOT EXISTS private_to_user_id "
        "UUID REFERENCES users(id) ON DELETE SET NULL"
    )
    # Partial: the overwhelming majority of rows are NULL and the read predicate
    # short-circuits on them through the existing (team_id, created_at) path.
    # This index serves the other side — "show me my own hidden notes" — without
    # paying for an entry per ordinary message.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_INDEX} ON team_messages "
        "(private_to_user_id, team_id, created_at DESC) "
        "WHERE private_to_user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE team_messages DROP COLUMN IF EXISTS private_to_user_id")

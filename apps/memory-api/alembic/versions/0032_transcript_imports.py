"""transcript_imports — the ledger that makes a re-import a no-op.

Importing the same ChatGPT conversation twice must not double the brain. The
identity is derived from the SOURCE conversation (its own id where the format
has one, otherwise a sha256 over the normalised turns) and recorded here, one
row per (team, conversation). The UNIQUE index is the dedupe: the import path
does an `INSERT ... ON CONFLICT DO NOTHING RETURNING id`, and "no row came
back" IS the duplicate verdict — a check-then-insert would race two shortcuts
fired from a phone in the same second.

Scoped per TEAM on purpose. The same conversation may legitimately be imported
into two different teams (it is two different pieces of team knowledge, tagged
and isolated separately); what must never happen is two copies inside one team.

`imported_by` is ON DELETE SET NULL rather than CASCADE: deleting an account
must not silently reopen the door to re-importing everything that account ever
brought in.

Revision ID: 0032_transcript_imports
Revises: 0031_api_token_capability
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0032_transcript_imports"
down_revision: Union[str, None] = "0031_api_token_capability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS transcript_imports (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_scope              TEXT NOT NULL,
            dedupe_key              TEXT NOT NULL,
            source_format           TEXT NOT NULL,
            source_conversation_id  TEXT,
            title                   TEXT,
            imported_by             UUID REFERENCES users(id) ON DELETE SET NULL,
            turn_count              INTEGER NOT NULL DEFAULT 0,
            queued_count            INTEGER NOT NULL DEFAULT 0,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # THE dedupe. Unique on (team, identity) — not on identity alone.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_transcript_imports_team_key "
        "ON transcript_imports(team_scope, dedupe_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_transcript_imports_team_created "
        "ON transcript_imports(team_scope, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_transcript_imports_team_created")
    op.execute("DROP INDEX IF EXISTS uq_transcript_imports_team_key")
    op.execute("DROP TABLE IF EXISTS transcript_imports")

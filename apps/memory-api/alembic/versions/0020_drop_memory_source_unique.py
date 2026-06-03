"""Drop idx_memory_source_team_unique — `source` is metadata, not a unique key.

Quick task 260603 (memory_add 500 fix). Migration 0004 added
UNIQUE(source, team_scope) "for drive-sync idempotence", but:

  - NO code path ever used ON CONFLICT(source, team_scope). drive-sync does
    search-then-update by id; the generic provider.upsert uses
    ON CONFLICT(id) DO UPDATE — idempotency is the deterministic uuid5 id, not
    the source.
  - The constraint is therefore vestigial AND harmful: any path that writes
    multiple memory_items with the SAME source per team hits a
    UniqueViolationError -> 500. This broke the active mcp-brain `memory_add`
    tool (source="mcp-brain") and silently dropped the 2nd+ brain_ingest
    messages per source (fire-and-forget swallowed the error).

Dropping the index restores "many facts per source" (the correct semantics).
Idempotency is unchanged (still ON CONFLICT(id) + drive-sync search-then-update).

Revision ID: 0020_drop_memory_source_unique
Revises: 0019_github_app_install
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020_drop_memory_source_unique"
down_revision: Union[str, None] = "0019_github_app_install"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS — the index may already have been dropped manually on the live
    # VM (260603 hotfix). Keep the migration idempotent.
    op.execute("DROP INDEX IF EXISTS idx_memory_source_team_unique")


def downgrade() -> None:
    # Best-effort restore. NOTE: this will FAIL if duplicate (source, team_scope)
    # rows now exist (which is precisely why the index was dropped). Deduplicate
    # before rolling back if you truly need the constraint back.
    op.create_index(
        "idx_memory_source_team_unique",
        "memory_items",
        ["source", "team_scope"],
        unique=True,
    )

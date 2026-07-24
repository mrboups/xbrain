"""boards + board_docs — collaborative Excalidraw board persistence (Phase 26a BOARD-01, D-26-02).

Creates TWO tables:

  * `boards` — the metadata row. It is needed regardless of where the blob lives: to
    list a team's boards, to resolve `board_id -> team_id` at token-mint time (the
    membership re-check that is gate 2 of the phase's team-scope invariant), and to
    soft-delete. `deleted_at` follows the Phase-11 soft-delete convention.
  * `board_docs` — ONE compacted Y.Doc update per board, as raw `bytea`.

Why the blob is a SIBLING table and not a `bytea` column on `boards`: a `bytea` in the
same row as the metadata drags the blob through TOAST on every `SELECT` for a list view.
Splitting keeps `GET /v1/teams/{id}/boards` cheap — the list query never touches
`board_docs` at all.

Why there is exactly one row per board and no append log: Hocuspocus hands `store()` the
already-compacted FULL document state, and Yjs garbage-collects deleted content
internally, so overwriting the single `state` row on each debounced store IS the
compaction strategy. Never store an append log here — `fetch()` must return byte-for-byte
what `store()` was given or the document history is silently corrupted.

Why the unique index on `is_default` is PARTIAL: the schema must NOT forbid more than one
board per team (26-CONTEXT "Claude's Discretion"). `ux_boards_team_default` therefore
constrains only rows where `is_default AND deleted_at IS NULL` — a team may hold MANY
boards while at most one live default exists, which is what makes the create endpoint
idempotent. 26a ships that single default; multi-board UI is a later increment with
nothing to migrate.

Additive, forward-only (Phase-17 pattern): CREATE TABLE / CREATE INDEX with IF NOT
EXISTS, no data backfill, and NO branch on the install-edition flag — the tables are
created unconditionally so the schema is identical for every edition (asserted by
tests/test_migration_editions.py::test_no_migration_branches_on_edition). Raw SQL +
`gen_random_uuid()` (pgcrypto is preloaded in the containers), mirroring
0027_team_invite_codes. The `downgrade()` is present for symmetry only; release
validation never invokes it.

Revision ID: 0028_boards
Revises: 0027_team_invite_codes
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0028_boards"
down_revision: Union[str, None] = "0027_team_invite_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS boards (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id            UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            title              TEXT NOT NULL DEFAULT 'Team board',
            is_default         BOOLEAN NOT NULL DEFAULT false,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at         TIMESTAMPTZ              -- soft delete, Phase-11 convention
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_boards_team_id ON boards (team_id)")
    # PARTIAL unique index: at most ONE live default board per team, while the table
    # still allows a team to own many boards (see module docstring).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_boards_team_default "
        "ON boards (team_id) WHERE is_default AND deleted_at IS NULL"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS board_docs (
            board_id   UUID PRIMARY KEY REFERENCES boards(id) ON DELETE CASCADE,
            state      BYTEA NOT NULL,          -- ONE compacted Y.Doc update; Yjs GCs internally
            size_bytes INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS board_docs")
    op.execute("DROP INDEX IF EXISTS ux_boards_team_default")
    op.execute("DROP INDEX IF EXISTS ix_boards_team_id")
    op.execute("DROP TABLE IF EXISTS boards")

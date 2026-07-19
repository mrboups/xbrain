"""team_invite_codes — Slack/Discord-style shared join codes for a team (Phase 25 JOINCODE-01, D-25-02/D-25-05).

Creates the `team_invite_codes` TABLE (a table, not a column — a team may have many
live codes, each with its own role/expiry/max-uses/revocation). A join code is a
bearer secret to the team-scoped brain: only its sha256 HASH is stored (`code_hash`,
UNIQUE-indexed — the redeem lookup is by hash, no plaintext timing oracle, D-25-01);
the plaintext is returned to the admin once at mint and never persisted. `uses` is
incremented atomically at each redeem (repos.team_invite_codes.redeem_atomic) so a
concurrent double-redeem can never exceed `max_uses`.

Additive, forward-only (Phase-17 pattern): CREATE TABLE / CREATE INDEX with IF NOT
EXISTS, no data backfill, and NO branch on the install-edition flag — the table is
created unconditionally so the schema is identical for every edition (asserted by
tests/test_migration_editions.py::test_no_migration_branches_on_edition). Tables are
created with raw SQL + `gen_random_uuid()` (pgcrypto is preloaded in the containers),
mirroring 0022_oauth_as_tables. The `downgrade()` is present for symmetry only;
release validation never invokes it.

Revision ID: 0027_team_invite_codes
Revises: 0026_team_member_last_read
Create Date: 2026-07-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0027_team_invite_codes"
down_revision: Union[str, None] = "0026_team_member_last_read"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS team_invite_codes (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id            UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            code_hash          TEXT NOT NULL,           -- sha256(plaintext) hex — store ONLY this
            code_prefix        TEXT NOT NULL,           -- non-secret display prefix
            role               VARCHAR(16) NOT NULL DEFAULT 'member',
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            expires_at         TIMESTAMPTZ,             -- NULL = never expires
            max_uses           INTEGER,                 -- NULL = unlimited
            uses               INTEGER NOT NULL DEFAULT 0,
            revoked_at         TIMESTAMPTZ,             -- soft-revoke; row stays for audit
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT team_invite_codes_role_check CHECK (role IN ('admin','member'))
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_team_invite_codes_code_hash "
        "ON team_invite_codes (code_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_invite_codes_team_id "
        "ON team_invite_codes (team_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_team_invite_codes_team_id")
    op.execute("DROP INDEX IF EXISTS ix_team_invite_codes_code_hash")
    op.execute("DROP TABLE IF EXISTS team_invite_codes")

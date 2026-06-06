"""OAuth 2.1 Authorization Server storage — clients, codes, access tokens.

Backs the Custom-Connector OAuth layer (quick-260604-glo): memory-api becomes
the OAuth 2.1 Authorization Server. These three tables are SEPARATE from
``user_api_tokens`` (xbt_ personal keys) on purpose — connector tokens carry an
audience (``resource``), a bound ``team_scope``, and a fixed
``source='claude.ai-connector'`` tag, none of which belong on the xbt_ table.

Revision ID: 0022_oauth_as_tables
Revises: 0021_brain_events_media
Create Date: 2026-06-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022_oauth_as_tables"
down_revision: Union[str, None] = "0021_brain_events_media"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # OAuth registered clients (Dynamic Client Registration — RFC 7591).
    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id     TEXT PRIMARY KEY,
            client_name   TEXT,
            redirect_uris TEXT[] NOT NULL,
            grant_types   TEXT[] NOT NULL DEFAULT '{authorization_code,refresh_token}',
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Short-lived, one-time authorization codes (Authorization Code + PKCE).
    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
            code           TEXT PRIMARY KEY,
            client_id      TEXT NOT NULL,
            user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            team_scope     TEXT NOT NULL,
            resource       TEXT NOT NULL,          -- audience binding (RFC 8707)
            code_challenge TEXT NOT NULL,          -- PKCE S256 challenge
            redirect_uri   TEXT NOT NULL,
            scope          TEXT NOT NULL,
            expires_at     TIMESTAMPTZ NOT NULL,   -- short TTL (~5 min)
            used_at        TIMESTAMPTZ             -- set on first exchange (one-time)
        )
    """)

    # Issued OAuth access tokens (+ paired refresh token). Only hashes stored.
    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_access_tokens (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash               TEXT NOT NULL UNIQUE,   -- SHA-256 of raw oat_ token
            client_id                TEXT NOT NULL,
            user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            team_scope               TEXT NOT NULL,          -- single bound team
            resource                 TEXT NOT NULL,          -- audience claim (normalized)
            scope                    TEXT NOT NULL,
            source                   TEXT NOT NULL DEFAULT 'claude.ai-connector',
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at               TIMESTAMPTZ,
            revoked_at               TIMESTAMPTZ,
            refresh_token_hash       TEXT UNIQUE,
            refresh_token_expires_at TIMESTAMPTZ
        )
    """)

    # Indexed lookups for introspection + refresh rotation.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_hash "
        "ON oauth_access_tokens(token_hash) WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_refresh "
        "ON oauth_access_tokens(refresh_token_hash) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_oauth_access_tokens_refresh")
    op.execute("DROP INDEX IF EXISTS idx_oauth_access_tokens_hash")
    op.execute("DROP TABLE IF EXISTS oauth_access_tokens")
    op.execute("DROP TABLE IF EXISTS oauth_authorization_codes")
    op.execute("DROP TABLE IF EXISTS oauth_clients")

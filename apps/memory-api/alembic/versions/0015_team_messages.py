"""team_messages table for quick task 260512-tcr (team chat realtime).

Stores both user and agent (Claude / future GPT, Grok) messages with the xbrain
tagging contract carried through metadata. Forward-compat fields for Phase 2
(threads via parent_message_id, edit/delete) are added nullable so the table
doesn't need a second migration when those features land.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_messages (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id           UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            author_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            agent_name        VARCHAR(64),
            kind              VARCHAR(16) NOT NULL,
            content           TEXT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Provenance: how a Claude reply was routed.
            -- NULL for user messages, 'user_promax' or 'team_api' for agent messages.
            routed_via        VARCHAR(32),
            -- Free-form metadata: token_usage, claude.ai conversation_id,
            -- prompt cache stats, etc. JSONB lets agents annotate freely.
            metadata          JSONB NOT NULL DEFAULT '{}'::JSONB,
            -- Phase 2 forward-compat: threads, edit, delete.
            -- All nullable so the table stays unchanged when those land.
            parent_message_id UUID REFERENCES team_messages(id) ON DELETE SET NULL,
            edited_at         TIMESTAMPTZ,
            deleted_at        TIMESTAMPTZ,

            CONSTRAINT ck_team_messages_kind
                CHECK (kind IN ('user', 'agent')),
            CONSTRAINT ck_team_messages_author_required
                CHECK (
                    (kind = 'user'  AND author_user_id IS NOT NULL)
                 OR (kind = 'agent' AND agent_name     IS NOT NULL)
                )
        )
        """
    )
    # Pagination index — list_messages_before queries the latest 50 for a
    # given team newest first, then walks older pages via WHERE created_at < ?.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_messages_team_created "
        "ON team_messages(team_id, created_at DESC)"
    )
    # Threads (Phase 2) — covers "fetch all replies in a thread" queries.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_messages_parent "
        "ON team_messages(parent_message_id) "
        "WHERE parent_message_id IS NOT NULL"
    )
    # Author-scoped queries ("show me everything Alice posted").
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_messages_author "
        "ON team_messages(author_user_id, created_at DESC) "
        "WHERE author_user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_team_messages_author")
    op.execute("DROP INDEX IF EXISTS idx_team_messages_parent")
    op.execute("DROP INDEX IF EXISTS idx_team_messages_team_created")
    op.execute("DROP TABLE IF EXISTS team_messages")

"""tasks.source — accept the Claude.ai connector provenance tag.

Closes the connector-task-write gap from quick-260604-glo: mcp-brain's
``task_create`` tags connector-originated tasks with
``source='claude.ai-connector'`` (locked tagging-contract decision — no hedge),
but the ``tasks.source`` column was ``VARCHAR(16)`` with a CHECK limited to
('granola','agent','chat','manual'). The tag is 20 chars, so both the width and
the CHECK rejected it. This widens the column to 32 and extends the allowed set.

The ``v_brain_events`` view (Brain Monitor, Phase 11) SELECTs ``tasks.source``,
so Postgres refuses ``ALTER COLUMN ... TYPE`` while the view exists
("cannot alter type of a column used by a view or rule"). We capture the LIVE
view definition, drop it, widen the column, then recreate the view verbatim —
no hardcoded view body, so it stays correct across environments. If the view is
absent (fresh DB before Phase 11), we just widen the column directly.

Forward-only / additive: existing rows and sources are untouched.

Revision ID: 0023_tasks_source_connector
Revises: 0022_oauth_as_tables
Create Date: 2026-06-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0023_tasks_source_connector"
down_revision: Union[str, None] = "0022_oauth_as_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Widen the column to fit the 20-char connector tag. tasks.source is
    # referenced by the v_brain_events view, so drop+recreate it around the
    # type change (capturing its live definition).
    op.execute(
        """
        DO $$
        DECLARE v_def text;
        BEGIN
            SELECT pg_get_viewdef('v_brain_events'::regclass, true) INTO v_def;
            EXECUTE 'DROP VIEW v_brain_events';
            EXECUTE 'ALTER TABLE tasks ALTER COLUMN source TYPE VARCHAR(32)';
            EXECUTE 'CREATE VIEW v_brain_events AS ' || v_def;
        EXCEPTION WHEN undefined_table THEN
            EXECUTE 'ALTER TABLE tasks ALTER COLUMN source TYPE VARCHAR(32)';
        END $$;
        """
    )
    # Extend the allowed-source CHECK to include the connector provenance tag.
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_source_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_source_check "
        "CHECK (source IN ('granola','agent','chat','manual','claude.ai-connector'))"
    )


def downgrade() -> None:
    # Revert the CHECK to the original set. NOTE: if any connector-tagged rows
    # exist they must be reassigned/removed first, or the column shrink fails.
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_source_check")
    op.execute(
        """
        DO $$
        DECLARE v_def text;
        BEGIN
            SELECT pg_get_viewdef('v_brain_events'::regclass, true) INTO v_def;
            EXECUTE 'DROP VIEW v_brain_events';
            EXECUTE 'ALTER TABLE tasks ALTER COLUMN source TYPE VARCHAR(16)';
            EXECUTE 'CREATE VIEW v_brain_events AS ' || v_def;
        EXCEPTION WHEN undefined_table THEN
            EXECUTE 'ALTER TABLE tasks ALTER COLUMN source TYPE VARCHAR(16)';
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_source_check "
        "CHECK (source IN ('granola','agent','chat','manual'))"
    )

"""Migration 0017 schema lock — Phase 11 BMO-01.

These integration tests run against a fresh testcontainers Postgres after
`alembic upgrade head` (see `tests/conftest.py::pg_url`). They lock the schema
contract introduced by `0017_brain_monitor_base`:

  1. All 12 new columns exist (3 truth_level + 5 deleted_at + 6 deleted_by, minus
     pre-existing columns) across the 6 target tables.
  2. The new CHECK constraints reject invalid truth_level values.
  3. The DDL `server_default` for `tasks.truth_level` is `'WORKING'`.
  4. The new composite indexes exist.

If a future migration accidentally drops a column, weakens a CHECK, or removes
an index, these tests go red. Without them, plans 11-02 .. 11-11 would build
on a silently regressed schema.

Skipped automatically when Docker is unavailable (same gate as
`test_team_isolation.py`).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ─────────────────────────────────────────────────────────────────────────────
# Expected new columns introduced by migration 0017_brain_monitor_base.
#
# Each row = (table_name, column_name). Pre-existing columns (e.g.
# memory_items.truth_level from 0002) are NOT listed — only the ones 0017
# itself adds, so a regression that drops these specific columns surfaces here.
# ─────────────────────────────────────────────────────────────────────────────
EXPECTED_NEW_COLUMNS: list[tuple[str, str]] = [
    # memory_items: truth_level already from 0002
    ("memory_items", "deleted_at"),
    ("memory_items", "deleted_by"),
    # conversations: gets all three
    ("conversations", "truth_level"),
    ("conversations", "deleted_at"),
    ("conversations", "deleted_by"),
    # messages: truth_level already from 0001
    ("messages", "deleted_at"),
    ("messages", "deleted_by"),
    # team_messages: deleted_at already from 0015
    ("team_messages", "truth_level"),
    ("team_messages", "deleted_by"),
    # tasks: gets all three
    ("tasks", "truth_level"),
    ("tasks", "deleted_at"),
    ("tasks", "deleted_by"),
    # contacts: truth_level already from 0009
    ("contacts", "deleted_at"),
    ("contacts", "deleted_by"),
]

# Expected composite indexes introduced by 0017.
EXPECTED_INDEXES: list[tuple[str, str]] = [
    ("memory_items", "idx_memory_team_deleted"),
    ("conversations", "idx_conv_team_deleted"),
    ("messages", "idx_msg_team_deleted"),
    ("team_messages", "idx_team_messages_deleted"),
    ("tasks", "idx_tasks_team_deleted"),
    ("contacts", "idx_contacts_team_deleted"),
]


async def test_0017_all_new_columns_present(session) -> None:
    """Every column 0017 was supposed to add MUST exist after alembic upgrade head."""
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (table_name, column_name) IN (
                    SELECT * FROM unnest(:tables ::text[], :cols ::text[]) AS t(tn, cn)
                  )
                """
            ),
            {
                "tables": [t for t, _ in EXPECTED_NEW_COLUMNS],
                "cols": [c for _, c in EXPECTED_NEW_COLUMNS],
            },
        )
    ).fetchall()
    found = {(r.table_name, r.column_name) for r in rows}
    missing = set(EXPECTED_NEW_COLUMNS) - found
    assert not missing, (
        f"Migration 0017 left {len(missing)} columns unapplied: "
        f"{sorted(missing)}. Did a downstream migration drop them?"
    )
    # Belt-and-braces: 14 columns total in EXPECTED_NEW_COLUMNS — count must match.
    assert len(EXPECTED_NEW_COLUMNS) == 14
    assert len(found) == 14


async def test_0017_truth_level_default_is_working_for_tasks(
    session, seeded_two_teams
) -> None:
    """INSERT INTO tasks without truth_level → DDL default fills it as 'WORKING'."""
    team_a = seeded_two_teams["team_a"]
    # Insert a minimal valid task row — omit truth_level deliberately.
    inserted_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO tasks (team_scope, title, source)
                VALUES (:ts, :title, 'manual')
                RETURNING id, truth_level
                """
            ),
            {"ts": team_a.slug, "title": "phase-11 smoke task"},
        )
    ).fetchone()
    assert inserted_id is not None
    assert inserted_id.truth_level == "WORKING", (
        f"tasks.truth_level default should be 'WORKING' per CONTEXT.md but DDL "
        f"materialised {inserted_id.truth_level!r}"
    )


async def test_0017_check_constraint_rejects_bogus_truth_level(
    session, seeded_two_teams
) -> None:
    """INSERT a task with truth_level='BOGUS' → CheckViolation via tasks_truth_level_check."""
    team_a = seeded_two_teams["team_a"]
    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            sa.text(
                """
                INSERT INTO tasks (team_scope, title, source, truth_level)
                VALUES (:ts, :title, 'manual', 'BOGUS')
                """
            ),
            {"ts": team_a.slug, "title": "should-fail"},
        )
    # asyncpg raises CheckViolationError; SQLAlchemy wraps it in IntegrityError.
    # The constraint name should appear in the message to anchor on a stable id.
    msg = str(excinfo.value).lower()
    assert "tasks_truth_level_check" in msg, (
        f"Expected CHECK constraint 'tasks_truth_level_check' to fire, got: {msg!r}"
    )


async def test_0017_composite_indexes_created(session) -> None:
    """Each (table, idx_..._team_deleted) MUST exist in pg_indexes."""
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT tablename, indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = ANY(:names)
                """
            ),
            {"names": [name for _, name in EXPECTED_INDEXES]},
        )
    ).fetchall()
    found = {(r.tablename, r.indexname) for r in rows}
    missing = set(EXPECTED_INDEXES) - found
    assert not missing, (
        f"Migration 0017 should have created {len(EXPECTED_INDEXES)} indexes; "
        f"missing: {sorted(missing)}"
    )

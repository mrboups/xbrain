"""Migration 0018 view lock — Phase 11 BMO-02.

Locks the contract of the `v_brain_events` SQL view introduced by
`0018_brain_events_view`. The view is the universal event surface every brain-
monitor endpoint (built in plan 11-04) queries. Without these tests, a future
migration that drops a UNION arm or renames a normalised column would silently
hide an entity type from the monitor and we'd only notice in production.

Locked invariants:

  1. The view exists in `pg_views` (the migration ran and the view is
     queryable through `information_schema`).
  2. With one fixture row in each of the 6 base tables (and one extra
     memory_items row with `source='granola'`), `SELECT DISTINCT entity_type
     FROM v_brain_events WHERE team_scope = :slug` returns ALL 7 entity_types:
     `memory_item`, `granola_note`, `conversation`, `message`, `team_message`,
     `task`, `contact`. Adding a new entity arm to the view in a later phase
     requires updating both the migration AND this test — that coupling is
     intentional.
  3. The `preview` column truncates content/title to 200 characters
     (`LEFT(..., 200)`). A row inserted with 250-char content surfaces 200
     chars exactly. Catches accidental `LEFT(..., 100)` or no-truncation
     regressions.
  4. The `deleted_at` column passes through as NULL by default — a fresh
     insert is visible in the view without a `deleted_at IS NULL` filter
     because every base table starts the column NULL. This pins the soft-
     delete contract at the view layer.
  5. The `team_messages` arm's JOIN on `teams.id = team_messages.team_id`
     correctly surfaces `t.slug AS team_scope` so callers can filter
     uniformly by slug across all 7 entity types — even for team_messages
     which uses `team_id` (UUID) natively.
  6. The 5 new composite indexes `(team_scope, created_at DESC)` exist on
     memory_items, conversations, messages, tasks, and contacts. Backs the
     cursor pagination path on the view.

Skipped automatically when Docker is unavailable — same gate as
`test_migration_0017.py`.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# Every entity_type literal the view is contractually obliged to surface
# when the 6 source tables each have at least one row (and memory_items has
# one with source='granola' to trigger the granola_note CASE branch).
EXPECTED_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "memory_item",
        "granola_note",
        "conversation",
        "message",
        "team_message",
        "task",
        "contact",
    }
)

# Composite (team_scope, created_at DESC) indexes that back the view's
# predicate pushdown for cursor pagination. team_messages already had
# idx_team_messages_team_created from migration 0015 — not re-listed here
# because 0018 didn't create it.
EXPECTED_NEW_INDEXES: list[tuple[str, str]] = [
    ("memory_items", "idx_memory_team_created"),
    ("conversations", "idx_conv_team_created"),
    ("messages", "idx_msg_team_created"),
    ("tasks", "idx_tasks_team_created"),
    ("contacts", "idx_contacts_team_created"),
]


async def _seed_one_row_per_entity(session, *, team_a) -> dict[str, str]:
    """Insert one row in each of the 6 base tables (plus the granola variant).

    Returns a dict of `{entity_type: entity_id}` so individual assertions can
    pinpoint specific rows in the view. All rows are scoped to `team_a.slug`
    so a single `WHERE team_scope = :slug` filter selects exactly the seeded
    set.
    """
    seeded: dict[str, str] = {}

    # ── memory_item (source='chat' → entity_type='memory_item') ──────────
    mi_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items (team_scope, content, source)
                VALUES (:ts, :content, 'chat')
                RETURNING id
                """
            ),
            {"ts": team_a.slug, "content": "memory item smoke"},
        )
    ).scalar_one()
    seeded["memory_item"] = str(mi_id)

    # ── granola_note (source='granola' → entity_type='granola_note') ─────
    gn_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items (team_scope, content, source)
                VALUES (:ts, :content, 'granola')
                RETURNING id
                """
            ),
            {"ts": team_a.slug, "content": "granola meeting smoke"},
        )
    ).scalar_one()
    seeded["granola_note"] = str(gn_id)

    # ── conversation ─────────────────────────────────────────────────────
    # owner_user_id is NOT NULL — reuse alice's id from the team_a creator.
    # team_a was created by alice (per `seeded_two_teams` fixture in
    # conftest.py), so the admin row in team_members points to alice.
    alice_id = (
        await session.execute(
            sa.text(
                """
                SELECT user_id FROM team_members
                WHERE team_id = :tid AND role = 'admin'
                LIMIT 1
                """
            ),
            {"tid": str(team_a.id)},
        )
    ).scalar_one()

    conv_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO conversations
                  (team_scope, owner_user_id, source, title)
                VALUES (:ts, :uid, 'librechat', :title)
                RETURNING id
                """
            ),
            {
                "ts": team_a.slug,
                "uid": str(alice_id),
                "title": "conversation smoke",
            },
        )
    ).scalar_one()
    seeded["conversation"] = str(conv_id)

    # ── message (child of the conversation we just inserted) ─────────────
    msg_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO messages
                  (conversation_id, team_scope, project_scope, visibility,
                   confidence, truth_level, source, validation_status,
                   role, content)
                VALUES
                  (:cid, :ts, NULL, 'team',
                   1.0, 'WORKING', 'librechat', 'pending',
                   'user', :content)
                RETURNING id
                """
            ),
            {
                "cid": str(conv_id),
                "ts": team_a.slug,
                "content": "message smoke",
            },
        )
    ).scalar_one()
    seeded["message"] = str(msg_id)

    # ── team_message (uses team_id, NOT team_scope) ──────────────────────
    # kind='user' requires author_user_id NOT NULL per ck_team_messages_author_required.
    tm_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO team_messages
                  (team_id, author_user_id, kind, content)
                VALUES (:tid, :uid, 'user', :content)
                RETURNING id
                """
            ),
            {
                "tid": str(team_a.id),
                "uid": str(alice_id),
                "content": "team_message smoke",
            },
        )
    ).scalar_one()
    seeded["team_message"] = str(tm_id)

    # ── task ─────────────────────────────────────────────────────────────
    # source must be in ('granola','agent','chat','manual') per tasks_source_check.
    tk_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO tasks (team_scope, title, source)
                VALUES (:ts, :title, 'manual')
                RETURNING id
                """
            ),
            {"ts": team_a.slug, "title": "task smoke"},
        )
    ).scalar_one()
    seeded["task"] = str(tk_id)

    # ── contact ──────────────────────────────────────────────────────────
    co_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO contacts
                  (team_scope, contact_type, source, full_name)
                VALUES (:ts, 'direct', 'manual', :full_name)
                RETURNING id
                """
            ),
            {"ts": team_a.slug, "full_name": "Smoke Contact"},
        )
    ).scalar_one()
    seeded["contact"] = str(co_id)

    return seeded


async def test_0018_view_exists(session) -> None:
    """The v_brain_events view MUST exist after `alembic upgrade head`."""
    row = (
        await session.execute(
            sa.text(
                "SELECT viewname FROM pg_views "
                "WHERE schemaname = 'public' AND viewname = 'v_brain_events'"
            )
        )
    ).fetchone()
    assert row is not None, (
        "v_brain_events view missing from pg_views — migration 0018 didn't run "
        "or a downstream migration dropped it without recreating it."
    )
    assert row.viewname == "v_brain_events"


async def test_0018_view_returns_all_7_entity_types(
    session, seeded_two_teams
) -> None:
    """With one row per base table seeded, the view exposes all 7 entity_types.

    This is the BMO-02 lock. If a future plan adds a UNION arm (e.g. when a
    new entity table joins the brain), both the migration AND this test must
    be updated — the coupling is intentional.
    """
    team_a = seeded_two_teams["team_a"]
    await _seed_one_row_per_entity(session, team_a=team_a)

    rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT entity_type FROM v_brain_events "
                "WHERE team_scope = :ts"
            ),
            {"ts": team_a.slug},
        )
    ).fetchall()
    found = frozenset(r.entity_type for r in rows)

    missing = EXPECTED_ENTITY_TYPES - found
    extra = found - EXPECTED_ENTITY_TYPES
    assert not missing, (
        f"v_brain_events missing entity_types {sorted(missing)} — "
        f"got {sorted(found)}. UNION arm regression?"
    )
    assert not extra, (
        f"v_brain_events returned unexpected entity_types {sorted(extra)} — "
        f"got {sorted(found)}. View added an arm without updating the test?"
    )


async def test_0018_view_preview_truncates_to_200_chars(
    session, seeded_two_teams
) -> None:
    """`preview` column emits at most 200 characters via LEFT(content, 200).

    Insert a memory_item with a 250-char content body and assert the view
    surfaces exactly 200 chars. Catches the "LEFT(..., 100)" typo and the
    "no truncation at all" regression.
    """
    team_a = seeded_two_teams["team_a"]
    long_body = "x" * 250
    item_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items (team_scope, content, source)
                VALUES (:ts, :content, 'test')
                RETURNING id
                """
            ),
            {"ts": team_a.slug, "content": long_body},
        )
    ).scalar_one()

    preview = (
        await session.execute(
            sa.text(
                "SELECT preview FROM v_brain_events WHERE entity_id = :eid"
            ),
            {"eid": str(item_id)},
        )
    ).scalar_one()
    assert preview is not None
    assert len(preview) == 200, (
        f"preview should be truncated to 200 chars (LEFT(content, 200)); "
        f"got len={len(preview)}"
    )
    assert preview == "x" * 200


async def test_0018_view_deleted_at_passes_through_as_null_by_default(
    session, seeded_two_teams
) -> None:
    """A row inserted without setting deleted_at MUST surface deleted_at IS NULL.

    Pins the soft-delete contract at the view layer — the 11-04 endpoint can
    rely on `WHERE deleted_at IS NULL` to default-hide soft-deleted rows
    without juggling NULL-vs-empty payload semantics.
    """
    team_a = seeded_two_teams["team_a"]
    item_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items (team_scope, content, source)
                VALUES (:ts, 'fresh row', 'test')
                RETURNING id
                """
            ),
            {"ts": team_a.slug},
        )
    ).scalar_one()

    row = (
        await session.execute(
            sa.text(
                "SELECT entity_id, deleted_at, deleted_by FROM v_brain_events "
                "WHERE entity_id = :eid"
            ),
            {"eid": str(item_id)},
        )
    ).fetchone()
    assert row is not None
    assert row.deleted_at is None, (
        "deleted_at must default to NULL — fresh inserts should not look soft-deleted"
    )
    assert row.deleted_by is None, "deleted_by must default to NULL"


async def test_0018_view_team_messages_arm_normalises_team_scope(
    session, seeded_two_teams
) -> None:
    """team_messages uses team_id (UUID); the view MUST surface team_scope (slug).

    The JOIN `team_messages tm JOIN teams t ON t.id = tm.team_id` is the
    critical glue that lets brain-monitor callers filter all 7 entity types
    by a single `team_scope` slug — without this join, team_messages would
    be filterable only by UUID, breaking the universal feed.

    Insert one team_message and assert it's selectable by slug in the view.
    """
    team_a = seeded_two_teams["team_a"]
    alice_id = (
        await session.execute(
            sa.text(
                """
                SELECT user_id FROM team_members
                WHERE team_id = :tid AND role = 'admin'
                LIMIT 1
                """
            ),
            {"tid": str(team_a.id)},
        )
    ).scalar_one()
    tm_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO team_messages
                  (team_id, author_user_id, kind, content)
                VALUES (:tid, :uid, 'user', 'normalisation check')
                RETURNING id
                """
            ),
            {"tid": str(team_a.id), "uid": str(alice_id)},
        )
    ).scalar_one()

    row = (
        await session.execute(
            sa.text(
                "SELECT entity_type, team_scope, team_id FROM v_brain_events "
                "WHERE entity_id = :eid AND team_scope = :slug"
            ),
            {"eid": str(tm_id), "slug": team_a.slug},
        )
    ).fetchone()
    assert row is not None, (
        "team_message row was inserted but is invisible when filtered by slug — "
        "the JOIN on teams must be missing or broken in the view"
    )
    assert row.entity_type == "team_message"
    assert row.team_scope == team_a.slug
    assert str(row.team_id) == str(team_a.id)


async def test_0018_composite_indexes_created(session) -> None:
    """Each (table, idx_..._team_created) introduced by 0018 MUST exist."""
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
            {"names": [name for _, name in EXPECTED_NEW_INDEXES]},
        )
    ).fetchall()
    found = {(r.tablename, r.indexname) for r in rows}
    missing = set(EXPECTED_NEW_INDEXES) - found
    assert not missing, (
        f"Migration 0018 should have created {len(EXPECTED_NEW_INDEXES)} "
        f"composite (team_scope, created_at DESC) indexes; missing: "
        f"{sorted(missing)}"
    )

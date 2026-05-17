"""Phase 11 — universal v_brain_events SQL view + composite (team, created_at DESC) indexes.

Foundation for BMO-02: the brain monitor needs a single query surface across all
7+ brain-tracked entity types. This migration creates:

  1. Five composite indexes on (team_scope, created_at DESC) — one per table that
     lacks one — to back cursor pagination on the view. team_messages already has
     `idx_team_messages_team_created` from migration 0015 (on team_id, created_at
     DESC), so we don't recreate it.

  2. A non-materialised VIEW `v_brain_events` that UNION ALL-s the 6 base tables
     into the normalised projection mandated by 11-CONTEXT.md:

       entity_type | entity_id | team_id | team_scope | created_at | created_by
       | truth_level | deleted_at | deleted_by | preview (LEFT(content, 200))
       | source

     Every arm JOINs `teams` so the view exposes both `team_id` (UUID) and
     `team_scope` (slug) — required because `team_messages` natively uses
     `team_id`, all others use `team_scope`. Callers can filter on either column
     uniformly across all entity types.

     `memory_items` is special-cased to surface as `entity_type='granola_note'`
     when `source='granola'` (per 11-RESEARCH.md §Q1: granola notes are stored
     as memory_items rows, not in a separate table). That gives the view the
     7 distinct entity_type values the brain monitor UI expects.

     `created_by` is NULL for entity types that have no author FK
     (memory_items, messages, contacts). The authorization helper built in 11-04
     must treat NULL `created_by` as admin-only edit (Option A from CONTEXT.md
     locked decision).

Why a separate migration (0018) rather than inlining the view in 0017:

  - The view references columns 0017 introduced (logical dependency); keeping
    them in distinct migrations makes rollback safer (DROP VIEW then DROP
    COLUMN works in one `alembic downgrade -2` if a future bug surfaces).
  - Future plans that add an entity type need to recreate the view (Postgres
    does NOT support ALTER VIEW ADD COLUMN). Keeping the view DDL in a
    dedicated migration makes the "drop-and-recreate" pattern obvious.

NOTE on view evolution: when a new entity table is added later, the
downstream migration MUST `DROP VIEW IF EXISTS v_brain_events;` then
`CREATE OR REPLACE VIEW v_brain_events AS ...` with the new UNION arm
appended. Document this in the migration's leading docstring.

Revision ID: 0018_brain_events_view
Revises: 0017_brain_monitor_base
Create Date: 2026-05-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018_brain_events_view"
down_revision: Union[str, None] = "0017_brain_monitor_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# View DDL kept as a module-level constant so the downgrade can DROP it by
# name and any future "add an entity arm" migration can import the prior
# definition for diffing without re-parsing this file.
_V_BRAIN_EVENTS_DDL = """
CREATE OR REPLACE VIEW v_brain_events AS
SELECT
  CASE WHEN mi.source = 'granola' THEN 'granola_note' ELSE 'memory_item' END AS entity_type,
  mi.id                                       AS entity_id,
  t.id                                        AS team_id,
  mi.team_scope                               AS team_scope,
  mi.created_at                               AS created_at,
  NULL::UUID                                  AS created_by,
  mi.truth_level                              AS truth_level,
  mi.deleted_at                               AS deleted_at,
  mi.deleted_by                               AS deleted_by,
  LEFT(mi.content, 200)                       AS preview,
  mi.source                                   AS source
FROM memory_items mi
JOIN teams t ON t.slug = mi.team_scope

UNION ALL

SELECT
  'conversation'                              AS entity_type,
  c.id, t.id, c.team_scope, c.created_at,
  c.owner_user_id                             AS created_by,
  c.truth_level, c.deleted_at, c.deleted_by,
  LEFT(COALESCE(c.title, c.source), 200)      AS preview,
  c.source
FROM conversations c
JOIN teams t ON t.slug = c.team_scope

UNION ALL

SELECT
  'message'                                   AS entity_type,
  m.id, t.id, m.team_scope, m.created_at,
  NULL::UUID                                  AS created_by,
  m.truth_level, m.deleted_at, m.deleted_by,
  LEFT(m.content, 200)                        AS preview,
  m.source
FROM messages m
JOIN teams t ON t.slug = m.team_scope

UNION ALL

SELECT
  'team_message'                              AS entity_type,
  tm.id,
  tm.team_id,
  t.slug                                      AS team_scope,
  tm.created_at,
  tm.author_user_id                           AS created_by,
  tm.truth_level, tm.deleted_at, tm.deleted_by,
  LEFT(tm.content, 200)                       AS preview,
  COALESCE(tm.routed_via, tm.kind)            AS source
FROM team_messages tm
JOIN teams t ON t.id = tm.team_id

UNION ALL

SELECT
  'task'                                      AS entity_type,
  tk.id, t.id, tk.team_scope, tk.created_at,
  tk.created_by,
  tk.truth_level, tk.deleted_at, tk.deleted_by,
  LEFT(tk.title, 200)                         AS preview,
  tk.source
FROM tasks tk
JOIN teams t ON t.slug = tk.team_scope

UNION ALL

SELECT
  'contact'                                   AS entity_type,
  co.id, t.id, co.team_scope, co.created_at,
  NULL::UUID                                  AS created_by,
  co.truth_level, co.deleted_at, co.deleted_by,
  LEFT(COALESCE(co.full_name, co.email, '(unnamed)'), 200) AS preview,
  co.source
FROM contacts co
JOIN teams t ON t.slug = co.team_scope
"""


def upgrade() -> None:
    # ── 1. Composite (team_scope, created_at DESC) indexes for cursor pagination ──
    # The view's natural read pattern is "newest first, scoped to team":
    #   WHERE team_scope = :ts ORDER BY created_at DESC LIMIT 50
    # Predicate pushdown through UNION ALL works only if each arm exposes
    # an index that matches both the WHERE column and the ORDER BY direction.
    # Use IF NOT EXISTS so the migration is re-runnable in case of partial
    # apply (and to defensively no-op on any environment that already shipped
    # the index out-of-band).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_team_created "
        "ON memory_items (team_scope, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_conv_team_created "
        "ON conversations (team_scope, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_team_created "
        "ON messages (team_scope, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_team_created "
        "ON tasks (team_scope, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contacts_team_created "
        "ON contacts (team_scope, created_at DESC)"
    )
    # idx_team_messages_team_created already exists from migration 0015
    # (team_id, created_at DESC). Skip — recreating it would be a no-op but
    # CREATE INDEX without IF NOT EXISTS would error and the IF NOT EXISTS
    # form keeps us defensive.

    # ── 2. v_brain_events view (UNION ALL across the 6 base tables) ──────
    op.execute(_V_BRAIN_EVENTS_DDL)


def downgrade() -> None:
    # Drop view first — composite indexes are independent of it but the
    # reverse-order discipline makes the intent obvious.
    op.execute("DROP VIEW IF EXISTS v_brain_events")

    # Drop the five new composite indexes in reverse order. team_messages
    # index was NOT created here, so don't drop it.
    op.execute("DROP INDEX IF EXISTS idx_contacts_team_created")
    op.execute("DROP INDEX IF EXISTS idx_tasks_team_created")
    op.execute("DROP INDEX IF EXISTS idx_msg_team_created")
    op.execute("DROP INDEX IF EXISTS idx_conv_team_created")
    op.execute("DROP INDEX IF EXISTS idx_memory_team_created")

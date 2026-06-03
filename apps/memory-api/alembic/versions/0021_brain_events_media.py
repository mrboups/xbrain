"""BL-003 slice 2 — add `media` column to v_brain_events view.

The `v_brain_events` view from migration 0018 projects a fixed set of
columns; it has no `media` column yet.  The Brain Monitor needs to know
whether a `memory_item` row carries a media blob so it can render an image
thumbnail or a file chip in the Preview cell.

This migration drops and recreates the view with a single new trailing
column on every arm:

  - memory_items arm: ``mi.metadata->'media' AS media``
    (returns the ``{key, mime, size, filename}`` JSON object if present,
    NULL otherwise).
  - All other arms (conversation, message, team_message, task, contact):
    ``NULL::jsonb AS media``
    (those entity types never carry media blobs).

The MinIO object key embedded in ``media.key`` is intentionally forwarded
through the view — the brain.py route layer strips it before sending the
field over the wire, replacing the raw key with a signed token URL (see
``mint_media_token`` in media_helpers.py).

Revision ID: 0021_brain_events_media
Revises: 0020_drop_memory_source_unique
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021_brain_events_media"
down_revision: Union[str, None] = "0020_drop_memory_source_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# New view DDL (0018 + media column on every arm)
# ---------------------------------------------------------------------------

_V_BRAIN_EVENTS_DDL_WITH_MEDIA = """
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
  mi.source                                   AS source,
  mi.metadata->'media'                        AS media
FROM memory_items mi
JOIN teams t ON t.slug = mi.team_scope

UNION ALL

SELECT
  'conversation'                              AS entity_type,
  c.id, t.id, c.team_scope, c.created_at,
  c.owner_user_id                             AS created_by,
  c.truth_level, c.deleted_at, c.deleted_by,
  LEFT(COALESCE(c.title, c.source), 200)      AS preview,
  c.source,
  NULL::jsonb                                 AS media
FROM conversations c
JOIN teams t ON t.slug = c.team_scope

UNION ALL

SELECT
  'message'                                   AS entity_type,
  m.id, t.id, m.team_scope, m.created_at,
  NULL::UUID                                  AS created_by,
  m.truth_level, m.deleted_at, m.deleted_by,
  LEFT(m.content, 200)                        AS preview,
  m.source,
  NULL::jsonb                                 AS media
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
  COALESCE(tm.routed_via, tm.kind)            AS source,
  NULL::jsonb                                 AS media
FROM team_messages tm
JOIN teams t ON t.id = tm.team_id

UNION ALL

SELECT
  'task'                                      AS entity_type,
  tk.id, t.id, tk.team_scope, tk.created_at,
  tk.created_by,
  tk.truth_level, tk.deleted_at, tk.deleted_by,
  LEFT(tk.title, 200)                         AS preview,
  tk.source,
  NULL::jsonb                                 AS media
FROM tasks tk
JOIN teams t ON t.slug = tk.team_scope

UNION ALL

SELECT
  'contact'                                   AS entity_type,
  co.id, t.id, co.team_scope, co.created_at,
  NULL::UUID                                  AS created_by,
  co.truth_level, co.deleted_at, co.deleted_by,
  LEFT(COALESCE(co.full_name, co.email, '(unnamed)'), 200) AS preview,
  co.source,
  NULL::jsonb                                 AS media
FROM contacts co
JOIN teams t ON t.slug = co.team_scope
"""


# ---------------------------------------------------------------------------
# Original 0018 view DDL — restored verbatim by downgrade()
# ---------------------------------------------------------------------------

_V_BRAIN_EVENTS_DDL_0018 = """
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
    # Postgres does not support ALTER VIEW ADD COLUMN — DROP + CREATE is the
    # required pattern.  IF EXISTS is defensive (partial apply tolerance).
    op.execute("DROP VIEW IF EXISTS v_brain_events")
    op.execute(_V_BRAIN_EVENTS_DDL_WITH_MEDIA)


def downgrade() -> None:
    # Restore the 0018 view (no media column).
    op.execute("DROP VIEW IF EXISTS v_brain_events")
    op.execute(_V_BRAIN_EVENTS_DDL_0018)

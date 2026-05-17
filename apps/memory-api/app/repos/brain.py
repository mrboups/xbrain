"""Phase 11 — brain repo: typed dispatch from (entity_type, entity_id) to underlying tables.

The Brain Monitor surfaces 7 entity types through a single `v_brain_events` view
(migration 0018). Mutation endpoints (PATCH truth_level / DELETE soft / restore
— plan 11-05) need to write back to the right base table. This module owns:

  1. The closed allow-list of entity types and their physical table names.
  2. A single `fetch_event_row` lookup that goes through the view so the team-
     scope check matches the read path exactly.
  3. Per-mutation SQL writes (`update_truth_level`, `soft_delete_entity`,
     `restore_entity`) with the 30-day retention window enforced server-side.

Why a separate repo (instead of inlining the SQL in `routes/brain.py`):

  - The router stays thin and the auth + audit + Qdrant-sync ordering reads
    in one screen instead of being interleaved with table dispatch.
  - The retention window (30 days) and the 404-vs-410 distinction on restore
    live in one place — the route handler can stay agnostic of the calendar
    rule, just propagating whatever HTTPException the repo raises.
  - Future entity types only need an addition to `ENTITY_TABLE_MAP`; the
    route layer is untouched.

f-string safety note: `update_truth_level`, `soft_delete_entity` and
`restore_entity` interpolate the table name into raw SQL because asyncpg
cannot parameterise an identifier. `_resolve_table()` enforces a closed
allow-list of hard-coded strings, so user input never reaches the f-string.
Any unknown entity_type raises HTTPException(400) before the SQL is built.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession


# Closed allow-list — keys are user-supplied via the URL path, values are
# hard-coded table names. Any unknown entity_type raises 400 at the route
# layer before this resolver runs, so the f-string substitution downstream
# is safe (no user-controlled table-name interpolation).
#
# `granola_note` maps to `memory_items` because the v_brain_events view
# (migration 0018) special-cases memory_items rows with `source='granola'`
# as their own entity_type. The route handler calls `fetch_event_row` first
# — that goes through the view's WHERE filter, so a non-granola memory_item
# cannot be reached via entity_type='granola_note' (returns 404).
ENTITY_TABLE_MAP: dict[str, str] = {
    "memory_item": "memory_items",
    "granola_note": "memory_items",
    "conversation": "conversations",
    "message": "messages",
    "team_message": "team_messages",
    "task": "tasks",
    "contact": "contacts",
}

ALLOWED_ENTITY_TYPES = frozenset(ENTITY_TABLE_MAP.keys())


def _resolve_table(entity_type: str) -> str:
    """Map a Brain Monitor entity_type to its underlying physical table.

    Raises HTTPException(400) for any value outside the closed allow-list —
    this is the only sanitiser between the URL path and the f-string SQL
    substitutions below.
    """
    if entity_type not in ENTITY_TABLE_MAP:
        raise HTTPException(400, f"unknown entity_type: {entity_type}")
    return ENTITY_TABLE_MAP[entity_type]


async def fetch_event_row(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    team_slug: str,
) -> Mapping | None:
    """Fetch a single brain-event row scoped to (entity_type, entity_id, team_slug).

    Returns the raw mapping from `v_brain_events` (uniform schema across all
    7 entity types) or None when no row matches.

    The view's WHERE filter — `entity_type=:et AND entity_id=:id AND
    team_scope=:ts` — enforces both:
      * cross-team scope (a member of team-a cannot reach team-b rows), and
      * the granola_note special case: passing `entity_type='granola_note'`
        for a non-granola memory_item returns None (the view tags those as
        `entity_type='memory_item'`).

    Soft-deleted rows ARE visible here. The view does not filter on
    `deleted_at` — that is the caller's responsibility. PATCH/DELETE require
    a non-deleted row; restore requires a soft-deleted one. Keeping the
    filter in the route handler avoids two near-duplicate fetch helpers.
    """
    sql = """
        SELECT * FROM v_brain_events
        WHERE entity_type = :et AND entity_id = :id AND team_scope = :ts
    """
    return (await session.execute(
        sa.text(sql), {"et": entity_type, "id": str(entity_id), "ts": team_slug}
    )).mappings().fetchone()


async def update_truth_level(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    new_value: str,
) -> bool:
    """Set truth_level on the underlying base table.

    Returns True when a row was updated, False otherwise. The route handler
    already validated the (entity_type, entity_id, team_scope) tuple via
    `fetch_event_row`, so a False return here typically means the row
    vanished between the fetch and the update (rare race; respond with
    404 at the route layer).

    Caller is responsible for `session.commit()`.

    The Pydantic-layer regex on `TruthLevelPatchBody.truth_level` plus the
    per-table DB CHECK constraint together prevent an invalid value from
    reaching here.
    """
    table = _resolve_table(entity_type)
    sql = f"UPDATE {table} SET truth_level = :tl WHERE id = :id RETURNING id"
    result = await session.execute(
        sa.text(sql), {"tl": new_value, "id": str(entity_id)}
    )
    return result.first() is not None


async def soft_delete_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    deleted_by_user_id: UUID | None,
) -> datetime:
    """Set deleted_at = now() (Python clock) and deleted_by on the row.

    Returns the deleted_at timestamp so the route handler can pass the same
    wall-clock value to `NativeProvider.mark_deleted()` for the Qdrant
    payload (RESEARCH §Q3: PG and Qdrant clocks must not drift on the
    soft-delete moment, otherwise the daily janitor sees ghost rows).

    Raises HTTPException(404) if no row was updated — either the entity id
    is unknown or it was already soft-deleted by a parallel request.

    `deleted_by_user_id` is nullable to support bridge service JWTs (no
    associated user). The base tables permit NULL via
    `ON DELETE SET NULL` references.

    Caller is responsible for `session.commit()`.
    """
    table = _resolve_table(entity_type)
    # Use Python clock (not `NOW()`) so we can hand the exact same value to
    # Qdrant — preventing PG/Qdrant clock skew on the soft-delete moment.
    now = datetime.now(tz=timezone.utc)
    sql = f"""
        UPDATE {table}
        SET deleted_at = :now, deleted_by = :uid
        WHERE id = :id AND deleted_at IS NULL
        RETURNING id
    """
    result = await session.execute(
        sa.text(sql),
        {
            "now": now,
            "uid": str(deleted_by_user_id) if deleted_by_user_id else None,
            "id": str(entity_id),
        },
    )
    if result.first() is None:
        raise HTTPException(404, "entity not found or already deleted")
    return now


async def restore_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
) -> bool:
    """Clear deleted_at + deleted_by IFF deleted_at > now() - 30 days.

    Returns True on successful restore.

    Raises HTTPException(410) Gone when the row is outside the 30-day
    retention window (already eligible for the daily janitor's hard-purge
    — restoring after that point would create a Postgres/Qdrant ghost
    because the Qdrant point may already be gone).

    Raises HTTPException(404) when no row exists at all for this entity id
    (distinct from "exists but isn't soft-deleted" or "exists and is
    outside window" — the route handler will surface a clearer error).

    Caller is responsible for `session.commit()`.
    """
    table = _resolve_table(entity_type)
    sql = f"""
        UPDATE {table}
        SET deleted_at = NULL, deleted_by = NULL
        WHERE id = :id
          AND deleted_at IS NOT NULL
          AND deleted_at > NOW() - INTERVAL '30 days'
        RETURNING id
    """
    result = await session.execute(sa.text(sql), {"id": str(entity_id)})
    if result.first() is not None:
        return True

    # Distinguish: nonexistent row (404) vs not-soft-deleted (404 — nothing
    # to restore) vs outside the 30-day window (410 Gone — restore was
    # technically valid until the janitor passed it).
    check = (await session.execute(
        sa.text(f"SELECT deleted_at FROM {table} WHERE id = :id"),
        {"id": str(entity_id)},
    )).fetchone()
    if check is None:
        # No such row.
        raise HTTPException(404, "entity not found")
    if check.deleted_at is None:
        # Row exists but isn't deleted — nothing to restore.
        raise HTTPException(404, "entity is not soft-deleted")
    # Row exists and is soft-deleted but the timestamp is outside the
    # retention window.
    raise HTTPException(
        410, "retention window expired (>30 days since soft-delete)"
    )

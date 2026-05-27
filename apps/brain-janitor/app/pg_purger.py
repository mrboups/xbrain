"""Postgres hard-delete + audit log writer.

For each Phase 11 brain-tracked table that exposes `deleted_at`, the purger:
  1. Opens a single transaction.
  2. Issues `DELETE ... WHERE deleted_at < now() - $1 RETURNING id` per table,
     collecting the UUIDs that were physically removed.  ``$1`` is a
     ``datetime.timedelta`` — asyncpg serialises it natively to the Postgres
     ``interval`` wire type.  Passing a plain str like ``"30 days"`` causes
     asyncpg to call ``.days`` on the argument (str has no ``.days``),
     resulting in ``'str' object has no attribute 'days'`` at runtime.
  3. Writes ONE audit_log row summarising the batch (per-table counts in payload).
  4. Commits.

Idempotency: subsequent runs find nothing to delete because rows are physically
gone. Adding `deleted_at < now() - retention` is itself the dedup key.

audit_log schema (apps/memory-api/alembic/versions/0001_initial.py): columns are
`id, ts, actor_user_id, team_scope, action, target_id, payload`. There is NO
entity_type / entity_id pair. The plan's draft INSERT used those — we use the
real schema instead and put the per-table summary in the JSONB payload.
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

import asyncpg
import structlog

log = structlog.get_logger(__name__)

# Tables purged by brain-janitor. Each tuple is (table_name, team_scope_column).
# memory_items must come FIRST so we have its UUIDs handy for the Qdrant +
# Neo4j purgers (which only care about memory_items).
PURGE_TABLES: list[tuple[str, str]] = [
    ("memory_items", "team_scope"),
    ("messages", "team_scope"),
    ("conversations", "team_scope"),
    ("team_messages", "team_id"),
    ("tasks", "team_scope"),
    ("contacts", "team_scope"),
]


def _pg_url(database_url: str) -> str:
    """Strip the SQLAlchemy async dialect prefix asyncpg cannot parse."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )


async def purge_pg(
    pool: asyncpg.Pool, retention_days: int
) -> dict[str, list[UUID]]:
    """Hard-delete soft-deleted rows older than retention_days from every brain table.

    Returns a dict {table_name: list_of_purged_ids}. Caller passes the
    ``memory_items`` list to the Qdrant + Neo4j purgers downstream.

    Single transaction so an exception mid-way rolls everything back and the
    next run retries the whole batch (Postgres is the source of truth).
    """
    purged: dict[str, list[UUID]] = {}
    # Use datetime.timedelta so asyncpg serialises it to the Postgres interval
    # wire type directly.  Passing a plain str like "30 days" causes asyncpg to
    # call .days on the argument (str has no .days), producing:
    #   'str' object has no attribute 'days'
    # This was the bug causing every daily purge run to abort since 2026-05-18.
    interval = timedelta(days=int(retention_days))

    async with pool.acquire() as conn:
        async with conn.transaction():
            for table, _scope_col in PURGE_TABLES:
                rows = await conn.fetch(
                    f"DELETE FROM {table} "
                    f"WHERE deleted_at IS NOT NULL "
                    f"  AND deleted_at < now() - $1::interval "
                    f"RETURNING id",
                    interval,
                )
                purged[table] = [r["id"] for r in rows]
                if rows:
                    log.info(
                        "brain_janitor.pg_purged",
                        table=table,
                        count=len(rows),
                    )

            counts = {t: len(v) for t, v in purged.items()}
            total = sum(counts.values())
            if total > 0:
                # ONE audit row per run, summarising the batch. team_scope='system'
                # marks this as a platform-level event (no specific team owns it).
                await conn.execute(
                    "INSERT INTO audit_log (actor_user_id, team_scope, action, target_id, payload) "
                    "VALUES (NULL, 'system', 'brain_janitor.purge', NULL, $1::jsonb)",
                    json.dumps(
                        {
                            "retention_days": retention_days,
                            "counts": counts,
                            "total": total,
                        }
                    ),
                )
                log.info(
                    "brain_janitor.audit_logged",
                    total=total,
                    counts=counts,
                )

    return purged

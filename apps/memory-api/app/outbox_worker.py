"""Background asyncio task — drains neo4j_outbox to Neo4j every 2s.

Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple memory-api workers (--workers 2)
don't race on the same outbox rows.

Pattern:
  - Fetch up to 50 unprocessed rows per tick
  - Execute Cypher query via neo4j driver
  - Mark processed=true, set processed_at=now()
  - On Cypher error: set error=<message>, processed=true (avoids infinite retry on bad Cypher)
  - Sleep 2s between ticks

Threat model:
  - T-03-04-01 (DoS / busy-loop): asyncio.sleep(2) between ticks; CancelledError handled cleanly.
  - T-03-04-02 (Cypher injection): params passed as **kwargs to execute_query(), no string interpolation.
  - T-03-04-03 (info disclosure): error stored in DB only, not surfaced via public API.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)

BATCH_SIZE = 50
DRAIN_INTERVAL_SECONDS = 2


async def drain_outbox(pg_dsn: str) -> None:
    """Long-running coroutine. Launched as asyncio.Task in FastAPI lifespan.

    Args:
        pg_dsn: asyncpg-compatible DSN (same as settings.DATABASE_URL).
    """
    import asyncpg  # noqa: PLC0415 — lazy import to avoid hard dep at module load

    from app.neo4j_client import get_driver

    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    log.info("outbox_worker.started")
    try:
        while True:
            try:
                driver = get_driver()
                if driver is None:
                    # Neo4j not configured — nothing to drain
                    await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
                    continue

                async with pool.acquire() as conn:
                    async with conn.transaction():
                        rows = await conn.fetch(
                            """
                            SELECT id, cypher, params FROM neo4j_outbox
                            WHERE processed = false
                            ORDER BY created_at
                            LIMIT $1
                            FOR UPDATE SKIP LOCKED
                            """,
                            BATCH_SIZE,
                        )

                        for row in rows:
                            cypher = row["cypher"]
                            raw_params = row["params"]
                            params: dict = (
                                json.loads(raw_params)
                                if isinstance(raw_params, str)
                                else dict(raw_params)
                            )
                            try:
                                await driver.execute_query(
                                    cypher,
                                    database_="neo4j",
                                    **params,
                                )
                                await conn.execute(
                                    "UPDATE neo4j_outbox SET processed=true, processed_at=$2 WHERE id=$1",
                                    row["id"],
                                    datetime.now(timezone.utc),
                                )
                            except Exception as exc:
                                # Mark as processed with error — avoids infinite retry on bad Cypher
                                log.error(
                                    "outbox_worker.cypher_error",
                                    id=str(row["id"]),
                                    error=str(exc),
                                )
                                await conn.execute(
                                    "UPDATE neo4j_outbox SET processed=true, processed_at=$2, error=$3 WHERE id=$1",
                                    row["id"],
                                    datetime.now(timezone.utc),
                                    str(exc)[:2000],
                                )

                if rows:
                    log.info("outbox_worker.drained", count=len(rows))

            except asyncio.CancelledError:
                raise  # propagate to outer try/except for clean shutdown
            except Exception as exc:
                log.error("outbox_worker.tick_error", error=str(exc))

            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        log.info("outbox_worker.cancelled")
    finally:
        await pool.close()

"""brain-janitor entry point — daily 03:00 UTC hard-purge cycle.

Phase 11 plan 11-07. Boots, runs one cycle immediately (so verify-phase11.sh
and a fresh `docker compose up -d brain-janitor` get instant signal), then
loops forever sleeping until next 03:00 UTC.

A `/tmp/brain-janitor-alive` sentinel file is touched after every successful
cycle — the docker-compose healthcheck reads its mtime to mark the container
unhealthy if a daily run hasn't fired in ~25 hours.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from datetime import datetime, timedelta, timezone

import asyncpg
import structlog

from app.config import Settings
from app.neo4j_purger import purge_neo4j
from app.pg_purger import _pg_url, purge_pg
from app.qdrant_purger import purge_qdrant

SENTINEL_PATH = pathlib.Path("/tmp/brain-janitor-alive")


def _configure_logging(level: str) -> None:
    """Wire structlog to stdout at the configured level."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
    )
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        )
    )


log = structlog.get_logger(__name__)


def _next_run_at(now: datetime, hour: int = 3) -> datetime:
    """Return the next datetime at `hour`:00:00 UTC strictly after `now`."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


async def run_once(settings: Settings) -> None:
    """One purge cycle: PG → Qdrant → Neo4j → sentinel touch."""
    pool = await asyncpg.create_pool(
        _pg_url(settings.DATABASE_URL), min_size=1, max_size=2
    )
    try:
        purged = await purge_pg(pool, settings.RETENTION_DAYS)
        mem_ids = purged.get("memory_items", [])
        if mem_ids:
            await purge_qdrant(
                settings.QDRANT_URL,
                settings.QDRANT_COLLECTION,
                mem_ids,
            )
            await purge_neo4j(
                settings.NEO4J_URI,
                settings.NEO4J_USER,
                settings.NEO4J_PASSWORD,
                mem_ids,
            )
        SENTINEL_PATH.touch()
        log.info(
            "brain_janitor.run_complete",
            purged_counts={t: len(v) for t, v in purged.items()},
            sentinel=str(SENTINEL_PATH),
        )
    finally:
        await pool.close()


async def main() -> None:
    settings = Settings()
    _configure_logging(settings.LOG_LEVEL)
    log.info(
        "brain_janitor.boot",
        retention_days=settings.RETENTION_DAYS,
        qdrant_collection=settings.QDRANT_COLLECTION,
    )

    # First run at boot — gives verify-phase11.sh instant evidence the loop
    # works without waiting for the 03:00 UTC schedule.
    try:
        await run_once(settings)
    except Exception as exc:  # noqa: BLE001 — keep loop alive across crashes
        log.error("brain_janitor.boot_run_failed", error=str(exc))

    while True:
        now = datetime.now(tz=timezone.utc)
        next_run = _next_run_at(now)
        sleep_s = (next_run - now).total_seconds()
        log.info(
            "brain_janitor.sleep",
            next_run_utc=next_run.isoformat(),
            seconds=int(sleep_s),
        )
        await asyncio.sleep(sleep_s)
        try:
            await run_once(settings)
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            log.error("brain_janitor.run_failed", error=str(exc))


if __name__ == "__main__":
    asyncio.run(main())

"""Neo4j AsyncGraphDatabase driver — singleton, graceful degrade if not configured.

If NEO4J_URI or NEO4J_PASSWORD is empty, all functions are no-ops (returns None).
This lets memory-api boot in dev environments without a running Neo4j instance.

Threat model:
- T-03-04-02: Cypher injection avoided — callers pass params dict, no string interpolation.
- Driver shared as singleton between outbox_worker and graph routes (plan 03-05).
- T-15-05-01..04 (Phase 15): reconnect_loop() below restores the ordering guarantee that
  `depends_on: neo4j: {condition: service_healthy}` used to provide before it had to be removed
  (Compose forbids an untagged core service from depending on a profile-tagged one). See
  reconnect_loop()'s docstring for the full story.
"""
from __future__ import annotations

import asyncio

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_driver = None


async def init_driver(quiet: bool = False):
    """Create and verify the AsyncGraphDatabase driver. Called from lifespan, and again by
    reconnect_loop() until it succeeds.

    Returns the driver if connected, None if not (graceful degrade — never raises).

    `quiet=True` downgrades the connectivity-failure log from ERROR to DEBUG. Retry attempts use it:
    an OSS-light install has NEO4J_URI set (docker-compose.yml passes it as a bare literal) but no
    Neo4j container, so every retry would otherwise emit an ERROR. Six ERRORs on every default boot
    trains operators to ignore errors.
    """
    global _driver
    if not (settings.NEO4J_URI and settings.NEO4J_PASSWORD):
        log.warning(
            "neo4j.disabled",
            reason="NEO4J_URI or NEO4J_PASSWORD not set — graph sync disabled",
        )
        return None
    from neo4j import AsyncGraphDatabase  # lazy import — avoid import error if not installed

    _driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        await _driver.verify_connectivity()
        log.info("neo4j.connected", uri=settings.NEO4J_URI)
    except Exception as exc:
        if quiet:
            log.debug("neo4j.connectivity_failed", error=str(exc))
        else:
            log.error("neo4j.connectivity_failed", error=str(exc))
        # Don't crash — memory-api is still usable without Neo4j
        await _driver.close()
        _driver = None
    return _driver


async def reconnect_loop(attempts: int = 6, interval_s: float = 20.0) -> None:
    """Keep trying to reach Neo4j in the background until connected, or until the budget runs out.

    WHY THIS EXISTS. Phase 15 removed memory-api's `depends_on: neo4j: {condition: service_healthy}`
    edge — Docker Compose forbids an untagged (core) service from depending on a profile-tagged one, and
    with that edge present the compose file does not parse at all once `profiles:` are applied. But that
    edge was ALSO the only thing guaranteeing Neo4j was healthy before init_driver() ran. Without it, a
    cold `docker compose --profile integrations up` races: memory-api attempts the connection within
    seconds while Neo4j is still starting (its healthcheck has a 60s start_period), the attempt fails,
    _driver stays None, and NOTHING retries — graph sync would be silently dead for the whole life of the
    process. `clean-start.sh` does exactly this cold start, and it is run regularly.

    Budget: attempts x interval_s = 2 minutes by default, which comfortably covers Neo4j's 60s
    start_period. Bounded on purpose: NEO4J_URI is truthy even in an OSS-light install that will never
    have a Neo4j (docker-compose.yml's default is a bare literal), so an unbounded loop would retry a
    doomed connection forever.

    Non-blocking on purpose: this runs as a background task, so /v1/healthz answers 200 throughout. An
    OSS-light boot must not pay minutes of retry for a graph it does not want.
    """
    if not (settings.NEO4J_URI and settings.NEO4J_PASSWORD):
        return  # not configured — nothing to reconnect to, and init_driver already said so
    for attempt in range(1, attempts + 1):
        if get_driver() is not None:
            return  # connected (either the initial attempt succeeded, or a previous retry did)
        await asyncio.sleep(interval_s)
        if get_driver() is not None:
            return
        log.debug("neo4j.reconnect_attempt", attempt=attempt, of=attempts)
        await init_driver(quiet=True)
    if get_driver() is None:
        log.warning(
            "neo4j.reconnect_exhausted",
            attempts=attempts,
            window_s=attempts * interval_s,
            reason=(
                "Neo4j is configured (NEO4J_URI/NEO4J_PASSWORD are set) but was not reachable within "
                "the reconnect window. Graph sync is DISABLED for this process: graph routes return "
                "503 and entity rows are not enqueued. This is EXPECTED in an OSS-light install (the "
                "`integrations` profile is off and NEO4J_URI simply has a default value). If you DID "
                "enable `integrations`, restart memory-api once Neo4j is healthy."
            ),
        )


async def close_driver():
    """Close the driver. Called from lifespan cleanup."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def get_driver():
    """Return the live driver or None if not configured/connected."""
    return _driver

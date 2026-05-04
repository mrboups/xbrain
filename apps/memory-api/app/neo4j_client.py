"""Neo4j AsyncGraphDatabase driver — singleton, graceful degrade if not configured.

If NEO4J_URI or NEO4J_PASSWORD is empty, all functions are no-ops (returns None).
This lets memory-api boot in dev environments without a running Neo4j instance.

Threat model:
- T-03-04-02: Cypher injection avoided — callers pass params dict, no string interpolation.
- Driver shared as singleton between outbox_worker and graph routes (plan 03-05).
"""
from __future__ import annotations

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_driver = None


async def init_driver():
    """Create and verify AsyncGraphDatabase driver. Called from lifespan.

    Returns driver if configured, None if not (graceful degrade).
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
        log.error("neo4j.connectivity_failed", error=str(exc))
        # Don't crash — memory-api is still usable without Neo4j
        await _driver.close()
        _driver = None
    return _driver


async def close_driver():
    """Close the driver. Called from lifespan cleanup."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def get_driver():
    """Return the live driver or None if not configured/connected."""
    return _driver

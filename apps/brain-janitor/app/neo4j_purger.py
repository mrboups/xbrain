"""Neo4j detach-delete for purged memory_items.

The xbrain Neo4j graph stores entity nodes keyed by `external_id` (the PG
memory_items.id as a string). When a memory_items row is hard-deleted from PG,
we DETACH DELETE the matching graph node — removing it AND every relation
attached to it in one Cypher statement.

If the node never existed (e.g. the memory_item never produced an extraction
worth graphing), the Cypher is a no-op — safe.

Fail-soft: per-id try/except so a single bad node doesn't abort the batch.
Connection-level failures (Neo4j down) are caught at the top — we log and
return; the next run will retry. PG is source of truth, so orphan PG-IDs in
Neo4j are a stale-graph annoyance, not a correctness bug.
"""
from __future__ import annotations

from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


async def purge_neo4j(
    uri: str,
    user: str,
    password: str,
    memory_item_ids: list[UUID],
) -> int:
    """DETACH DELETE every node {external_id: $id} for the given IDs.

    Returns the number of IDs processed (not the number of nodes actually
    deleted — Cypher doesn't surface that without an extra RETURN, and we
    treat 'no match' as success).
    """
    if not memory_item_ids:
        return 0

    # Lazy import — neo4j driver is heavy and only needed when there's work.
    from neo4j import AsyncGraphDatabase

    deleted = 0
    try:
        async with AsyncGraphDatabase.driver(uri, auth=(user, password)) as drv:
            async with drv.session() as sess:
                for mid in memory_item_ids:
                    try:
                        await sess.run(
                            "MATCH (n {external_id: $id}) DETACH DELETE n",
                            id=str(mid),
                        )
                        deleted += 1
                    except Exception as exc:  # noqa: BLE001 per-id resilience
                        log.warning(
                            "brain_janitor.neo4j_purge_id_failed",
                            id=str(mid),
                            error=str(exc),
                        )
    except Exception as exc:  # noqa: BLE001 connection-level failure
        log.warning(
            "brain_janitor.neo4j_purge_failed",
            count=len(memory_item_ids),
            error=str(exc),
        )
        return 0

    log.info(
        "brain_janitor.neo4j_purged",
        count=deleted,
    )
    return deleted

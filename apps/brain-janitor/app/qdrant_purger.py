"""Qdrant point-delete for purged memory_items.

memory_items.id is used verbatim as PointStruct.id by the canonical writer in
packages/memory-models/xbrain_memory/providers/native_provider.py — so a hard
PG delete of a memory_items row only needs to drop the same UUID from the
Qdrant `messages` collection.

Fail-soft semantics: if Qdrant is unreachable or refuses the delete, we log a
warning and return. The PG row is already gone (source of truth), and the
retrieval-side soft-delete filter from plan 11-03 (`deleted_at_ts > 0`) would
have already hidden the point from search results — so an orphan Qdrant point
is a wasted storage byte, not a correctness bug. The next janitor run will not
re-attempt this specific UUID (PG row gone), but if we want we can later add
an "orphan reaper" sweeping Qdrant for points whose IDs are no longer in PG.
That's explicitly out of scope for 11-07.
"""
from __future__ import annotations

from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


async def purge_qdrant(
    qdrant_url: str,
    collection: str,
    memory_item_ids: list[UUID],
    *,
    qdrant_api_key: str = "",
) -> int:
    """Delete the given memory_items UUIDs from the Qdrant collection.

    Returns the number of points the client was asked to delete (best-effort;
    Qdrant reports affected count in its response but we don't surface it).
    Returns 0 if the list is empty (no-op, no client constructed).
    """
    if not memory_item_ids:
        return 0

    # Lazy imports — keep this module importable on hosts without qdrant_client
    # (unit-test runners that mock the client don't need the real install).
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http.models import PointIdsList

    point_ids = [str(u) for u in memory_item_ids]
    client = AsyncQdrantClient(url=qdrant_url, api_key=qdrant_api_key or None)
    try:
        await client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=point_ids),
        )
        log.info(
            "brain_janitor.qdrant_purged",
            collection=collection,
            count=len(point_ids),
        )
        return len(point_ids)
    except Exception as exc:  # broad: Qdrant may raise httpx, grpc, value errors
        log.warning(
            "brain_janitor.qdrant_purge_failed",
            collection=collection,
            count=len(point_ids),
            error=str(exc),
        )
        return 0
    finally:
        await client.close()

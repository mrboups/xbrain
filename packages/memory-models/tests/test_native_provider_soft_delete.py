"""Phase 11 plan 11-03 — Qdrant soft-delete payload contract tests.

Two layers of coverage:

  1. **Unit layer (NativeStubProvider, runs always):** locks the in-process
     bookkeeping for `mark_deleted` / `mark_restored`. Goes red if a future
     refactor of the stub forgets to honour the soft-delete marker in
     `search()`. Runs in every CI environment because the stub has no
     external dependencies.

  2. **Integration layer (raw Qdrant, runs only when QDRANT_URL is set
     and reachable):** locks the actual Qdrant payload contract that
     `NativeProvider.upsert()` and `NativeProvider.search()` rely on:

       Case 1 — `Range(lte=0.0)` filter EXCLUDES points where
                `deleted_at_ts > 0.0` (the mark_deleted scenario).
       Case 2 — `Range(lte=0.0)` filter INCLUDES points where
                `deleted_at_ts == 0.0` (fresh upsert + after
                mark_restored).
       Case 3 — `Range(lte=0.0)` filter EXCLUDES points where the
                `deleted_at_ts` payload field is ABSENT (the legacy /
                pre-Phase-11 point case). This is the exact failure mode
                the backfill script `infrastructure/scripts/
                backfill_qdrant_deleted_at.py` prevents in production.

     Skipped automatically when Qdrant is unreachable — identical SKIP
     pattern to `apps/memory-api/tests/test_migration_0017.py` and the
     rest of the xbrain integration suite (no Docker in dev / no live
     Qdrant in CI ⇒ SKIPPED, never FAILED).

Why no NativeProvider end-to-end test against PG + Qdrant: the soft-
delete contract this plan owns lives entirely in the Qdrant payload and
the search filter. The PG hydration step (`SELECT * FROM memory_items
WHERE id = ANY(...)`) is plan 11-06's territory. Mixing the two layers
in one test would couple this plan to a PG fixture for no incremental
contract value — the integration test above covers the Qdrant contract
fully, the unit test above covers the stub contract fully, and 11-06's
test suite covers the PG-side filter.

Pin assumption: qdrant-client 1.17.1 (CLAUDE.md). The integration test
uses a unique collection name per run (uuid4-suffixed) and tears it
down in a fixture, so concurrent runs and crashed-mid-test runs do not
leak shared state.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from xbrain_memory.providers.native_stub import NativeStubProvider
from xbrain_memory.types import MemoryItem, TruthLevel


# --------------------------------------------------------------------------
# Unit layer — NativeStubProvider, no external deps. Always runs.
# --------------------------------------------------------------------------


def _make_item(team: str, content: str, **overrides) -> MemoryItem:
    now = datetime.now(timezone.utc)
    base = dict(
        id="",
        team_scope=team,
        content=content,
        source="test:11-03-soft-delete",
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return MemoryItem(**base)


@pytest.mark.asyncio
async def test_stub_mark_deleted_excludes_from_search():
    """After mark_deleted, the item disappears from search results."""
    p = NativeStubProvider()
    keep_id = await p.upsert(_make_item("team-a", "alpha apple keep"))
    drop_id = await p.upsert(_make_item("team-a", "alpha apple drop"))

    hits_before = await p.search("apple", team_scope="team-a")
    ids_before = {h.item.id for h in hits_before}
    assert keep_id in ids_before and drop_id in ids_before, (
        f"both items must be findable before mark_deleted (got {ids_before})"
    )

    await p.mark_deleted(drop_id, datetime.now(timezone.utc))

    hits_after = await p.search("apple", team_scope="team-a")
    ids_after = {h.item.id for h in hits_after}
    assert keep_id in ids_after, "non-deleted item must remain"
    assert drop_id not in ids_after, "soft-deleted item must be hidden"


@pytest.mark.asyncio
async def test_stub_mark_restored_re_includes_in_search():
    """mark_restored undoes mark_deleted; the item reappears in search."""
    p = NativeStubProvider()
    item_id = await p.upsert(_make_item("team-a", "banana to restore"))

    await p.mark_deleted(item_id, datetime.now(timezone.utc))
    assert not [
        h for h in await p.search("banana", team_scope="team-a")
        if h.item.id == item_id
    ], "item should be hidden after mark_deleted"

    await p.mark_restored(item_id)
    hits = await p.search("banana", team_scope="team-a")
    assert any(h.item.id == item_id for h in hits), (
        "item must be visible again after mark_restored"
    )


@pytest.mark.asyncio
async def test_stub_mark_deleted_idempotent():
    """Re-marking an already-deleted item is a no-op (same end state)."""
    p = NativeStubProvider()
    item_id = await p.upsert(_make_item("team-a", "cherry idempotent"))

    t1 = datetime.now(timezone.utc) - timedelta(seconds=5)
    t2 = datetime.now(timezone.utc)
    await p.mark_deleted(item_id, t1)
    await p.mark_deleted(item_id, t2)  # idempotent overwrite — must not raise

    hits = await p.search("cherry", team_scope="team-a")
    assert not [h for h in hits if h.item.id == item_id], (
        "item must remain hidden after repeated mark_deleted calls"
    )


# --------------------------------------------------------------------------
# Integration layer — raw Qdrant, SKIPPED when QDRANT_URL is unreachable.
# --------------------------------------------------------------------------


def _qdrant_reachable() -> bool:
    """Best-effort reachability probe — runs in test discovery time.

    Returns True iff `qdrant-client` is importable AND a TCP connection
    to QDRANT_URL succeeds. Either condition fails ⇒ tests SKIP.
    """
    try:
        from qdrant_client import QdrantClient  # noqa: F401
    except Exception:
        return False
    url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=url, timeout=3.0)
        client.get_collections()
        client.close()
        return True
    except Exception:
        return False


_QDRANT_SKIP_REASON = (
    "Qdrant not reachable at $QDRANT_URL (default http://localhost:6333). "
    "Skipping plan 11-03 integration tests — identical SKIP pattern to "
    "test_migration_0017.py and the rest of the xbrain integration suite. "
    "These tests run live on the VM where Qdrant is available."
)
# Module-level probe cached so each integration test does not re-open a TCP
# connection. Probe runs at collection time, identical to other integration
# suites in the repo. Unit tests above are NOT decorated with this skip —
# they exercise NativeStubProvider and have no external dependencies.
_QDRANT_REACHABLE = _qdrant_reachable()
_qdrant_skip = pytest.mark.skipif(
    not _QDRANT_REACHABLE, reason=_QDRANT_SKIP_REASON
)


@pytest.fixture
def qdrant_fixture():
    """Create a uuid-suffixed Qdrant collection; yield (client, name); drop on teardown."""
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, VectorParams

    url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    api_key = os.environ.get("QDRANT_API_KEY", "").strip() or None
    coll = f"xbrain_test_softdelete_{uuid.uuid4().hex[:12]}"

    client = QdrantClient(url=url, api_key=api_key, timeout=10.0)
    client.create_collection(
        collection_name=coll,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    try:
        yield client, coll
    finally:
        try:
            client.delete_collection(collection_name=coll)
        finally:
            client.close()


def _seed(client, coll: str, point_id: str, deleted_at_ts: float | None) -> None:
    """Upsert a single point. deleted_at_ts=None ⇒ omit the payload key (legacy)."""
    from qdrant_client.http.models import PointStruct

    payload: dict = {"team_scope": "test", "source": "test:11-03"}
    if deleted_at_ts is not None:
        payload["deleted_at_ts"] = deleted_at_ts
    client.upsert(
        collection_name=coll,
        points=[PointStruct(id=point_id, vector=[0.1, 0.2, 0.3, 0.4], payload=payload)],
        wait=True,
    )


def _search_with_filter(client, coll: str) -> set[str]:
    """Run the canonical NativeProvider search filter (team_scope + Range(lte=0.0))
    and return the matching point ids as a set of strings."""
    from qdrant_client.http.models import (
        FieldCondition,
        Filter,
        MatchValue,
        Range,
    )

    flt = Filter(
        must=[
            FieldCondition(key="team_scope", match=MatchValue(value="test")),
            FieldCondition(key="deleted_at_ts", range=Range(lte=0.0)),
        ]
    )
    results = client.search(
        collection_name=coll,
        query_vector=[0.1, 0.2, 0.3, 0.4],
        query_filter=flt,
        limit=100,
    )
    return {str(r.id) for r in results}


@_qdrant_skip
def test_qdrant_filter_includes_zero_excludes_positive(qdrant_fixture):
    """Case 1+2 — Range(lte=0.0) includes deleted_at_ts==0.0 and excludes >0.0."""
    client, coll = qdrant_fixture
    keep = str(uuid.uuid4())
    drop = str(uuid.uuid4())

    _seed(client, coll, keep, deleted_at_ts=0.0)
    _seed(client, coll, drop, deleted_at_ts=datetime.now(timezone.utc).timestamp())

    visible = _search_with_filter(client, coll)
    assert keep in visible, "non-deleted point (deleted_at_ts=0.0) must be visible"
    assert drop not in visible, "soft-deleted point (deleted_at_ts>0) must be excluded"


@_qdrant_skip
def test_qdrant_set_payload_mark_then_restore(qdrant_fixture):
    """Case 1+2 round-trip — set_payload flips visibility both ways."""
    client, coll = qdrant_fixture
    pid = str(uuid.uuid4())

    _seed(client, coll, pid, deleted_at_ts=0.0)
    assert pid in _search_with_filter(client, coll), (
        "freshly upserted point must be visible"
    )

    # Simulate mark_deleted — same set_payload call NativeProvider.mark_deleted makes
    client.set_payload(
        collection_name=coll,
        payload={"deleted_at_ts": datetime.now(timezone.utc).timestamp()},
        points=[pid],
        wait=True,
    )
    assert pid not in _search_with_filter(client, coll), (
        "after mark_deleted (epoch>0), the point must be excluded"
    )

    # Simulate mark_restored — same set_payload call NativeProvider.mark_restored makes
    client.set_payload(
        collection_name=coll,
        payload={"deleted_at_ts": 0.0},
        points=[pid],
        wait=True,
    )
    assert pid in _search_with_filter(client, coll), (
        "after mark_restored (epoch==0), the point must be visible again"
    )


@_qdrant_skip
def test_qdrant_filter_excludes_points_missing_payload_field(qdrant_fixture):
    """Case 3 — legacy-point simulation.

    A point whose payload lacks `deleted_at_ts` entirely is silently EXCLUDED
    by `Range(lte=0.0)`. This is the exact failure mode the backfill script
    `infrastructure/scripts/backfill_qdrant_deleted_at.py` prevents in
    production. The test seeds a legacy point (no deleted_at_ts key) and
    asserts the filter hides it — proving the deployment gate (run backfill
    BEFORE shipping the filter) is correct as documented in plan 11-03 §1b.

    A future schema change that switches storage to a sentinel-null pattern
    (e.g. IsEmpty/IsNull) would change this contract — and the failure here
    would warn future implementers about the production blast radius.
    """
    client, coll = qdrant_fixture

    legacy = str(uuid.uuid4())
    tagged = str(uuid.uuid4())

    _seed(client, coll, legacy, deleted_at_ts=None)  # NO deleted_at_ts key
    _seed(client, coll, tagged, deleted_at_ts=0.0)  # has the key

    visible = _search_with_filter(client, coll)
    assert tagged in visible, "tagged-as-not-deleted point must be visible"
    assert legacy not in visible, (
        "legacy point (missing deleted_at_ts) must be excluded — "
        "this is the exact failure mode the backfill script prevents"
    )

    # Apply the backfill operation directly — set_payload({deleted_at_ts: 0.0}).
    # The point must then become visible. This proves the backfill script's
    # operation is sufficient.
    client.set_payload(
        collection_name=coll,
        payload={"deleted_at_ts": 0.0},
        points=[legacy],
        wait=True,
    )
    visible_after = _search_with_filter(client, coll)
    assert legacy in visible_after, (
        "after the backfill operation, the legacy point must be visible"
    )

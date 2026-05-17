"""Phase 11 — one-shot Qdrant backfill: set deleted_at_ts=0.0 on every point.

Deployment gate (plan 11-03 §1b) — MANDATORY ordering:

    1.  Deploy the memory-api image carrying the `feat(memory-models): write
        deleted_at_ts to Qdrant payload on upsert` commit (Task 2). After
        this, every NEW upsert lands with `deleted_at_ts=0.0`.

    2.  Run THIS script against the live Qdrant instance — it scrolls every
        xbrain collection and sets `deleted_at_ts=0.0` on every existing
        point that lacks the field. Idempotent: re-running on tagged points
        overwrites with the same value (no harm).

    3.  Deploy the memory-api image carrying the `feat(memory-models):
        NativeProvider mark_deleted/mark_restored + soft-delete search
        filter` commit (Task 3 of plan 11-03). The new search filter
        `Range(lte=0.0)` would blackhole every pre-Phase-11 point if shipped
        BEFORE this backfill — Qdrant range filters do NOT match points
        where the payload field is absent (11-RESEARCH §Q3).

Usage on the VM:

    docker exec -e QDRANT_URL=http://qdrant:6333 \
        -e QDRANT_API_KEY=$QDRANT_API_KEY \
        xbrain-memory-api \
        python /app/infrastructure/scripts/backfill_qdrant_deleted_at.py

Alternative (copy script into image first):

    docker cp infrastructure/scripts/backfill_qdrant_deleted_at.py \
        xbrain-memory-api:/tmp/
    docker exec xbrain-memory-api python /tmp/backfill_qdrant_deleted_at.py

Acceptance:

    *  Script runs to completion without exception.
    *  For every collection processed, log shows
       `[backfill] OK: before_count=N == after_count=N`.
    *  Re-running prints the same OK lines (no-op).

Env vars:

    QDRANT_URL       required — e.g. http://qdrant:6333
    QDRANT_API_KEY   optional — defaults to unauthenticated (Phase 1 dev)
    QDRANT_BATCH     optional — default 1000; lower if memory-constrained
    QDRANT_COLLECTIONS optional — comma-separated allow-list. If unset, the
                     script discovers all collections via get_collections()
                     and processes every one. Set to "messages" to restrict
                     to xbrain's known collection.

This script is intentionally synchronous + standalone — uses the SYNC
`qdrant_client.QdrantClient` (not `AsyncQdrantClient`) so it can run
outside the FastAPI event loop without an async runner. The memory-api
container ships with `qdrant-client>=1.17` in its site-packages, so the
import resolves with no extra installs.

Pin assumption: qdrant-client 1.17.1 (CLAUDE.md). The `scroll` /
`set_payload` / `count` / `get_collections` API surface used here is
stable across the 1.x series.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

# Lazy import keeps the file py_compile-able on a dev machine without the
# qdrant client installed (tests / docs builds / planning machines).
def _client_factory(url: str, api_key: str | None):  # pragma: no cover - thin wrapper
    from qdrant_client import QdrantClient

    return QdrantClient(url=url, api_key=api_key or None)


DEFAULT_BATCH = 1000


def _resolve_collections(
    client, allow_list: list[str] | None
) -> list[str]:
    """Return the list of collections to backfill.

    If `allow_list` is provided (comma-split from QDRANT_COLLECTIONS), use it
    verbatim. Otherwise discover all collections via `get_collections()`.

    The plan's Section 3 Task 1 outline suggested filtering by name prefix
    (`startswith("memory_items")`) — kept as an OPTIONAL allow-list rather
    than a hard-coded filter, because xbrain's actual Qdrant collection
    today is named `messages` (see apps/memory-api/app/qdrant_setup.py:11)
    and a hard-coded prefix would skip it. Discovery is the safe default;
    the allow-list lets the operator narrow scope.
    """
    if allow_list:
        return allow_list
    return [c.name for c in client.get_collections().collections]


def _backfill_collection(client, coll: str, batch: int) -> tuple[int, int]:
    """Scroll the collection and set `deleted_at_ts=0.0` on every point.

    Returns (before_count, after_count). Asserts they're equal.
    """
    before_count = client.count(collection_name=coll, exact=True).count
    print(f"[backfill] {coll}: starting (count={before_count})", flush=True)

    offset = None
    total = 0
    while True:
        points, offset = client.scroll(
            collection_name=coll,
            limit=batch,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            break
        ids = [p.id for p in points]
        client.set_payload(
            collection_name=coll,
            payload={"deleted_at_ts": 0.0},
            points=ids,
        )
        total += len(ids)
        print(
            f"[backfill] {coll}: tagged {total}/{before_count}",
            flush=True,
        )
        if offset is None:
            break

    after_count = client.count(collection_name=coll, exact=True).count
    assert before_count == after_count, (
        f"[backfill] FAIL: {coll}: before_count={before_count} "
        f"!= after_count={after_count}"
    )
    print(
        f"[backfill] OK: before_count={before_count} == "
        f"after_count={after_count}",
        flush=True,
    )
    return before_count, after_count


def main(argv: Iterable[str] | None = None) -> int:
    qdrant_url = os.environ.get("QDRANT_URL")
    if not qdrant_url:
        print(
            "[backfill] FATAL: QDRANT_URL env var is required "
            "(e.g. http://qdrant:6333)",
            file=sys.stderr,
        )
        return 2

    api_key = os.environ.get("QDRANT_API_KEY", "").strip() or None
    batch = int(os.environ.get("QDRANT_BATCH", str(DEFAULT_BATCH)))
    allow_raw = os.environ.get("QDRANT_COLLECTIONS", "").strip()
    allow_list = (
        [c.strip() for c in allow_raw.split(",") if c.strip()]
        if allow_raw
        else None
    )

    client = _client_factory(qdrant_url, api_key)

    try:
        collections = _resolve_collections(client, allow_list)
        if not collections:
            print(
                "[backfill] no collections found — nothing to do",
                flush=True,
            )
            return 0

        print(
            f"[backfill] target collections: {collections}",
            flush=True,
        )

        for coll in collections:
            _backfill_collection(client, coll, batch)

        print("[backfill] DONE", flush=True)
        return 0
    finally:
        # QdrantClient holds an httpx pool — close on the way out to be neat.
        try:
            client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


if __name__ == "__main__":
    raise SystemExit(main())

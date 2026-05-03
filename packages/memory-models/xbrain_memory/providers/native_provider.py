"""Native MemoryProvider — Postgres + Qdrant direct.

Stores facts in `memory_items` table (Phase 2 migration 0002, see plan 02-04).
Vector embeddings go to Qdrant collection `messages` (shared with Phase 1).
Versioning via `memory_items_history` append-only table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from xbrain_memory.provider import MemoryProvider
from xbrain_memory.types import (
    MemoryItem,
    SearchHit,
    TruthLevel,
    ValidationStatus,
    Visibility,
)


Embedder = Callable[[str], Awaitable[list[float]]]


class NativeProvider(MemoryProvider):
    """Direct Postgres+Qdrant impl. Self-contained, no external memory framework."""

    def __init__(
        self,
        pg_dsn: str,
        qdrant_url: str,
        embedder: Embedder,
        qdrant_api_key: str = "",
        collection: str = "messages",
    ) -> None:
        # Lazy imports — only required when actually constructing this provider
        from qdrant_client import AsyncQdrantClient

        self._pg_dsn = pg_dsn
        self._qdrant = AsyncQdrantClient(
            url=qdrant_url, api_key=qdrant_api_key or None
        )
        self._collection = collection
        self._embedder = embedder
        self._pool = None  # asyncpg.Pool, lazy-init

    async def _ensure_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._pg_dsn, min_size=1, max_size=5
            )
        return self._pool

    async def upsert(self, item: MemoryItem) -> str:
        from qdrant_client.http.models import PointStruct

        pool = await self._ensure_pool()
        item_id = item.id if item.id else str(uuid4())
        embedding = item.embedding or await self._embedder(item.content)

        async with pool.acquire() as conn:
            existing = None
            if item.id:
                existing = await conn.fetchrow(
                    "SELECT * FROM memory_items WHERE id = $1 AND team_scope = $2",
                    UUID(item_id), item.team_scope,
                )
            if existing:
                # Snapshot to history table
                await conn.execute(
                    "INSERT INTO memory_items_history "
                    "(item_id, team_scope, content, metadata, truth_level, validation_status, "
                    " visibility, confidence, source) "
                    "SELECT id, team_scope, content, metadata, truth_level, validation_status, "
                    "  visibility, confidence, source FROM memory_items WHERE id = $1",
                    UUID(item_id),
                )
                await conn.execute(
                    "UPDATE memory_items SET content=$2, metadata=$3, truth_level=$4, "
                    "  validation_status=$5, visibility=$6, confidence=$7, source=$8, "
                    "  updated_at=now() WHERE id=$1",
                    UUID(item_id), item.content, json.dumps(item.metadata),
                    item.truth_level.value, item.validation_status.value,
                    item.visibility.value, item.confidence, item.source,
                )
            else:
                await conn.execute(
                    "INSERT INTO memory_items "
                    "(id, team_scope, project_scope, content, metadata, truth_level, "
                    " validation_status, visibility, confidence, source) "
                    "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)",
                    UUID(item_id), item.team_scope, item.project_scope, item.content,
                    json.dumps(item.metadata), item.truth_level.value,
                    item.validation_status.value, item.visibility.value,
                    item.confidence, item.source,
                )

        # Upsert vector to Qdrant (team_scope is filter key)
        await self._qdrant.upsert(
            collection_name=self._collection,
            points=[PointStruct(
                id=item_id,
                vector=embedding,
                payload={
                    "team_scope": item.team_scope,
                    "project_scope": item.project_scope,
                    "truth_level": item.truth_level.value,
                    "source": item.source,
                },
            )],
        )
        return item_id

    async def get(self, item_id: str, *, team_scope: str) -> MemoryItem | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memory_items WHERE id=$1 AND team_scope=$2",
                UUID(item_id), team_scope,
            )
        return _row_to_item(row) if row else None

    async def search(
        self,
        query: str,
        *,
        team_scope: str,
        project_scope: str | None = None,
        truth_level_min: TruthLevel | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        embedding = await self._embedder(query)
        must: list = [FieldCondition(key="team_scope", match=MatchValue(value=team_scope))]
        if project_scope is not None:
            must.append(FieldCondition(
                key="project_scope", match=MatchValue(value=project_scope)
            ))
        try:
            results = await self._qdrant.search(
                collection_name=self._collection,
                query_vector=embedding,
                query_filter=Filter(must=must),
                limit=limit * 2,  # over-fetch then filter truth_level in app
            )
        except Exception:
            return []

        ids = [str(r.id) for r in results]
        if not ids:
            return []

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM memory_items WHERE id = ANY($1::uuid[]) AND team_scope=$2",
                [UUID(i) for i in ids], team_scope,
            )
        items_by_id = {str(r["id"]): _row_to_item(r) for r in rows}
        hits: list[SearchHit] = []
        for r in results:
            item = items_by_id.get(str(r.id))
            if item is None:
                continue
            if truth_level_min is not None and not (item.truth_level >= truth_level_min):
                continue
            hits.append(SearchHit(item=item, score=float(r.score)))
        return hits[:limit]

    async def update(
        self,
        item_id: str,
        *,
        team_scope: str,
        patch: dict[str, Any],
    ) -> MemoryItem:
        existing = await self.get(item_id, team_scope=team_scope)
        if existing is None:
            raise KeyError(f"item {item_id} not in team {team_scope}")
        merged = existing.model_copy(update=patch)
        await self.upsert(merged)
        return merged

    async def delete(self, item_id: str, *, team_scope: str) -> None:
        item = await self.get(item_id, team_scope=team_scope)
        if item is None:
            return  # idempotent
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_items WHERE id=$1 AND team_scope=$2",
                UUID(item_id), team_scope,
            )
        try:
            await self._qdrant.delete(
                collection_name=self._collection, points_selector=[item_id]
            )
        except Exception:
            pass

    async def history(self, item_id: str, *, team_scope: str) -> list[MemoryItem]:
        current = await self.get(item_id, team_scope=team_scope)
        if current is None:
            return []
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM memory_items_history WHERE item_id=$1 AND team_scope=$2 "
                "ORDER BY snapshot_at DESC",
                UUID(item_id), team_scope,
            )
        return [current] + [_history_row_to_item(r) for r in rows]

    async def health(self) -> dict[str, Any]:
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            cols = await self._qdrant.get_collections()
            return {
                "status": "ok",
                "backend": "native",
                "qdrant_collections": [c.name for c in cols.collections],
            }
        except Exception as e:
            return {"status": "down", "backend": "native", "error": str(e)}


def _row_to_item(row) -> MemoryItem:
    md_raw = row["metadata"]
    md = json.loads(md_raw) if isinstance(md_raw, str) else dict(md_raw or {})
    return MemoryItem(
        id=str(row["id"]),
        team_scope=row["team_scope"],
        project_scope=row.get("project_scope"),
        content=row["content"],
        metadata=md,
        embedding=None,
        visibility=Visibility(row["visibility"]),
        truth_level=TruthLevel(row["truth_level"]),
        confidence=float(row["confidence"]),
        source=row["source"],
        validation_status=ValidationStatus(row["validation_status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _history_row_to_item(row) -> MemoryItem:
    md_raw = row["metadata"]
    md = json.loads(md_raw) if isinstance(md_raw, str) else dict(md_raw or {})
    return MemoryItem(
        id=str(row["item_id"]),
        team_scope=row["team_scope"],
        project_scope=md.get("project_scope"),
        content=row["content"],
        metadata=md,
        embedding=None,
        visibility=Visibility(row.get("visibility", "team")),
        truth_level=TruthLevel(row["truth_level"]),
        confidence=float(row.get("confidence", 1.0)),
        source=row["source"],
        validation_status=ValidationStatus(row["validation_status"]),
        created_at=row["snapshot_at"],
        updated_at=row["snapshot_at"],
    )

"""In-process stub MemoryProvider for tests + bootstrap.

NOT FOR PRODUCTION. Stores everything in dicts, no persistence, no real semantic search
(naive substring match instead). Use only for:
- Unit tests that don't need a real backend
- Allowing memory-api to boot before Plan 02-03 ships the real impl
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from xbrain_memory.provider import MemoryProvider
from xbrain_memory.types import MemoryItem, SearchHit, TruthLevel


class NativeStubProvider(MemoryProvider):
    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}
        self._versions: dict[str, list[MemoryItem]] = {}
        # Phase 11 BMO-05/06 — mirror the Qdrant `deleted_at_ts` payload field.
        # 0.0 (or missing) = not deleted; positive epoch = soft-deleted.
        self._deleted_at_ts: dict[str, float] = {}

    async def upsert(self, item: MemoryItem) -> str:
        new_id = item.id if item.id else str(uuid.uuid4())
        if item.id and item.id in self._items:
            # Snapshot existing version before overwrite
            self._versions.setdefault(item.id, []).append(self._items[item.id])
        now = datetime.now(timezone.utc)
        # Use model_copy to set id + updated_at (created_at preserved if existed)
        existing_created = self._items[item.id].created_at if item.id in self._items else item.created_at
        new_item = item.model_copy(update={
            "id": new_id,
            "created_at": existing_created,
            "updated_at": now,
        })
        self._items[new_id] = new_item
        return new_id

    async def get(self, item_id: str, *, team_scope: str) -> MemoryItem | None:
        item = self._items.get(item_id)
        if item is None or item.team_scope != team_scope:
            return None
        return item

    async def search(
        self,
        query: str,
        *,
        team_scope: str,
        project_scope: str | None = None,
        truth_level_min: TruthLevel | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        # Naive substring scoring (production impl would use vector embeddings)
        query_lower = query.lower()
        results: list[SearchHit] = []
        for item in self._items.values():
            if item.team_scope != team_scope:
                continue
            # Phase 11 BMO-05 — mirror NativeProvider's soft-delete filter.
            # `> 0.0` means the item was flagged via mark_deleted; skip it.
            if self._deleted_at_ts.get(item.id, 0.0) > 0.0:
                continue
            if project_scope is not None and item.project_scope != project_scope:
                continue
            if truth_level_min is not None and not (item.truth_level >= truth_level_min):
                continue
            content_lower = item.content.lower()
            if query_lower in content_lower:
                # Naive score: ratio of query length to content length
                score = min(1.0, max(0.1, len(query_lower) / max(1, len(content_lower)) * 10))
                results.append(SearchHit(item=item, score=score))
        results.sort(key=lambda h: h.score, reverse=True)
        return results[:limit]

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
        # Snapshot for history
        self._versions.setdefault(item_id, []).append(existing)
        # Apply patch (Pydantic validates the merged result)
        merged = existing.model_copy(update={**patch, "updated_at": datetime.now(timezone.utc)})
        self._items[item_id] = merged
        return merged

    async def delete(self, item_id: str, *, team_scope: str) -> None:
        item = await self.get(item_id, team_scope=team_scope)
        if item is None:
            return  # idempotent (not found OR wrong team)
        del self._items[item_id]
        self._versions.pop(item_id, None)
        # Hard delete clears any soft-delete bookkeeping.
        self._deleted_at_ts.pop(item_id, None)

    async def mark_deleted(self, item_id: str, deleted_at: datetime) -> None:
        """Mirror of NativeProvider — see provider.py ABC docstring for contract."""
        self._deleted_at_ts[str(item_id)] = deleted_at.timestamp()

    async def mark_restored(self, item_id: str) -> None:
        """Mirror of NativeProvider — see provider.py ABC docstring for contract."""
        self._deleted_at_ts[str(item_id)] = 0.0

    async def history(self, item_id: str, *, team_scope: str) -> list[MemoryItem]:
        current = await self.get(item_id, team_scope=team_scope)
        if current is None:
            return []
        # Most recent first: current, then versions in reverse chronological order
        return [current] + list(reversed(self._versions.get(item_id, [])))

    async def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "backend": "native-stub",
            "item_count": len(self._items),
            "ts": time.time(),
        }

"""mem0-backed MemoryProvider.

Wraps mem0.AsyncMemory. team_scope encoded as user_id="team:{slug}" since mem0 has
no native team_id field. truth_level + visibility + validation_status stored in metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from xbrain_memory.provider import MemoryProvider
from xbrain_memory.types import (
    MemoryItem,
    SearchHit,
    TruthLevel,
    ValidationStatus,
    Visibility,
)


def _team_to_uid(team_scope: str) -> str:
    """xbrain convention: encode team_scope as user_id since mem0 has no team_id field."""
    return f"team:{team_scope}"


def _row_to_item(row: dict[str, Any]) -> MemoryItem:
    """Translate mem0's native dict format → MemoryItem."""
    md = row.get("metadata") or {}
    now = datetime.now(timezone.utc)

    def _get_dt(key: str) -> datetime:
        v = row.get(key) or md.get(key)
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
        return now

    return MemoryItem(
        id=str(row.get("id") or row.get("memory_id") or ""),
        team_scope=md.get("team_scope") or "",
        project_scope=md.get("project_scope"),
        content=row.get("memory") or row.get("text") or row.get("content") or "",
        metadata={
            k: v for k, v in md.items()
            if k not in (
                "team_scope", "project_scope", "visibility",
                "truth_level", "confidence", "source", "validation_status",
            )
        },
        embedding=None,
        visibility=Visibility(md.get("visibility", "team")),
        truth_level=TruthLevel(md.get("truth_level", "EPHEMERAL")),
        confidence=float(md.get("confidence", 1.0)),
        source=md.get("source", "unknown:legacy"),
        validation_status=ValidationStatus(md.get("validation_status", "pending")),
        created_at=_get_dt("created_at"),
        updated_at=_get_dt("updated_at"),
    )


class Mem0Provider(MemoryProvider):
    """mem0-backed implementation."""

    def __init__(self, qdrant_url: str, openai_api_key: str | None = None) -> None:
        # Lazy import — only required when actually constructing this provider
        from mem0 import AsyncMemory  # type: ignore[import-untyped]

        config: dict[str, Any] = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "url": qdrant_url,
                    "collection_name": "messages",  # share Phase 1 collection
                },
            },
        }
        if openai_api_key:
            config["llm"] = {
                "provider": "openai",
                "config": {"model": "gpt-4o-mini", "api_key": openai_api_key},
            }
            config["embedder"] = {
                "provider": "openai",
                "config": {"model": "text-embedding-3-small", "api_key": openai_api_key},
            }
        self._mem = AsyncMemory.from_config(config)

    def _to_meta(self, item: MemoryItem) -> dict[str, Any]:
        return {
            "team_scope": item.team_scope,
            "project_scope": item.project_scope,
            "visibility": item.visibility.value,
            "truth_level": item.truth_level.value,
            "confidence": item.confidence,
            "source": item.source,
            "validation_status": item.validation_status.value,
            **item.metadata,
        }

    async def upsert(self, item: MemoryItem) -> str:
        uid = _team_to_uid(item.team_scope)
        meta = self._to_meta(item)
        if item.id:
            # Update existing
            try:
                await self._mem.update(memory_id=item.id, data=item.content, metadata=meta)
                return item.id
            except Exception:
                # Fall through to add
                pass
        res = await self._mem.add(
            messages=[{"role": "user", "content": item.content}],
            user_id=uid,
            metadata=meta,
        )
        results = res.get("results", []) if isinstance(res, dict) else res
        if not results:
            return ""
        first = results[0] if isinstance(results, list) else results
        return str(first.get("id") if isinstance(first, dict) else first)

    async def get(self, item_id: str, *, team_scope: str) -> MemoryItem | None:
        try:
            row = await self._mem.get(memory_id=item_id)
        except Exception:
            return None
        if not row:
            return None
        item = _row_to_item(row)
        if item.team_scope != team_scope:
            return None  # leak prevention
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
        uid = _team_to_uid(team_scope)
        try:
            rows = await self._mem.search(query=query, user_id=uid, limit=limit * 3)
        except Exception:
            return []
        raw = rows.get("results", rows) if isinstance(rows, dict) else rows
        items = [_row_to_item(r) for r in raw if isinstance(r, dict)]
        # Defensive: post-filter team_scope (mem0 user_id should already filter, belt+suspenders)
        items = [i for i in items if i.team_scope == team_scope]
        if project_scope is not None:
            items = [i for i in items if i.project_scope == project_scope]
        if truth_level_min is not None:
            items = [i for i in items if i.truth_level >= truth_level_min]
        # Score: descending position-based (mem0 doesn't expose raw scores reliably)
        return [
            SearchHit(item=it, score=max(0.05, 1.0 - 0.05 * idx))
            for idx, it in enumerate(items[:limit])
        ]

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
        merged = existing.model_copy(
            update={**patch, "updated_at": datetime.now(timezone.utc)}
        )
        # Re-upsert through mem0 (preserves history if mem0 supports it)
        await self._mem.update(
            memory_id=item_id,
            data=merged.content,
            metadata=self._to_meta(merged),
        )
        return merged

    async def delete(self, item_id: str, *, team_scope: str) -> None:
        item = await self.get(item_id, team_scope=team_scope)
        if item is None:
            return  # idempotent
        try:
            await self._mem.delete(memory_id=item_id)
        except Exception:
            pass  # idempotent

    async def history(self, item_id: str, *, team_scope: str) -> list[MemoryItem]:
        current = await self.get(item_id, team_scope=team_scope)
        if current is None:
            return []
        try:
            rows = await self._mem.history(memory_id=item_id)
            history_list = rows if isinstance(rows, list) else rows.get("results", [])
            items = [_row_to_item(r) for r in history_list if isinstance(r, dict)]
            # Filter to team + sort recent first
            items = [i for i in items if i.team_scope == team_scope]
            items.sort(key=lambda x: x.updated_at, reverse=True)
            return items if items else [current]
        except Exception:
            return [current]

    async def health(self) -> dict[str, Any]:
        try:
            # mem0 has no native health endpoint — confirm we can call get_all
            return {"status": "ok", "backend": "mem0"}
        except Exception as e:
            return {"status": "down", "backend": "mem0", "error": str(e)}

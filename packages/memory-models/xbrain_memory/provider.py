"""MemoryProvider abstract base class — backend-agnostic interface."""

from abc import ABC, abstractmethod
from typing import Any

from xbrain_memory.types import MemoryItem, SearchHit, TruthLevel


class MemoryProvider(ABC):
    """Abstract memory backend.

    All implementations MUST enforce team_scope isolation: a search/get with team_scope=A
    MUST NOT return items belonging to team_scope=B, even if the underlying backend
    supports cross-tenant queries.
    """

    @abstractmethod
    async def upsert(self, item: MemoryItem) -> str:
        """Insert or update a memory item. Returns the backend-assigned ID."""

    @abstractmethod
    async def get(self, item_id: str, *, team_scope: str) -> MemoryItem | None:
        """Fetch by ID, scoped to team. Returns None if not found OR not in team."""

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        team_scope: str,
        project_scope: str | None = None,
        truth_level_min: TruthLevel | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        """Semantic search. team_scope is REQUIRED and FILTER-AT-RETRIEVAL.

        truth_level_min: if set, only return items with truth_level >= this value.
        """

    @abstractmethod
    async def update(
        self,
        item_id: str,
        *,
        team_scope: str,
        patch: dict[str, Any],
    ) -> MemoryItem:
        """Partial update. patch keys must be a subset of MemoryItem fields.

        Raises KeyError if item not found or wrong team.
        """

    @abstractmethod
    async def delete(self, item_id: str, *, team_scope: str) -> None:
        """Hard delete. Idempotent (no error if already absent or wrong team)."""

    @abstractmethod
    async def history(self, item_id: str, *, team_scope: str) -> list[MemoryItem]:
        """Versions historiques d'un item. Most recent first.

        Backends that don't support versioning return [current_item] only.
        """

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Backend health check.

        Returns dict with at minimum {"status": "ok|degraded|down", "backend": "<name>"}.
        """

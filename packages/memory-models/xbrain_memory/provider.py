"""MemoryProvider abstract base class — backend-agnostic interface."""

from abc import ABC, abstractmethod
from datetime import datetime
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
    async def mark_deleted(self, item_id: str, deleted_at: datetime) -> None:
        """Flip the vector-store soft-delete marker so this item is excluded from search.

        Phase 11 contract (BMO-05):
          The retrieval-side filter built into `search()` excludes any item flagged
          here. Implementations that have no vector store (e.g. mem0 metadata-only)
          should still record the marker so their `search()` post-filter honours it.

        Authorization is the CALL SITE's responsibility — this method does not
        check `team_scope`. Plan 11-05's PATCH/DELETE handler authorizes the
        request via `assert_can_edit_brain_event` BEFORE calling this method.

        Idempotent: marking an already-deleted item just overwrites the timestamp.
        """

    @abstractmethod
    async def mark_restored(self, item_id: str) -> None:
        """Clear the vector-store soft-delete marker so this item reappears in search.

        Phase 11 contract (BMO-06):
          Inverse of `mark_deleted`. Idempotent: restoring a non-deleted item
          is a no-op. Authorization is the call site's responsibility (see
          `mark_deleted` docstring).
        """

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

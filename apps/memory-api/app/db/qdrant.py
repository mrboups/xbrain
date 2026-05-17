"""Async Qdrant client init — singleton via lru_cache.

Phase 11 plan 11-10: used by the superadmin storage metric endpoint to
count points per team. Mirrors the construction pattern used by
qdrant_setup.py:21 and routes/health.py:35 — a single source of truth
for AsyncQdrantClient instantiation across the codebase.

Returns None on construction failure (qdrant_client lib missing, malformed
URL, etc.) so the caller can fail-soft to qdrant_points=None for the
dashboard column.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_qdrant_client() -> Any | None:
    """Return a shared AsyncQdrantClient or None if construction fails."""
    try:
        from qdrant_client import AsyncQdrantClient
        return AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
        )
    except Exception as exc:
        logger.warning("qdrant_client_init_failed error=%s", exc)
        return None

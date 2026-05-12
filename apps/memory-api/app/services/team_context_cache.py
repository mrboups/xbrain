"""In-process TTL cache for the team memory context bundle.

Quick task 260512-tcr decision #13: the bundle handed to Anthropic must be
**prefix-stable across mentions in a 5-minute window** so the Anthropic
prompt cache (`cache_control: ephemeral`) yields ~90% input cost reduction
on follow-up questions.

Strategy:
  - Query top N items WHERE team_scope=<slug> AND truth_level IN
    ('WORKING','VALIDATED','CANONICAL'), ordered by created_at DESC.
  - Format as a deterministic markdown block (no timestamps in the BODY
    so re-runs within the cache window produce IDENTICAL bytes → cache hit).
  - Cache per team_id with TTL = settings.TEAM_CONTEXT_CACHE_TTL_S (300s).
  - On cache hit within TTL: return the same string instance — bytes are
    guaranteed identical.

Phase 2 plans to invalidate explicitly when a memory item is promoted to
VALIDATED+. For v1 the 5-minute TTL is acceptable staleness.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


class _CacheEntry:
    __slots__ = ("expires_at", "bundle", "item_count")

    def __init__(self, expires_at: float, bundle: str, item_count: int):
        self.expires_at = expires_at
        self.bundle = bundle
        self.item_count = item_count


# team_id (str) -> _CacheEntry
_cache: dict[str, _CacheEntry] = {}


def _now() -> float:
    """Indirection so tests can monkeypatch time."""
    return time.monotonic()


def _format_item(content: str, truth_level: str, source: str) -> str:
    """Format one memory item line. Deterministic — no timestamps in body.

    The relative timestamp would change every second and bust the cache.
    For Phase 2 we can include a coarse bucket (e.g. "this week") if it
    improves answers.
    """
    # Truncate to 800 chars per item to bound the total payload.
    snippet = content.strip()
    if len(snippet) > 800:
        snippet = snippet[:800].rstrip() + "…"
    return f"- [{truth_level}] ({source}) {snippet}"


async def get_team_memory_bundle(
    session: AsyncSession,
    team_scope: str,
    team_id: UUID | str,
) -> dict[str, Any]:
    """Return {"bundle": str, "item_count": int, "cached": bool, "cache_age_s": float}.

    The bundle is a deterministic markdown list of memory items, suitable
    for direct inclusion in an Anthropic system block with cache_control:
    ephemeral.
    """
    key = str(team_id)
    now = _now()
    entry = _cache.get(key)
    if entry is not None and entry.expires_at > now:
        return {
            "bundle": entry.bundle,
            "item_count": entry.item_count,
            "cached": True,
            "cache_age_s": settings.TEAM_CONTEXT_CACHE_TTL_S - (entry.expires_at - now),
        }

    # Cache miss — rebuild.
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT content, truth_level, source
                FROM memory_items
                WHERE team_scope = :team_scope
                  AND truth_level IN ('WORKING', 'VALIDATED', 'CANONICAL')
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"team_scope": team_scope, "limit": settings.TEAM_CONTEXT_MAX_ITEMS},
        )
    ).mappings().all()

    if not rows:
        bundle = "(no team memory items yet)"
    else:
        lines = [
            _format_item(r["content"], r["truth_level"], r["source"] or "unknown")
            for r in rows
        ]
        bundle = "\n".join(lines)

    _cache[key] = _CacheEntry(
        expires_at=now + settings.TEAM_CONTEXT_CACHE_TTL_S,
        bundle=bundle,
        item_count=len(rows),
    )
    return {
        "bundle": bundle,
        "item_count": len(rows),
        "cached": False,
        "cache_age_s": 0.0,
    }


def invalidate(team_id: UUID | str) -> None:
    """Drop the cached entry for a team. Called by Phase 2 truth-promotion
    workflow to force a fresh bundle when an item moves to VALIDATED+.
    """
    _cache.pop(str(team_id), None)


def _reset_for_tests() -> None:
    """Test helper — clear the module-level cache between cases."""
    _cache.clear()

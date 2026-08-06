"""In-process TTL cache for the team memory context bundle.

Quick task 260512-tcr decision #13: the bundle handed to Anthropic must be
**prefix-stable across mentions in a 5-minute window** so the Anthropic
prompt cache (`cache_control: ephemeral`) yields ~90% input cost reduction
on follow-up questions.

Strategy:
  - Query top N items WHERE team_scope=<slug> AND truth_level IN
    ('WORKING','VALIDATED','CANONICAL'), ordered by WEIGHT and then by
    created_at DESC — starred (a person's pick) first, then the assistant's
    important/final flag, then ordinary working notes. See _LEVEL_RANK_SQL.
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
from app.services.user_label import label_from_parts


class _CacheEntry:
    __slots__ = ("expires_at", "bundle", "item_count")

    def __init__(self, expires_at: float, bundle: str, item_count: int):
        self.expires_at = expires_at
        self.bundle = bundle
        self.item_count = item_count


# team_id (str) -> _CacheEntry
_cache: dict[str, _CacheEntry] = {}


def invalidate(team_id) -> None:
    """Drop the cached bundle for a team so the next read rebuilds from the DB.

    Called after a new memory item is ingested for the team (e.g. brain_ingest)
    so freshly-ingested chat facts surface in the @claude bundle immediately
    instead of after the 5-min TTL.
    """
    _cache.pop(str(team_id), None)


def _now() -> float:
    """Indirection so tests can monkeypatch time."""
    return time.monotonic()


# Per-item char cap — generous so long VALIDATED/CANONICAL items (e.g. a full
# deck/document summary) reach the agent instead of being cut after a few
# hundred chars. The TOTAL bundle is bounded separately (_MAX_BUNDLE_CHARS) so a
# team with many long items can never blow the model input budget.
_PER_ITEM_CHARS = 5000
# Total bundle cap (~15k tokens). Items are newest-first, so the most recent /
# relevant items are kept; older ones are dropped once the budget is reached.
_MAX_BUNDLE_CHARS = 60000


# How the levels sort when the bundle is built. Lower wins.
#
# THIS IS THE WEIGHTING (2026-08-05). A starred item is one a PERSON on the team
# picked out by hand; an important/final item is the AI's own opinion; everything
# else is working knowledge. Ordering by that before recency does two things,
# and the second matters more than the first:
#
#   * the model reads the team's own picks at the top of the block, where
#     attention is cheapest, instead of wherever they happen to fall by date;
#   * when a talkative team overflows `_MAX_BUNDLE_CHARS`, what gets dropped is
#     the ORDINARY tail. Before this, a starred item from last week was evicted
#     by a hundred lines of today's chatter — the one case where losing it is
#     least forgivable, because somebody deliberately marked it.
#
# Recency still decides within a level, so the block stays "newest first" for
# everything a person or the AI has not singled out.
_LEVEL_RANK_SQL = """
        CASE truth_level
            WHEN 'CANONICAL' THEN 0
            WHEN 'VALIDATED' THEN 1
            ELSE 2
        END
"""

#: What each level is called in the block. The stored names are the enum's; these
#: are what the four-level design calls them, and the difference is load-bearing
#: — "CANONICAL" reads to a model as "canonical truth" when what it actually
#: means is "a teammate starred this", and "VALIDATED" claims a verification that
#: never happened. The AI's own flag must not come back to it dressed as a
#: warrant somebody granted.
_LEVEL_LABELS = {
    "CANONICAL": "starred by a teammate",
    "VALIDATED": "flagged important by the assistant",
    "WORKING": "working",
}

#: One line above the items saying what the labels mean, so the ordering is not
#: the only thing carrying the ranking. Included only when there is something to
#: label; deterministic, so it cannot bust the prompt cache.
_LEGEND = (
    "(Ordered by weight: items a teammate STARRED come first — a person chose "
    "those by hand and they outrank everything below. Then items the assistant "
    "flagged important, which are its own opinion, not a human's. Then ordinary "
    "working notes, newest first. The parenthetical on each line says WHERE THE "
    "FACT CAME FROM: \"added by <name>\" is the person who put it in the brain, "
    "\"from <connector>\" is an integration with no human author. When you use "
    "one of these facts in an answer, say who or what it came from — and never "
    "name a person for a \"from <connector>\" line.)"
)


def _attribution(row) -> str:
    """Who — or what — put this in the brain.

    A person when one is known, otherwise the connector that carried it. The
    distinction is not cosmetic: a Drive file has no author in this table, and a
    line that reads "from Alice" when Alice merely owns the folder invents a
    claim the model will repeat as fact.
    """
    name = label_from_parts(
        preferred_name=row.get("preferred_name"),
        display_name=row.get("display_name"),
        email=row.get("email"),
        source_user_id=row.get("author_source_user_id"),
    )
    if row.get("author_source_user_id"):
        return f"added by {name}"
    return f"from {row.get('source') or 'unknown'}"


def _format_item(content: str, truth_level: str, attribution: str) -> str:
    """Format one memory item line. Deterministic — no timestamps in body.

    The relative timestamp would change every second and bust the cache.
    For Phase 2 we can include a coarse bucket (e.g. "this week") if it
    improves answers.
    """
    snippet = content.strip()
    if len(snippet) > _PER_ITEM_CHARS:
        snippet = snippet[:_PER_ITEM_CHARS].rstrip() + "…"
    label = _LEVEL_LABELS.get(truth_level, truth_level)
    return f"- [{label}] ({attribution}) {snippet}"


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
    # Phase 11 (BMO-07) — soft-deleted memory_items must NOT leak into the
    # team-context bundle the LLM ingests. Without this filter, an item the
    # author deleted via the brain monitor would still surface in every
    # subsequent agent prompt for up to 30 days (janitor window).
    rows = (
        await session.execute(
            sa.text(
                f"""
                SELECT mi.content,
                       mi.truth_level,
                       mi.source,
                       u.preferred_name        AS preferred_name,
                       u.display_name          AS display_name,
                       u.email                 AS email,
                       u.source_user_id        AS author_source_user_id
                FROM memory_items mi
                -- LEFT so an item whose author has no user row (or no author at
                -- all, like a Drive file) still appears — attributed to its
                -- connector rather than dropped.
                LEFT JOIN users u
                       ON u.source_user_id = mi.metadata->>'author_sub'
                WHERE mi.team_scope = :team_scope
                  AND mi.truth_level IN ('WORKING', 'VALIDATED', 'CANONICAL')
                  AND mi.deleted_at IS NULL
                ORDER BY {_LEVEL_RANK_SQL}, mi.created_at DESC
                LIMIT :limit
                """  # noqa: S608 — _LEVEL_RANK_SQL is a module constant, not input
            ),
            {"team_scope": team_scope, "limit": settings.TEAM_CONTEXT_MAX_ITEMS},
        )
    ).mappings().all()

    if not rows:
        bundle = "(no team memory items yet)"
    else:
        lines: list[str] = [_LEGEND]
        total = len(_LEGEND) + 1
        for r in rows:
            line = _format_item(r["content"], r["truth_level"], _attribution(r))
            # Budget reached — stop. Because the rows arrive starred-first, what
            # this drops is the ordinary tail, never somebody's starred item.
            if len(lines) > 1 and total + len(line) > _MAX_BUNDLE_CHARS:
                break
            lines.append(line)
            total += len(line) + 1
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

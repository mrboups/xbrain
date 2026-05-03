"""RAG enrichment — build a system_addendum from a team's CANONICAL facts.

Used by /v1/system-prompt. Designed to be cheap and bounded:
- Fixed Top-K (default 5)
- Per-fact char cap (Twitter-length) so the prompt prefix stays small
- Filtered by truth_level >= CANONICAL by default — only vetted team knowledge

Phase 3 will refine selection (graph-based, recency-weighted, opt-in/out).
"""

from __future__ import annotations

from xbrain_memory import MemoryProvider, SearchHit, TruthLevel

DEFAULT_TOP_K = 5
MAX_FACT_CHARS = 280  # Twitter-length cap per fact, keeps prompt prefix small


def _format_addendum(hits: list[SearchHit], min_level: TruthLevel) -> str:
    """Render hits as a markdown system-prompt addendum. Empty string when no hits."""
    if not hits:
        return ""
    lines = [
        f"## Team facts ({min_level.value}+ truth level)",
        "",
        "Use these as ground truth in your response. "
        "Cite the fact ID if you reference one.",
        "",
    ]
    for h in hits:
        content = h.item.content[:MAX_FACT_CHARS]
        if len(h.item.content) > MAX_FACT_CHARS:
            content += "…"
        # First 8 chars of UUID + 2-decimal confidence for compact citation
        lines.append(
            f"- ({h.item.id[:8]}, conf={h.item.confidence:.2f}): {content}"
        )
    return "\n".join(lines)


async def build_system_addendum(
    provider: MemoryProvider,
    *,
    query: str,
    team_scope: str,
    project_scope: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_level: TruthLevel = TruthLevel.CANONICAL,
) -> str:
    """Returns markdown addendum (or empty string if no facts found).

    Selected facts must satisfy:
      - team_scope == team_scope (mandatory — enforced by provider.search)
      - project_scope == project_scope (if specified)
      - truth_level >= min_level (default CANONICAL)
    """
    hits = await provider.search(
        query,
        team_scope=team_scope,
        project_scope=project_scope,
        truth_level_min=min_level,
        limit=top_k,
    )
    return _format_addendum(hits, min_level)


def count_facts(addendum: str) -> int:
    """Cheap fact counter — counts the per-fact bullet prefix `- (` lines."""
    return sum(1 for line in addendum.splitlines() if line.startswith("- ("))

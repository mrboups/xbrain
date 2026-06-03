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
MAX_MEDIA_CONTENT_CHARS = 120  # shorter cap for media bullets (URL takes space)

# Instruction injected once when at least one media item appears in the addendum.
_MEDIA_INSTRUCTION = (
    "When an item below is an image or file and is relevant to the user's request, "
    "include its markdown (the ![...](...) or [...](...) part) in your reply so the user can see it."
)


def _human_size(n: int) -> str:
    """Return a compact human-readable byte size string (e.g. '1.2 MB', '340 KB')."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "B":
                return f"{n} B"
            return f"{n / 1024:.1f} {unit}"
        n //= 1024
    return f"{n} GB"  # unreachable but satisfies type checker


def _format_addendum(hits: list[SearchHit], min_level: TruthLevel) -> str:
    """Render hits as a markdown system-prompt addendum. Empty string when no hits."""
    if not hits:
        return ""

    # Check whether any hit carries a media sub-dict so we can prepend the
    # instruction only once rather than repeating it per item.
    has_media = any(
        isinstance((h.item.metadata or {}).get("media"), dict)
        for h in hits
    )

    lines = [
        f"## Team facts ({min_level.value}+ truth level)",
        "",
        "Use these as ground truth in your response. "
        "Cite the fact ID if you reference one.",
    ]
    if has_media:
        lines.append(_MEDIA_INSTRUCTION)
    lines.append("")

    for h in hits:
        media = (h.item.metadata or {}).get("media") if h.item.metadata else None
        short_id = h.item.id[:8]
        conf = h.item.confidence

        if isinstance(media, dict) and media.get("mime"):
            # Media item: emit markdown image or link so the model can echo it.
            item_id = h.item.id
            filename = media.get("filename") or item_id
            mime: str = media.get("mime", "")
            url = f"/api/xbrain/media/{item_id}"
            content = h.item.content[:MAX_MEDIA_CONTENT_CHARS]

            if mime.startswith("image/"):
                media_md = f"![{filename}]({url})"
            else:
                size_str = _human_size(media.get("size", 0)) if media.get("size") else ""
                label = f"{filename} ({size_str})" if size_str else filename
                media_md = f"[{label}]({url})"

            lines.append(f"- {media_md} — ({short_id}, conf={conf:.2f}): {content}")
        else:
            # Plain fact: existing format unchanged.
            content = h.item.content[:MAX_FACT_CHARS]
            if len(h.item.content) > MAX_FACT_CHARS:
                content += "…"
            # First 8 chars of UUID + 2-decimal confidence for compact citation
            lines.append(
                f"- ({short_id}, conf={conf:.2f}): {content}"
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

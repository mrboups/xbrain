"""RAG /v1/system-prompt tests — team isolation + truth_level filter + Top-K.

Uses NativeStubProvider in-process. Team isolation is enforced by the provider
contract; we double-check here that the build_system_addendum call surface
respects it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from xbrain_memory import MemoryItem, TruthLevel, ValidationStatus, Visibility
from xbrain_memory.providers.native_stub import NativeStubProvider

from app.services.rag_enrichment import (
    DEFAULT_TOP_K,
    MAX_FACT_CHARS,
    _format_addendum,
    _human_size,
    build_system_addendum,
    count_facts,
)

pytestmark = pytest.mark.asyncio


def _item(
    *,
    team: str,
    content: str,
    level: TruthLevel = TruthLevel.CANONICAL,
    confidence: float = 0.95,
    project: str | None = None,
) -> MemoryItem:
    now = datetime.now(timezone.utc)
    return MemoryItem(
        id=str(uuid.uuid4()),
        team_scope=team,
        project_scope=project,
        content=content,
        metadata={},
        embedding=None,
        visibility=Visibility.TEAM,
        truth_level=level,
        confidence=confidence,
        source="test",
        validation_status=ValidationStatus.VALIDATED,
        created_at=now,
        updated_at=now,
    )


# === Tests ===


async def test_system_prompt_returns_canonical_facts_for_team():
    provider = NativeStubProvider()
    await provider.upsert(_item(team="team-a", content="The auth module uses Google OAuth"))
    await provider.upsert(_item(team="team-a", content="Production DB is Postgres 17"))

    addendum = await build_system_addendum(
        provider, query="auth", team_scope="team-a"
    )

    assert "## Team facts (CANONICAL+ truth level)" in addendum
    assert "Google OAuth" in addendum
    assert count_facts(addendum) == 1  # only "auth" matches via stub substring search


async def test_system_prompt_excludes_other_teams_canonical_facts():
    """Cardinal team-isolation invariant: team-b facts MUST NOT appear in team-a results."""
    provider = NativeStubProvider()
    await provider.upsert(_item(team="team-a", content="Team A roadmap is Phase 2"))
    await provider.upsert(_item(team="team-b", content="Team B roadmap is sealed"))

    addendum = await build_system_addendum(
        provider, query="roadmap", team_scope="team-a"
    )

    assert "Team A roadmap" in addendum
    assert "Team B" not in addendum
    assert "sealed" not in addendum


async def test_system_prompt_excludes_below_min_level():
    """When min_level=CANONICAL, WORKING facts must NOT leak into the addendum."""
    provider = NativeStubProvider()
    await provider.upsert(_item(team="t", content="canonical fact xyz", level=TruthLevel.CANONICAL))
    await provider.upsert(_item(team="t", content="draft fact xyz", level=TruthLevel.WORKING))
    await provider.upsert(_item(team="t", content="raw fact xyz", level=TruthLevel.EPHEMERAL))

    addendum = await build_system_addendum(
        provider, query="xyz", team_scope="t", min_level=TruthLevel.CANONICAL
    )

    assert "canonical fact xyz" in addendum
    assert "draft fact xyz" not in addendum
    assert "raw fact xyz" not in addendum


async def test_system_prompt_top_k_limit_respected():
    provider = NativeStubProvider()
    for i in range(10):
        await provider.upsert(_item(team="t", content=f"alpha fact number {i}"))

    addendum = await build_system_addendum(
        provider, query="alpha", team_scope="t", top_k=3
    )

    assert count_facts(addendum) == 3


async def test_system_prompt_empty_when_no_facts():
    """No CANONICAL facts → empty string, no header, no pollution."""
    provider = NativeStubProvider()
    addendum = await build_system_addendum(
        provider, query="anything", team_scope="empty-team"
    )

    assert addendum == ""
    assert count_facts(addendum) == 0


async def test_system_prompt_truncates_long_fact_to_max_chars():
    provider = NativeStubProvider()
    long_text = "alpha " + "x" * 500
    await provider.upsert(_item(team="t", content=long_text))

    addendum = await build_system_addendum(provider, query="alpha", team_scope="t")

    # The fact bullet should contain the ellipsis truncation marker
    assert "…" in addendum
    # Total addendum length should be bounded close to MAX_FACT_CHARS + header overhead
    # (header ~120 chars + bullet prefix ~30 chars = ~150 chars overhead)
    assert len(addendum) <= MAX_FACT_CHARS + 200


async def test_system_prompt_default_topk_is_5():
    """Lock the documented default — changing it requires updating the contract."""
    assert DEFAULT_TOP_K == 5


async def test_system_prompt_project_scope_filter():
    """Filter on project_scope when supplied — only facts of matching project surface."""
    provider = NativeStubProvider()
    await provider.upsert(_item(team="t", content="alpha for proj-x", project="proj-x"))
    await provider.upsert(_item(team="t", content="alpha for proj-y", project="proj-y"))

    addendum = await build_system_addendum(
        provider, query="alpha", team_scope="t", project_scope="proj-x"
    )

    assert "proj-x" in addendum
    assert "proj-y" not in addendum


# ---------------------------------------------------------------------------
# BL-003 slice 5: media markdown in addendum
# ---------------------------------------------------------------------------


def _media_item(
    *,
    team: str,
    content: str,
    item_id: str | None = None,
    mime: str = "image/png",
    filename: str = "photo.png",
    size: int = 1024,
    level: TruthLevel = TruthLevel.CANONICAL,
    confidence: float = 0.9,
) -> MemoryItem:
    now = datetime.now(timezone.utc)
    return MemoryItem(
        id=item_id or str(uuid.uuid4()),
        team_scope=team,
        project_scope=None,
        content=content,
        metadata={
            "media": {
                "mime": mime,
                "filename": filename,
                "size": size,
                "key": f"media/{team}/{item_id or 'x'}.png",
            }
        },
        embedding=None,
        visibility=Visibility.TEAM,
        truth_level=level,
        confidence=confidence,
        source="test",
        validation_status=ValidationStatus.VALIDATED,
        created_at=now,
        updated_at=now,
    )


def test_format_addendum_image_hit_emits_markdown_image():
    """An image media hit must produce a ![...](...) line with the stable proxy URL."""
    from xbrain_memory import SearchHit

    item_id = "aaaabbbb-0000-0000-0000-000000000001"
    item = _media_item(
        team="t",
        content="team photo",
        item_id=item_id,
        mime="image/png",
        filename="team.png",
    )
    hits = [SearchHit(item=item, score=0.95)]
    addendum = _format_addendum(hits, TruthLevel.CANONICAL)

    assert f"![team.png](/api/xbrain/media/{item_id})" in addendum
    assert "team photo" in addendum
    # Instruction must appear because there is at least one media item.
    assert "include its markdown" in addendum


def test_format_addendum_document_hit_emits_markdown_link():
    """A non-image media hit must produce a [name (size)](url) link."""
    from xbrain_memory import SearchHit

    item_id = "aaaabbbb-0000-0000-0000-000000000002"
    item = _media_item(
        team="t",
        content="Q1 report",
        item_id=item_id,
        mime="application/pdf",
        filename="report.pdf",
        size=204800,  # 200 KB
    )
    hits = [SearchHit(item=item, score=0.88)]
    addendum = _format_addendum(hits, TruthLevel.CANONICAL)

    assert f"[report.pdf (200.0 KB)](/api/xbrain/media/{item_id})" in addendum
    assert "Q1 report" in addendum
    assert "include its markdown" in addendum


def test_format_addendum_mixed_media_and_plain_hits():
    """Mix of media + plain hits: media gets markdown, plain keeps existing format."""
    from xbrain_memory import SearchHit

    item_id = "aaaabbbb-0000-0000-0000-000000000003"
    media_item = _media_item(
        team="t", content="screenshot", item_id=item_id, mime="image/jpeg"
    )
    plain_item = _item(team="t", content="plain canonical fact")

    hits = [
        SearchHit(item=media_item, score=0.9),
        SearchHit(item=plain_item, score=0.8),
    ]
    addendum = _format_addendum(hits, TruthLevel.CANONICAL)

    assert f"![" in addendum
    assert "plain canonical fact" in addendum
    # Instruction appears exactly once (one media item in the set)
    assert addendum.count("include its markdown") == 1


def test_format_addendum_no_media_no_instruction():
    """When no hits have media, the instruction line must NOT appear."""
    from xbrain_memory import SearchHit

    item = _item(team="t", content="plain fact about deployment")
    hits = [SearchHit(item=item, score=0.7)]
    addendum = _format_addendum(hits, TruthLevel.CANONICAL)

    assert "include its markdown" not in addendum
    assert "plain fact about deployment" in addendum


def test_human_size_helper():
    assert _human_size(500) == "500 B"
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1536) == "1.5 KB"
    assert _human_size(1024 * 1024) == "1.0 MB"
    assert _human_size(2 * 1024 * 1024) == "2.0 MB"

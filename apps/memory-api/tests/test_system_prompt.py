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

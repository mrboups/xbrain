"""THE gate for how the agent's memory block weights a starred item.

The star is only worth setting if the agent actually reads it first. Ordering by
level before recency does two things, and the second is the one that would fail
silently:

  1. the team's own picks sit at the top of the block;
  2. when a talkative team overflows the bundle's character budget, what gets
     dropped is the ORDINARY tail — not somebody's starred item from last week.

Both are asserted against a real Postgres, because the ordering IS a SQL clause
and a mocked query cannot get it wrong.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this
file. A clean SKIP is legitimate ONLY when Docker is genuinely absent (conftest
gate); under Docker this file MUST run green.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _insert(session, *, team_scope: str, content: str, level: str, ago_days: int):
    """One live memory item, aged so recency and weight can disagree."""
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
                (team_scope, content, metadata, visibility, truth_level,
                 validation_status, confidence, source, created_at)
            VALUES
                (:ts, :content, '{}'::jsonb, 'team', :level,
                 'pending', 0.7, 'test',
                 NOW() - (CAST(:ago AS int) * INTERVAL '1 day'))
            """
        ),
        {"ts": team_scope, "content": content, "level": level, "ago": ago_days},
    )


async def test_starred_outranks_newer_ordinary_items(session, seeded_two_teams):
    from app.services import team_context_cache as tcc

    tcc._reset_for_tests()
    team_a = seeded_two_teams["team_a"]

    # The OLDEST item is the starred one, and the newest is ordinary — so an
    # ordering that still sorts by date alone puts them the wrong way round.
    await _insert(
        session, team_scope=team_a.slug, content="STARRED_OLD", level="CANONICAL", ago_days=30
    )
    await _insert(
        session, team_scope=team_a.slug, content="AI_FLAGGED_MID", level="VALIDATED", ago_days=15
    )
    await _insert(
        session, team_scope=team_a.slug, content="ORDINARY_NEW", level="WORKING", ago_days=1
    )
    await session.commit()

    result = await tcc.get_team_memory_bundle(session, team_a.slug, team_a.id)
    bundle = result["bundle"]

    pos_star = bundle.index("STARRED_OLD")
    pos_ai = bundle.index("AI_FLAGGED_MID")
    pos_plain = bundle.index("ORDINARY_NEW")
    assert pos_star < pos_ai < pos_plain, (
        "weight must beat recency: a teammate's starred item outranks the "
        f"assistant's flag, which outranks ordinary notes. Got:\n{bundle}"
    )

    # And the block says what the labels mean, so the ordering is not the only
    # thing carrying the ranking.
    assert "starred by a teammate" in bundle
    assert "flagged important by the assistant" in bundle, (
        "the assistant's own flag must not come back to it dressed as a warrant "
        "a person granted"
    )
    assert "[CANONICAL]" not in bundle, (
        "'CANONICAL' reads as canonical truth; what it means is that somebody "
        "starred it"
    )


async def test_the_budget_drops_the_ordinary_tail_not_the_star(session, seeded_two_teams, monkeypatch):
    """Overflow evicts chatter, never somebody's deliberate pick.

    This is the assertion that would have failed silently under date ordering: a
    starred item is by definition older than the flood of messages that pushes
    the bundle over budget, so date ordering evicts exactly the wrong one.
    """
    from app.services import team_context_cache as tcc

    tcc._reset_for_tests()
    team_a = seeded_two_teams["team_a"]

    # A budget that fits only a few lines, so the cut is reached deterministically.
    monkeypatch.setattr(tcc, "_MAX_BUNDLE_CHARS", 600)

    await _insert(
        session, team_scope=team_a.slug, content="STARRED_OLD", level="CANONICAL", ago_days=60
    )
    for i in range(40):
        await _insert(
            session,
            team_scope=team_a.slug,
            content=f"CHATTER_{i:02d} " + ("x" * 80),
            level="WORKING",
            ago_days=1,
        )
    await session.commit()

    bundle = (await tcc.get_team_memory_bundle(session, team_a.slug, team_a.id))["bundle"]

    assert "STARRED_OLD" in bundle, (
        "the one item somebody marked by hand is the last thing that may be "
        "evicted, not the first"
    )
    assert len(bundle) <= tcc._MAX_BUNDLE_CHARS + 200, "the budget must still bind"
    assert "CHATTER_39" not in bundle, (
        "…and the ordinary tail must genuinely be the thing that was cut, or "
        "this test proves nothing about the budget"
    )


async def test_bundle_bytes_stay_stable_within_the_cache_window(session, seeded_two_teams):
    """The ranking must not cost the Anthropic prompt cache.

    The block is handed to Anthropic with `cache_control: ephemeral`, which only
    pays if the bytes are IDENTICAL across calls in the window. A legend and a
    deterministic ORDER BY keep that true; a timestamp or a random tiebreak
    would not.
    """
    from app.services import team_context_cache as tcc

    tcc._reset_for_tests()
    team_a = seeded_two_teams["team_a"]
    await _insert(
        session, team_scope=team_a.slug, content="STARRED_OLD", level="CANONICAL", ago_days=3
    )
    await _insert(
        session, team_scope=team_a.slug, content="ORDINARY_NEW", level="WORKING", ago_days=1
    )
    await session.commit()

    first = await tcc.get_team_memory_bundle(session, team_a.slug, team_a.id)
    assert first["cached"] is False
    second = await tcc.get_team_memory_bundle(session, team_a.slug, team_a.id)
    assert second["cached"] is True
    assert second["bundle"] == first["bundle"]

    # Rebuild from scratch — the bytes must match the cached ones exactly.
    tcc._reset_for_tests()
    rebuilt = await tcc.get_team_memory_bundle(session, team_a.slug, team_a.id)
    assert rebuilt["cached"] is False
    assert rebuilt["bundle"] == first["bundle"], (
        "a rebuild inside the window must produce identical bytes or every "
        "follow-up question pays full input price"
    )

"""Tests for app.services.team_context_cache — TTL cache + deterministic format.

Quick task 260512-tcr Wave 2.6. Uses the existing `session` integration
fixture (Postgres via testcontainers) to insert memory_items and exercise
the real SQL query. The cache invalidation paths are pure-Python — verified
by manipulating module-level state.
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.services import team_context_cache as tcc


@pytest.fixture(autouse=True)
def _clear_cache():
    tcc._reset_for_tests()
    yield
    tcc._reset_for_tests()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_team_returns_placeholder(session, seeded_two_teams):
    """A team with no memory_items renders a clear empty-state placeholder."""
    team_a = seeded_two_teams["team_a"]
    result = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    assert result["item_count"] == 0
    assert result["cached"] is False
    assert "no team memory items" in result["bundle"].lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_only_working_or_above_included(session, seeded_two_teams):
    """EPHEMERAL items are excluded; WORKING/VALIDATED/CANONICAL included."""
    team_a = seeded_two_teams["team_a"]
    slug = team_a.slug
    items = [
        ("content_eph", "EPHEMERAL"),
        ("content_working", "WORKING"),
        ("content_valid", "VALIDATED"),
        ("content_canon", "CANONICAL"),
    ]
    for content, level in items:
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items
                  (team_scope, content, truth_level, validation_status, source, visibility)
                VALUES (:team, :content, :level, 'n/a', 'test', 'team')
                """
            ),
            {"team": slug, "content": content, "level": level},
        )
    await session.commit()

    result = await tcc.get_team_memory_bundle(
        session, team_scope=slug, team_id=team_a.id
    )
    assert result["item_count"] == 3  # EPHEMERAL excluded
    assert "content_eph" not in result["bundle"]
    assert "content_working" in result["bundle"]
    assert "content_valid" in result["bundle"]
    assert "content_canon" in result["bundle"]
    # Truth-level pill format check. The pill is the SERVICE's label, not the
    # stored enum name — `_LEVEL_LABELS` renames them on purpose (a model reads
    # "CANONICAL" as canonical truth when it only means a teammate starred it).
    # Assert through the mapping so a copy revision stays the service's call,
    # and assert separately on the thing that would be a real regression: the
    # raw enum names must never reach the prompt.
    for level in ("WORKING", "VALIDATED", "CANONICAL"):
        assert f"[{tcc._LEVEL_LABELS[level]}]" in result["bundle"]
        assert f"[{level}]" not in result["bundle"]
    assert len(set(tcc._LEVEL_LABELS.values())) == 3, "each level needs its own pill"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_hit_on_second_call(session, seeded_two_teams):
    """Second call within TTL returns cached:True; bundle bytes are identical."""
    team_a = seeded_two_teams["team_a"]
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
              (team_scope, content, truth_level, validation_status, source, visibility)
            VALUES (:team, 'item_x', 'WORKING', 'n/a', 'test', 'team')
            """
        ),
        {"team": team_a.slug},
    )
    await session.commit()

    first = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    assert first["cached"] is False

    second = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    assert second["cached"] is True
    # CRITICAL: bytes-identical so Anthropic prompt cache hits
    assert first["bundle"] == second["bundle"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_isolates_per_team(session, seeded_two_teams):
    """Cache is keyed by team_id; teams don't leak across."""
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
              (team_scope, content, truth_level, validation_status, source, visibility)
            VALUES (:team, 'secret_A', 'WORKING', 'n/a', 'test', 'team')
            """
        ),
        {"team": team_a.slug},
    )
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
              (team_scope, content, truth_level, validation_status, source, visibility)
            VALUES (:team, 'secret_B', 'WORKING', 'n/a', 'test', 'team')
            """
        ),
        {"team": team_b.slug},
    )
    await session.commit()

    a = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    b = await tcc.get_team_memory_bundle(
        session, team_scope=team_b.slug, team_id=team_b.id
    )
    assert "secret_A" in a["bundle"] and "secret_B" not in a["bundle"]
    assert "secret_B" in b["bundle"] and "secret_A" not in b["bundle"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalidate_drops_cache_entry(session, seeded_two_teams):
    """tcc.invalidate(team_id) forces a fresh fetch on the next call."""
    team_a = seeded_two_teams["team_a"]
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
              (team_scope, content, truth_level, validation_status, source, visibility)
            VALUES (:team, 'v1', 'WORKING', 'n/a', 'test', 'team')
            """
        ),
        {"team": team_a.slug},
    )
    await session.commit()

    first = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    assert first["cached"] is False

    tcc.invalidate(team_a.id)
    second = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    assert second["cached"] is False  # cache was invalidated


def test_invalidate_unknown_team_is_noop():
    """invalidate() on a team that was never cached doesn't raise."""
    tcc.invalidate(uuid4())  # should not raise

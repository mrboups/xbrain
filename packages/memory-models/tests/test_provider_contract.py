"""Contract tests for any MemoryProvider implementation.

Run against ALL registered providers via parametrize. Adding a new provider in conftest
PROVIDERS_TO_TEST automatically runs all these tests against it. Goal: a new impl is OK
iff all these tests pass.
"""

from datetime import datetime, timezone

import pytest

from xbrain_memory.types import MemoryItem, TruthLevel


def _make_item(team: str, content: str, **overrides) -> MemoryItem:
    now = datetime.now(timezone.utc)
    base = dict(
        id="",
        team_scope=team,
        content=content,
        source="test:contract",
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return MemoryItem(**base)


@pytest.mark.asyncio
async def test_health(provider):
    h = await provider.health()
    assert h["status"] in ("ok", "degraded", "down")
    assert "backend" in h


@pytest.mark.asyncio
async def test_upsert_returns_id(provider):
    item = _make_item("team-a", "alpha")
    iid = await provider.upsert(item)
    assert iid and isinstance(iid, str)


@pytest.mark.asyncio
async def test_get_round_trip(provider):
    item = _make_item("team-a", "round-trip content")
    iid = await provider.upsert(item)
    fetched = await provider.get(iid, team_scope="team-a")
    assert fetched is not None
    assert fetched.content == "round-trip content"
    assert fetched.team_scope == "team-a"


@pytest.mark.asyncio
async def test_get_wrong_team_returns_none(provider):
    """Critical: team isolation invariant."""
    item = _make_item("team-a", "secret to team-a")
    iid = await provider.upsert(item)
    leak = await provider.get(iid, team_scope="team-b")
    assert leak is None, "team isolation broken — team-b can read team-a's data"


@pytest.mark.asyncio
async def test_search_filters_team(provider):
    """Search returns only items matching team_scope."""
    await provider.upsert(_make_item("team-a", "shared keyword apple a1"))
    await provider.upsert(_make_item("team-a", "shared keyword apple a2"))
    await provider.upsert(_make_item("team-b", "shared keyword apple b1"))
    hits = await provider.search("apple", team_scope="team-a")
    assert len(hits) == 2
    assert all(h.item.team_scope == "team-a" for h in hits)


@pytest.mark.asyncio
async def test_search_truth_level_min(provider):
    """truth_level_min filters out items below the min."""
    await provider.upsert(
        _make_item("team-a", "ephemeral fact", truth_level=TruthLevel.EPHEMERAL)
    )
    await provider.upsert(
        _make_item("team-a", "canonical fact", truth_level=TruthLevel.CANONICAL)
    )
    hits = await provider.search(
        "fact", team_scope="team-a", truth_level_min=TruthLevel.VALIDATED
    )
    contents = {h.item.content for h in hits}
    assert "canonical fact" in contents
    assert "ephemeral fact" not in contents


@pytest.mark.asyncio
async def test_update_changes_content(provider):
    item = _make_item("team-a", "original")
    iid = await provider.upsert(item)
    updated = await provider.update(iid, team_scope="team-a", patch={"content": "modified"})
    assert updated.content == "modified"


@pytest.mark.asyncio
async def test_update_wrong_team_raises(provider):
    item = _make_item("team-a", "stuff")
    iid = await provider.upsert(item)
    with pytest.raises((KeyError, PermissionError, ValueError)):
        await provider.update(iid, team_scope="team-b", patch={"content": "hijacked"})


@pytest.mark.asyncio
async def test_delete_idempotent(provider):
    iid = await provider.upsert(_make_item("team-a", "to delete"))
    await provider.delete(iid, team_scope="team-a")
    # 2nd delete should not raise
    await provider.delete(iid, team_scope="team-a")
    assert await provider.get(iid, team_scope="team-a") is None


@pytest.mark.asyncio
async def test_delete_wrong_team_silent(provider):
    """Delete with wrong team is a no-op (idempotent), doesn't error or leak."""
    iid = await provider.upsert(_make_item("team-a", "to keep"))
    await provider.delete(iid, team_scope="team-b")  # should NOT delete
    assert await provider.get(iid, team_scope="team-a") is not None


@pytest.mark.asyncio
async def test_history_returns_versions(provider):
    iid = await provider.upsert(_make_item("team-a", "v1"))
    await provider.update(iid, team_scope="team-a", patch={"content": "v2"})
    await provider.update(iid, team_scope="team-a", patch={"content": "v3"})
    history = await provider.history(iid, team_scope="team-a")
    assert len(history) >= 1, "at minimum current version returned"
    contents = [h.content for h in history]
    assert "v3" in contents  # current is in history

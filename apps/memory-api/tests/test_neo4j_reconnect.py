"""Phase 15 — the reconnect loop that replaces the depends_on edge 15-01 had to remove.

These are fast unit tests (monkeypatched init_driver, interval_s=0) that prove reconnect_loop()'s
three behaviours in isolation: it does nothing when Neo4j is not configured, it connects (and stops
retrying) the moment a late Neo4j appears, and it gives up quietly after a bounded window when Neo4j
never appears.

The real container race — memory-api booted before Neo4j, Neo4j started late, proving the loop
actually reconnects to a real container over the network, plus the control run proving it does NOT
self-heal without reconnect_loop — is NOT here. It cannot be a permanent, always-green test: it needs
a real Neo4j image pulled and a real cold-start race reproduced with wall-clock sleeps, which is slow
and environment-dependent. That run was performed manually and its results (with actual log lines) are
recorded in 15-05-SUMMARY.md, per the plan's own instruction to make it a "documented manual run...not
a permanent test."
"""

import asyncio

import pytest

import app.neo4j_client as neo4j_client


@pytest.mark.asyncio
async def test_reconnect_returns_immediately_when_not_configured(monkeypatch):
    """OSS-light with NEO4J_* blank: no retries at all, no sleeping."""
    from app.config import settings

    monkeypatch.setattr(settings, "NEO4J_URI", "")
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", "")
    calls = []
    monkeypatch.setattr(neo4j_client, "init_driver", lambda **kw: calls.append(kw))

    await asyncio.wait_for(neo4j_client.reconnect_loop(attempts=6, interval_s=0), timeout=1)
    assert calls == [], "must not attempt a connection when Neo4j is not configured"


@pytest.mark.asyncio
async def test_reconnect_connects_when_neo4j_appears_late(monkeypatch):
    """THE regression this plan exists for: memory-api started first, Neo4j became healthy after."""
    from app.config import settings

    monkeypatch.setattr(settings, "NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", "pw")
    monkeypatch.setattr(neo4j_client, "_driver", None)

    sentinel = object()
    attempts = {"n": 0}

    async def fake_init(quiet: bool = False):
        attempts["n"] += 1
        if attempts["n"] >= 3:  # Neo4j finishes starting on the 3rd try
            neo4j_client._driver = sentinel
        return neo4j_client._driver

    monkeypatch.setattr(neo4j_client, "init_driver", fake_init)

    await asyncio.wait_for(neo4j_client.reconnect_loop(attempts=6, interval_s=0), timeout=5)

    assert neo4j_client.get_driver() is sentinel, "the loop must connect once Neo4j appears"
    assert attempts["n"] == 3, "and must STOP retrying the moment it succeeds"


@pytest.mark.asyncio
async def test_reconnect_gives_up_after_a_bounded_window(monkeypatch):
    """OSS-light with a default NEO4J_URI and no Neo4j: bounded, quiet, no crash."""
    from app.config import settings

    monkeypatch.setattr(settings, "NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", "pw")
    monkeypatch.setattr(neo4j_client, "_driver", None)

    attempts = {"n": 0}

    async def always_fails(quiet: bool = False):
        attempts["n"] += 1
        assert quiet is True, "retries must be QUIET — six ERRORs on every default boot is noise"
        return None

    monkeypatch.setattr(neo4j_client, "init_driver", always_fails)

    await asyncio.wait_for(neo4j_client.reconnect_loop(attempts=6, interval_s=0), timeout=5)

    assert attempts["n"] == 6, "must be BOUNDED — an unbounded loop would retry a doomed connection forever"
    assert neo4j_client.get_driver() is None

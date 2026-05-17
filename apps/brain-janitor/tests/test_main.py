"""Unit tests for main.run_once + the 03:00 UTC next-run scheduler."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.config import Settings
from app.main import _next_run_at, run_once


def test_next_run_at_morning_returns_today_3am_if_before_3am():
    """At 02:30 UTC the next run is today at 03:00."""
    now = datetime(2026, 5, 17, 2, 30, tzinfo=timezone.utc)
    nxt = _next_run_at(now)
    assert nxt == datetime(2026, 5, 17, 3, 0, tzinfo=timezone.utc)


def test_next_run_at_afternoon_returns_tomorrow_3am():
    """At 15:00 UTC the next run is tomorrow at 03:00."""
    now = datetime(2026, 5, 17, 15, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now)
    assert nxt == datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)


def test_next_run_at_exactly_3am_returns_tomorrow_3am():
    """At 03:00 sharp the next run is tomorrow (we just woke for this one)."""
    now = datetime(2026, 5, 17, 3, 0, tzinfo=timezone.utc)
    nxt = _next_run_at(now)
    assert nxt == datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)


def test_next_run_at_custom_hour():
    """Hour is parameterisable (defensive — not used in prod but cheap to lock)."""
    now = datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc)
    assert _next_run_at(now, hour=4) == datetime(
        2026, 5, 18, 4, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_run_once_dispatches_qdrant_neo4j_when_memory_items_purged(tmp_path):
    """run_once: PG returns memory_items UUIDs → Qdrant + Neo4j are called."""
    mem_ids = [uuid4(), uuid4()]

    fake_pool = AsyncMock()
    fake_pool.close = AsyncMock()

    settings = Settings(
        DATABASE_URL="postgresql://x:y@h/db",
        QDRANT_URL="http://q:6333",
        NEO4J_URI="bolt://n:7687",
        NEO4J_USER="neo4j",
        NEO4J_PASSWORD="pwd",
    )

    with patch("app.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch(
             "app.main.purge_pg",
             AsyncMock(return_value={"memory_items": mem_ids, "tasks": []}),
         ) as p_pg, \
         patch("app.main.purge_qdrant", AsyncMock(return_value=2)) as p_q, \
         patch("app.main.purge_neo4j", AsyncMock(return_value=2)) as p_n, \
         patch("app.main.SENTINEL_PATH", tmp_path / "brain-janitor-alive"):
        await run_once(settings)

    p_pg.assert_awaited_once()
    p_q.assert_awaited_once_with("http://q:6333", "messages", mem_ids)
    p_n.assert_awaited_once_with("bolt://n:7687", "neo4j", "pwd", mem_ids)
    fake_pool.close.assert_awaited_once()
    # Sentinel was touched
    assert (tmp_path / "brain-janitor-alive").exists()


@pytest.mark.asyncio
async def test_run_once_skips_qdrant_neo4j_when_no_memory_items(tmp_path):
    """run_once: PG returns 0 memory_items → Qdrant + Neo4j NOT called."""
    fake_pool = AsyncMock()
    fake_pool.close = AsyncMock()

    settings = Settings(
        DATABASE_URL="postgresql://x:y@h/db",
        NEO4J_PASSWORD="pwd",
    )

    with patch("app.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch(
             "app.main.purge_pg",
             AsyncMock(return_value={"memory_items": [], "tasks": []}),
         ), \
         patch("app.main.purge_qdrant", AsyncMock()) as p_q, \
         patch("app.main.purge_neo4j", AsyncMock()) as p_n, \
         patch("app.main.SENTINEL_PATH", tmp_path / "brain-janitor-alive"):
        await run_once(settings)

    p_q.assert_not_awaited()
    p_n.assert_not_awaited()
    # Sentinel still touched — empty run is still a successful run
    assert (tmp_path / "brain-janitor-alive").exists()


@pytest.mark.asyncio
async def test_run_once_closes_pool_even_on_error(tmp_path):
    """If purge_pg raises, the asyncpg pool is still closed (finally block)."""
    fake_pool = AsyncMock()
    fake_pool.close = AsyncMock()

    settings = Settings(
        DATABASE_URL="postgresql://x:y@h/db",
        NEO4J_PASSWORD="pwd",
    )

    with patch("app.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch(
             "app.main.purge_pg",
             AsyncMock(side_effect=RuntimeError("boom")),
         ), \
         patch("app.main.SENTINEL_PATH", tmp_path / "brain-janitor-alive"):
        with pytest.raises(RuntimeError, match="boom"):
            await run_once(settings)

    fake_pool.close.assert_awaited_once()
    # Sentinel NOT touched on failure
    assert not (tmp_path / "brain-janitor-alive").exists()

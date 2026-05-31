"""Tests for mongo_watcher.resolve_team_scope — per-user team resolution with TTL cache.

Three cases:
  1. Client returns a slug -> slug is returned and cached.
  2. Client returns None  -> BRIDGE_DEFAULT_TEAM_SCOPE fallback returned, NOT cached.
  3. Cache hit within TTL -> client.resolve_team_scope called exactly once across two
     calls (second call served from cache).
"""
from __future__ import annotations

import os
import time

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

# Env defaults so app.config parses without real services
os.environ.setdefault("LIBRECHAT_MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("BRIDGE_DEFAULT_TEAM_SCOPE", "default")

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_client_mock(return_value: str | None) -> object:
    """Return a minimal object whose resolve_team_scope is an AsyncMock."""
    client = type("FakeMemClient", (), {})()
    client.resolve_team_scope = AsyncMock(return_value=return_value)
    return client


def _clear_cache() -> None:
    """Reset the module-level TTL cache before each test."""
    from app.mongo_watcher import _team_scope_cache

    _team_scope_cache.clear()


# ── Tests ─────────────────────────────────────────────────────────────────


async def test_returns_slug_when_client_resolves():
    """When client.resolve_team_scope returns a slug, resolve_team_scope returns it."""
    _clear_cache()
    from app.mongo_watcher import resolve_team_scope

    client = _make_client_mock(return_value="aibrussels")
    result = await resolve_team_scope(client, "email:alice@test.local")

    assert result == "aibrussels"
    client.resolve_team_scope.assert_called_once_with(sub="email:alice@test.local")


async def test_falls_back_to_default_when_client_returns_none():
    """When client.resolve_team_scope returns None, the default scope is returned."""
    _clear_cache()
    from app.config import settings
    from app.mongo_watcher import resolve_team_scope

    client = _make_client_mock(return_value=None)
    result = await resolve_team_scope(client, "email:unknown@test.local")

    assert result == settings.BRIDGE_DEFAULT_TEAM_SCOPE
    client.resolve_team_scope.assert_called_once()


async def test_second_call_within_ttl_served_from_cache():
    """After a successful resolution, the second call within TTL must NOT call the client again."""
    _clear_cache()
    from app.mongo_watcher import resolve_team_scope

    client = _make_client_mock(return_value="cachedteam")

    # First call — populates cache
    result1 = await resolve_team_scope(client, "email:cached@test.local")
    # Second call within TTL — should hit cache
    result2 = await resolve_team_scope(client, "email:cached@test.local")

    assert result1 == "cachedteam"
    assert result2 == "cachedteam"
    # Client must have been called exactly once (second call was a cache hit)
    assert client.resolve_team_scope.call_count == 1, (
        f"Expected client.resolve_team_scope to be called once, "
        f"got {client.resolve_team_scope.call_count}"
    )


async def test_fallback_is_not_cached_so_later_signup_self_heals():
    """None return must NOT be cached — the next call will hit the API again."""
    _clear_cache()
    from app.config import settings
    from app.mongo_watcher import _team_scope_cache, resolve_team_scope

    client = _make_client_mock(return_value=None)

    result = await resolve_team_scope(client, "email:new@test.local")

    assert result == settings.BRIDGE_DEFAULT_TEAM_SCOPE
    # Verify the fallback was NOT written into the cache
    assert "email:new@test.local" not in _team_scope_cache


async def test_exception_in_client_returns_default_without_raising():
    """If client.resolve_team_scope raises, the function must not propagate and must return default."""
    _clear_cache()
    from app.config import settings
    from app.mongo_watcher import resolve_team_scope

    client = type("FakeMemClient", (), {})()
    client.resolve_team_scope = AsyncMock(side_effect=RuntimeError("network gone"))

    result = await resolve_team_scope(client, "email:failing@test.local")

    assert result == settings.BRIDGE_DEFAULT_TEAM_SCOPE

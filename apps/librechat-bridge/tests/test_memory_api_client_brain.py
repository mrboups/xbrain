"""Tests for MemoryApiClient.brain_ingest + BRAIN_INGEST_ENABLED config.

TDD RED phase — all tests must fail before implementation.

Tests:
  1. config default: BRAIN_INGEST_ENABLED is True
  2. config override: BRAIN_INGEST_ENABLED=false -> False
  3. brain_ingest method signature: keyword-only params present
  4. request shape: correct POST path, headers, body
  5. retry on 5xx, no retry on 4xx
  6. HTTP error raised by client (not swallowed)
"""

import inspect
import os

import httpx
import pytest
import respx

# --- Ensure env defaults set (conftest.py may not have loaded yet) ---
os.environ.setdefault("LIBRECHAT_MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("BRAIN_INGEST_ENABLED", "true")


# -- Test 1: config default --


def test_brain_ingest_enabled_default_is_true():
    """BRAIN_INGEST_ENABLED should default to True."""
    import importlib
    import app.config as config_mod

    importlib.reload(config_mod)
    from app.config import settings as fresh_settings

    assert fresh_settings.BRAIN_INGEST_ENABLED is True


# -- Test 2: config override --


def test_brain_ingest_enabled_override_false(monkeypatch):
    """Setting BRAIN_INGEST_ENABLED=false via env should yield False."""
    monkeypatch.setenv("BRAIN_INGEST_ENABLED", "false")
    import importlib
    import app.config as config_mod

    importlib.reload(config_mod)
    from app.config import Settings

    fresh = Settings()  # type: ignore[call-arg]
    assert fresh.BRAIN_INGEST_ENABLED is False


# -- Test 3: method signature --


def test_brain_ingest_signature():
    """MemoryApiClient.brain_ingest must expose the correct keyword-only params."""
    from app.memory_api_client import MemoryApiClient

    sig = inspect.signature(MemoryApiClient.brain_ingest)
    params = sig.parameters

    required_kwargs = {"sub", "team_scope", "content", "source"}
    optional_kwargs = {"metadata", "project_scope"}
    all_expected = required_kwargs | optional_kwargs

    for name in all_expected:
        assert name in params, f"Expected param '{name}' missing from brain_ingest signature"

    # All should be keyword-only (no positional except self)
    for name, p in params.items():
        if name == "self":
            continue
        assert p.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), f"Param '{name}' must be keyword-accessible"

    # Optional params have defaults
    assert (
        params["metadata"].default is inspect.Parameter.empty
        or params["metadata"].default is None
    )
    assert params["project_scope"].default is None


# -- Test 4: request shape --


@pytest.mark.asyncio
@respx.mock
async def test_brain_ingest_request_shape():
    """brain_ingest must POST to /v1/brain/ingest with correct headers and body."""
    from app.memory_api_client import MemoryApiClient

    route = respx.post("http://memory-api.test/v1/brain/ingest").mock(
        return_value=httpx.Response(202, json={"status": "accepted"})
    )

    client = MemoryApiClient()
    try:
        result = await client.brain_ingest(
            sub="u@e.com",
            team_scope="t1",
            content="hello world test message",
            source="librechat:claude-haiku-4-5",
            metadata={"librechat_id": "abc", "idempotency_key": "librechat:abc"},
        )
    finally:
        await client.aclose()

    assert route.called, "Expected POST to /v1/brain/ingest"
    req = route.calls[0].request

    # Headers
    assert "Authorization" in req.headers
    assert req.headers["Authorization"].startswith("Bearer ")
    assert req.headers["X-Team-Scope"] == "t1"

    # Body
    import json

    body = json.loads(req.content)
    assert body["content"] == "hello world test message"
    assert body["source"] == "librechat:claude-haiku-4-5"
    assert body["metadata"] == {"librechat_id": "abc", "idempotency_key": "librechat:abc"}
    assert body["project_scope"] is None

    # Response forwarded
    assert result == {"status": "accepted"}


# -- Test 5a: retry on 5xx --


@pytest.mark.asyncio
@respx.mock
async def test_brain_ingest_retries_on_503_then_succeeds():
    """brain_ingest should retry on 5xx (3 total attempts)."""
    from app.memory_api_client import MemoryApiClient

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(202, json={"status": "accepted"})

    respx.post("http://memory-api.test/v1/brain/ingest").mock(side_effect=side_effect)

    client = MemoryApiClient()
    try:
        result = await client.brain_ingest(
            sub="u@e.com",
            team_scope="t1",
            content="retry test message content here",
            source="librechat:test",
        )
    finally:
        await client.aclose()

    assert call_count == 3, f"Expected 3 attempts, got {call_count}"
    assert result == {"status": "accepted"}


# -- Test 5b: single attempt on 4xx --


@pytest.mark.asyncio
@respx.mock
async def test_brain_ingest_single_attempt_on_422():
    """brain_ingest should NOT retry on 4xx client errors."""
    from app.memory_api_client import MemoryApiClient

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(422, json={"detail": "validation error"})

    respx.post("http://memory-api.test/v1/brain/ingest").mock(side_effect=side_effect)

    client = MemoryApiClient()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.brain_ingest(
                sub="u@e.com",
                team_scope="t1",
                content="client error test message here",
                source="librechat:test",
            )
    finally:
        await client.aclose()

    # httpx.HTTPStatusError is NOT a subclass of httpx.HTTPError so retry
    # predicate (retry_if_exception_type(httpx.HTTPError)) does not match.
    # Verify only 1 attempt was made.
    assert call_count == 1, f"Expected 1 attempt on 4xx, got {call_count}"


# -- Test 6: HTTP error raised by client (not swallowed) --


@pytest.mark.asyncio
@respx.mock
async def test_brain_ingest_raises_on_5xx_after_retries():
    """After all retries exhausted on 5xx, brain_ingest should raise HTTPStatusError."""
    from app.memory_api_client import MemoryApiClient

    respx.post("http://memory-api.test/v1/brain/ingest").mock(
        return_value=httpx.Response(503, text="always fails")
    )

    client = MemoryApiClient()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.brain_ingest(
                sub="u@e.com",
                team_scope="t1",
                content="always fails test message content",
                source="librechat:test",
            )
    finally:
        await client.aclose()

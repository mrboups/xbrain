"""Phase 13 plan 13-06: Tests for MemoryApiClient.brain_ingest + get_system_prompt
and Phase 13 config knobs.

RED phase — these must fail before Task 1 implementation is applied.
"""

import inspect
import os

os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("PIPELINE_API_KEY", "test-pipeline-api-key")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("PIPELINE_DEFAULT_TEAM_SCOPE", "default")

import httpx
import pytest
import respx
from httpx import Response


# ── Test 1: config knobs ──────────────────────────────────────────────────────

def test_config_brain_ingest_enabled():
    from app.config import settings
    assert settings.BRAIN_INGEST_ENABLED is True


def test_config_chat07_top_k():
    from app.config import settings
    assert settings.CHAT07_TOP_K == 5


def test_config_chat07_truth_filter_min_level():
    from app.config import settings
    assert settings.CHAT07_TRUTH_FILTER_MIN_LEVEL == "VALIDATED"


# ── Test 2: brain_ingest signature ────────────────────────────────────────────

def test_brain_ingest_signature():
    from app.memory_api_client import MemoryApiClient
    sig = inspect.signature(MemoryApiClient.brain_ingest)
    params = sig.parameters
    assert "sub" in params
    assert "team_scope" in params
    assert "content" in params
    assert "source" in params
    # metadata and project_scope are optional with defaults
    assert "metadata" in params
    assert params["metadata"].default is None
    assert "project_scope" in params
    assert params["project_scope"].default is None


# ── Test 3: brain_ingest request shape ────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_brain_ingest_request_shape():
    from app.memory_api_client import MemoryApiClient

    route = respx.post("http://memory-api.test/v1/brain/ingest").mock(
        return_value=Response(201, json={"id": "mem-item-1"})
    )

    client = MemoryApiClient()
    result = await client.brain_ingest(
        sub="alice",
        team_scope="team-a",
        content="Deploy window is Tuesday 14:00 UTC",
        source="openwebui:claude-haiku-4-5",
        metadata={"idempotency_key": "openwebui:conv-1:abc123"},
    )
    await client.aclose()

    assert route.called
    req = route.calls.last.request

    # Authorization header must be a Bearer JWT
    assert req.headers["authorization"].startswith("Bearer ")
    assert req.headers["x-team-scope"] == "team-a"

    import json
    body = json.loads(req.content)
    assert body["content"] == "Deploy window is Tuesday 14:00 UTC"
    assert body["source"] == "openwebui:claude-haiku-4-5"
    assert body["metadata"]["idempotency_key"] == "openwebui:conv-1:abc123"
    assert body["project_scope"] is None
    assert result == {"id": "mem-item-1"}


# ── Test 4: get_system_prompt signature includes min_level ───────────────────

def test_get_system_prompt_signature_includes_min_level():
    from app.memory_api_client import MemoryApiClient
    sig = inspect.signature(MemoryApiClient.get_system_prompt)
    params = sig.parameters
    assert "min_level" in params
    # min_level should default to None
    assert params["min_level"].default is None


# ── Test 5: get_system_prompt request shape with min_level ───────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_system_prompt_request_shape_with_min_level():
    from app.memory_api_client import MemoryApiClient

    route = respx.get("http://memory-api.test/v1/system-prompt").mock(
        return_value=Response(
            200,
            json={"system_addendum": "## Team facts\n- Postgres 17.9", "fact_count": 1},
        )
    )

    client = MemoryApiClient()
    result = await client.get_system_prompt(
        sub="alice",
        team_scope="team-a",
        query="what database do we use?",
        min_level="VALIDATED",
        top_k=5,
    )
    await client.aclose()

    assert route.called
    req = route.calls.last.request

    # Auth
    assert req.headers["authorization"].startswith("Bearer ")
    assert req.headers["x-team-scope"] == "team-a"

    # Query params must include min_level and top_k
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(str(req.url))
    qs = parse_qs(parsed.query)
    assert qs.get("min_level") == ["VALIDATED"]
    assert qs.get("top_k") == ["5"]

    assert result["system_addendum"] == "## Team facts\n- Postgres 17.9"
    assert result["fact_count"] == 1


# ── Test 6: brain_ingest fail-soft — client raises HTTPError on 5xx ──────────

@pytest.mark.asyncio
@respx.mock
async def test_brain_ingest_raises_on_5xx():
    """brain_ingest propagates HTTPError on 5xx — the CALLER (chat handler) swallows it."""
    from app.memory_api_client import MemoryApiClient

    respx.post("http://memory-api.test/v1/brain/ingest").mock(
        return_value=Response(500, text="internal server error")
    )

    client = MemoryApiClient()
    with pytest.raises(httpx.HTTPStatusError):
        await client.brain_ingest(
            sub="alice",
            team_scope="team-a",
            content="some content",
            source="openwebui:claude-haiku-4-5",
        )
    await client.aclose()

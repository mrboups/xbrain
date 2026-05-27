"""Phase 13 plan 13-06: Integration tests for brain ingest + per-turn enrichment in chat().

RED phase — written before implementation; must fail until Task 2 implementation applied.

Test coverage:
  1. Anthropic happy path with enrichment
  2. Anthropic — no facts, no injection
  3. OpenAI happy path with enrichment
  4. memory-api unreachable → chat still succeeds (fail-soft)
  5. Slash command short-circuits BEFORE enrichment
  6. Brain ingest fired for non-slash user message
  7. BRAIN_INGEST_ENABLED=false disables both ingest AND enrichment
  8. brain_ingest failure does NOT break chat
  9. Streaming with enrichment (Anthropic)
"""

import os

os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("PIPELINE_API_KEY", "test-pipeline-api-key")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("PIPELINE_DEFAULT_TEAM_SCOPE", "default")

import hashlib
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

AUTH_HEADERS = {
    "Authorization": "Bearer test-pipeline-api-key",
    "X-OpenWebUI-User-Id": "alice",
    "X-Team-Scope": "team-a",
}

FAKE_ANTHROPIC_RESPONSE = {
    "id": "msg_test123",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "The deploy window is Tuesday 14:00 UTC."}],
    "model": "claude-haiku-4-5-20251001",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 15},
}

FAKE_OPENAI_RESPONSE = MagicMock()
FAKE_OPENAI_RESPONSE.id = "chatcmpl-test123"
FAKE_OPENAI_RESPONSE.choices = [
    MagicMock(message=MagicMock(content="OpenAI enriched response"), finish_reason="stop")
]
FAKE_OPENAI_RESPONSE.usage = MagicMock(prompt_tokens=10, completion_tokens=15)


# ── Fixture: async HTTPX client bound to the FastAPI app ─────────────────────

@pytest.fixture
async def client():
    from app.main import app
    transport = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Test 1: Anthropic happy path with enrichment ─────────────────────────────

@pytest.mark.asyncio
async def test_anthropic_enrichment_injected_as_system_param(client):
    """Enrichment text must appear in Anthropic 'system' parameter, NOT in messages list."""
    addendum = "## Team facts\n- The deploy window is Tuesday 14:00 UTC"

    mock_response = AsyncMock()
    mock_response.id = "msg_test123"
    mock_response.content = [MagicMock(type="text", text="The deploy window is Tuesday 14:00 UTC.")]
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=15)

    captured_kwargs = {}

    async def fake_anthropic_create(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.anthropic_client") as mock_anthropic,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            return_value={"system_addendum": addendum, "fact_count": 1}
        )
        mock_mem.brain_ingest = AsyncMock(return_value={"id": "item-1"})
        mock_mem.post_message = AsyncMock(return_value={})
        mock_anthropic.messages.create = AsyncMock(side_effect=fake_anthropic_create)

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "remind me of our deploy window"}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text

    # Enrichment must be in the system= parameter
    assert "system" in captured_kwargs
    assert addendum in captured_kwargs["system"]

    # No role=system in messages list (Pitfall 5)
    for msg in captured_kwargs.get("messages", []):
        assert msg.get("role") != "system", f"system role found in messages list: {msg}"


# ── Test 2: Anthropic — no facts, no injection ───────────────────────────────

@pytest.mark.asyncio
async def test_anthropic_empty_enrichment_no_system_param(client):
    """When enrichment is empty, Anthropic call proceeds unchanged (no empty system param)."""
    mock_response = AsyncMock()
    mock_response.id = "msg_empty"
    mock_response.content = [MagicMock(type="text", text="I don't know.")]
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=5)

    captured_kwargs = {}

    async def fake_anthropic_create(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.anthropic_client") as mock_anthropic,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            return_value={"system_addendum": "", "fact_count": 0}
        )
        mock_mem.brain_ingest = AsyncMock(return_value={"id": "item-1"})
        mock_mem.post_message = AsyncMock(return_value={})
        mock_anthropic.messages.create = AsyncMock(side_effect=fake_anthropic_create)

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "anything?"}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text
    # system should be absent or None — no empty string injected
    system_val = captured_kwargs.get("system")
    assert not system_val, f"Expected no system param, got: {system_val!r}"


# ── Test 3: OpenAI happy path with enrichment ────────────────────────────────

@pytest.mark.asyncio
async def test_openai_enrichment_injected_as_first_system_message(client):
    """For OpenAI, enrichment goes as first system-role message in the messages list."""
    addendum = "## Team facts\n- Postgres 17.9 is the primary event store"

    captured_kwargs = {}

    async def fake_openai_create(**kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.id = "chatcmpl-123"
        resp.choices = [MagicMock(message=MagicMock(content="Postgres 17.9"), finish_reason="stop")]
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        return resp

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.openai_client") as mock_openai,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            return_value={"system_addendum": addendum, "fact_count": 1}
        )
        mock_mem.brain_ingest = AsyncMock(return_value={"id": "item-1"})
        mock_mem.post_message = AsyncMock(return_value={})
        mock_openai.chat.completions.create = AsyncMock(side_effect=fake_openai_create)

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "what database do we use?"}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text

    msgs = captured_kwargs.get("messages", [])
    assert len(msgs) >= 2
    # First message must be the system addendum
    assert msgs[0]["role"] == "system"
    assert addendum in msgs[0]["content"]


# ── Test 4: memory-api unreachable → chat still succeeds ─────────────────────

@pytest.mark.asyncio
async def test_enrichment_fetch_failure_chat_still_succeeds(client):
    """If get_system_prompt raises, chat must still return 200 with no enrichment."""
    mock_response = AsyncMock()
    mock_response.id = "msg_fallback"
    mock_response.content = [MagicMock(type="text", text="Unenriched response.")]
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=5)

    captured_kwargs = {}

    async def fake_anthropic_create(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.anthropic_client") as mock_anthropic,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            side_effect=httpx.HTTPError("connection refused")
        )
        mock_mem.brain_ingest = AsyncMock(return_value={"id": "item-1"})
        mock_mem.post_message = AsyncMock(return_value={})
        mock_anthropic.messages.create = AsyncMock(side_effect=fake_anthropic_create)

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "fallback test"}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text
    # system should be absent/None — original messages unmodified
    system_val = captured_kwargs.get("system")
    assert not system_val, f"Enrichment should be empty on failure, got: {system_val!r}"


# ── Test 5: Slash command bypasses enrichment ─────────────────────────────────

@pytest.mark.asyncio
async def test_slash_command_bypasses_enrichment_and_ingest(client):
    """Slash commands must short-circuit BEFORE enrichment or ingest are called."""
    with (
        patch("app.main.mem") as mock_mem,
        patch(
            "app.pipelines.ingestion_trigger.try_handle",
            new=AsyncMock(return_value="Ingest triggered!"),
        ),
    ):
        mock_mem.get_system_prompt = AsyncMock()
        mock_mem.brain_ingest = AsyncMock()

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "/ingest https://example.com"}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text
    # Enrichment and ingest must NOT have been called
    mock_mem.get_system_prompt.assert_not_called()
    mock_mem.brain_ingest.assert_not_called()


# ── Test 6: Brain ingest fired for non-slash messages ─────────────────────────

@pytest.mark.asyncio
async def test_brain_ingest_fired_for_normal_message(client):
    """brain_ingest must be called with correct source + idempotency_key for normal messages."""
    user_content = "remind me of our deploy window"
    expected_sha = hashlib.sha256(user_content.encode("utf-8")).hexdigest()[:32]

    mock_response = AsyncMock()
    mock_response.id = "msg_test"
    mock_response.content = [MagicMock(type="text", text="Deploy window is Tues")]
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=5)

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.anthropic_client") as mock_anthropic,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            return_value={"system_addendum": "", "fact_count": 0}
        )
        mock_mem.brain_ingest = AsyncMock(return_value={"id": "item-1"})
        mock_mem.post_message = AsyncMock(return_value={})
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": user_content}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text

    # brain_ingest should have been called
    mock_mem.brain_ingest.assert_called_once()
    call_kwargs = mock_mem.brain_ingest.call_args.kwargs

    assert call_kwargs["source"] == "openwebui:claude-haiku-4-5"
    assert call_kwargs["content"] == user_content
    assert call_kwargs["team_scope"] == "team-a"

    meta = call_kwargs.get("metadata", {})
    assert meta.get("origin") == "openwebui"
    assert "conversation_id" in meta
    idem_key = meta.get("idempotency_key", "")
    assert idem_key.startswith("openwebui:")
    assert expected_sha in idem_key


# ── Test 7: BRAIN_INGEST_ENABLED=false disables both ingest AND enrichment ────

@pytest.mark.asyncio
async def test_kill_switch_disables_ingest_and_enrichment(client):
    """When BRAIN_INGEST_ENABLED is False, neither brain_ingest nor get_system_prompt is called."""
    mock_response = AsyncMock()
    mock_response.id = "msg_disabled"
    mock_response.content = [MagicMock(type="text", text="response without enrichment")]
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=5)

    # Temporarily override the attribute on the live settings singleton
    from app.config import settings as live_settings

    original_value = live_settings.BRAIN_INGEST_ENABLED
    live_settings.BRAIN_INGEST_ENABLED = False
    try:
        with (
            patch("app.main.mem") as mock_mem,
            patch("app.main.anthropic_client") as mock_anthropic,
        ):
            mock_mem.get_system_prompt = AsyncMock()
            mock_mem.brain_ingest = AsyncMock()
            mock_mem.post_message = AsyncMock(return_value={})
            mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

            r = await client.post(
                "/v1/chat/completions",
                headers=AUTH_HEADERS,
                json={
                    "model": "claude-haiku-4-5",
                    "messages": [{"role": "user", "content": "test kill switch"}],
                    "stream": False,
                },
            )
    finally:
        live_settings.BRAIN_INGEST_ENABLED = original_value

    assert r.status_code == 200, r.text
    mock_mem.get_system_prompt.assert_not_called()
    mock_mem.brain_ingest.assert_not_called()


# ── Test 8: brain_ingest failure does NOT break chat ─────────────────────────

@pytest.mark.asyncio
async def test_brain_ingest_failure_does_not_break_chat(client):
    """If brain_ingest raises, the chat response must still return 200."""
    mock_response = AsyncMock()
    mock_response.id = "msg_ingest_fail"
    mock_response.content = [MagicMock(type="text", text="response despite ingest failure")]
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=5)

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.anthropic_client") as mock_anthropic,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            return_value={"system_addendum": "", "fact_count": 0}
        )
        # brain_ingest raises — should be swallowed by _brain_ingest_owui
        mock_mem.brain_ingest = AsyncMock(
            side_effect=RuntimeError("ingest service down")
        )
        mock_mem.post_message = AsyncMock(return_value={})
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "test ingest failure"}],
                "stream": False,
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "response despite ingest failure"


# ── Test 9: Streaming with enrichment (Anthropic) ────────────────────────────

@pytest.mark.asyncio
async def test_anthropic_streaming_with_enrichment(client):
    """Streaming response must still receive enrichment in system param."""
    addendum = "## Team facts\n- Deploy on Tuesdays"

    # Create an async context manager mock that yields text chunks
    class FakeStream:
        def __init__(self):
            self.text_stream = self._gen()

        async def _gen(self):
            for chunk in ["Deploy", " on", " Tuesdays"]:
                yield chunk

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    captured_stream_kwargs = {}

    class FakeStreamContextManager:
        def __init__(self, **kwargs):
            captured_stream_kwargs.update(kwargs)

        async def __aenter__(self):
            return FakeStream()

        async def __aexit__(self, *args):
            pass

    with (
        patch("app.main.mem") as mock_mem,
        patch("app.main.anthropic_client") as mock_anthropic,
    ):
        mock_mem.get_system_prompt = AsyncMock(
            return_value={"system_addendum": addendum, "fact_count": 1}
        )
        mock_mem.brain_ingest = AsyncMock(return_value={"id": "item-1"})
        mock_mem.post_message = AsyncMock(return_value={})
        mock_anthropic.messages.stream = FakeStreamContextManager

        r = await client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "when do we deploy?"}],
                "stream": True,
            },
        )

    # Streaming response: 200 with event-stream content type
    assert r.status_code == 200, r.text
    # The system param should contain the addendum
    assert "system" in captured_stream_kwargs
    assert addendum in captured_stream_kwargs["system"]
    # No role=system in messages
    for msg in captured_stream_kwargs.get("messages", []):
        assert msg.get("role") != "system"

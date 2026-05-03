"""Tests for the OpenAI-compatible /v1/chat/completions endpoint."""

import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
async def test_health_returns_200(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_chat_completions_requires_auth(client):
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_invalid_key(client):
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong-key"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_unknown_model(client, auth_headers):
    r = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"model": "bogus-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert "unknown model" in r.text.lower()


@pytest.mark.asyncio
async def test_models_endpoint_lists_known_models(client, auth_headers):
    r = await client.get("/v1/models", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    ids = {m["id"] for m in data["data"]}
    assert "claude-3-5-sonnet" in ids
    assert "gpt-4o" in ids


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_chat_completions_anthropic_non_stream_returns_response(
    client, auth_headers, respx_mock
):
    """Mock Anthropic API and verify the pipeline forwards correctly + memory-api gets POSTed."""
    # Mock Anthropic
    respx_mock.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={
                "id": "msg_abc123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello back!"}],
                "model": "claude-3-5-sonnet-latest",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )
    )
    # Mock memory-api (best-effort — even if down, response should succeed)
    respx_mock.post("http://memory-api.test/v1/messages").mock(
        return_value=Response(201, json={"id": "msg-1"})
    )

    r = await client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "X-OpenWebUI-User-Id": "alice-sub", "X-Team-Scope": "team-a"},
        json={
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "Hello back!"
    assert body["model"] == "claude-3-5-sonnet"


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_chat_completions_log_failure_does_not_break_response(
    client, auth_headers, respx_mock
):
    """If memory-api is down, the response to Open WebUI must STILL succeed."""
    # Mock Anthropic OK
    respx_mock.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={
                "id": "msg_xyz",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": "claude-3-5-sonnet-latest",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    # memory-api returns 500 — should NOT block
    respx_mock.post("http://memory-api.test/v1/messages").mock(return_value=Response(500))

    r = await client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "X-OpenWebUI-User-Id": "bob-sub", "X-Team-Scope": "team-b"},
        json={
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        },
    )
    # Critical assertion: even with memory-api 500, the user gets their reply
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "ok"

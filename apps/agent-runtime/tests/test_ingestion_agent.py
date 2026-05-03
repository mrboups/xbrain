"""Ingestion-agent tests — load → extract → HITL → write.

External calls are mocked:
  - anthropic via monkeypatched _client + a stub messages.create
  - URL fetches via respx (mocks httpx)
  - memory-api upserts via respx — we assert the right items get POSTed

Marker `integration` because LangGraph PostgresSaver requires Postgres
(testcontainers); the same fixture suite as test_hitl_resume.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
import respx

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _StubAnthropicMessages:
    """Stand-in for anthropic.AsyncAnthropic().messages.create()."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"facts": []}
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))]
        )


def _patch_anthropic(monkeypatch, payload: dict) -> _StubAnthropicMessages:
    """Replace the singleton anthropic client with a stub returning `payload`."""
    from app.tools import extract_facts as ef

    stub_messages = _StubAnthropicMessages(payload)
    stub_client = SimpleNamespace(messages=stub_messages)
    monkeypatch.setattr(ef, "_client", stub_client)
    monkeypatch.setattr(ef.settings, "ANTHROPIC_API_KEY", "test-key")
    return stub_messages


async def test_load_url_extract_pause_at_review(client, auth_headers, monkeypatch):
    """Happy path up to interrupt: URL fetched, facts extracted, paused at await_review."""
    payload = {
        "facts": [
            {"content": "auth uses Google OAuth", "suggested_truth_level": "WORKING", "confidence": 0.9},
            {"content": "DB is Postgres 17", "suggested_truth_level": "VALIDATED", "confidence": 0.95},
        ]
    }
    _patch_anthropic(monkeypatch, payload)

    with respx.mock(assert_all_called=False) as m:
        m.get("https://example.com/doc").respond(
            200, text="auth section ... db section ..."
        )
        # Allow memory-api upsert (won't be called since we don't /resume here)
        m.post("http://memory-api.test/v1/memory/upsert").respond(201, json={"id": "x"})

        r = await client.post(
            "/v1/agents/ingestion/run",
            headers=auth_headers,
            json={
                "initial_state": {
                    "source_type": "url",
                    "source_url": "https://example.com/doc",
                    "team_scope": "team-a",
                    "sub": "alice",
                }
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "await_review" in data["interrupted_at"]
        facts = data["state"].get("extracted_facts") or []
        assert len(facts) == 2
        assert facts[0]["suggested_truth_level"] == "WORKING"


async def test_resume_with_partial_approval_writes_only_selected(
    client, auth_headers, monkeypatch
):
    payload = {
        "facts": [
            {"content": "fact 0", "confidence": 0.9},
            {"content": "fact 1", "confidence": 0.8},
            {"content": "fact 2", "confidence": 0.7},
        ]
    }
    _patch_anthropic(monkeypatch, payload)

    with respx.mock(assert_all_called=False) as m:
        m.get("https://example.com/doc").respond(200, text="content")
        upsert_route = m.post("http://memory-api.test/v1/memory/upsert").respond(
            201, json={"id": "generated-id"}
        )

        r1 = await client.post(
            "/v1/agents/ingestion/run",
            headers=auth_headers,
            json={
                "initial_state": {
                    "source_type": "url",
                    "source_url": "https://example.com/doc",
                    "team_scope": "team-a",
                    "sub": "alice",
                }
            },
        )
        thread_id = r1.json()["thread_id"]
        assert len(r1.json()["state"].get("extracted_facts") or []) == 3

        r2 = await client.post(
            f"/v1/agents/{thread_id}/resume",
            headers=auth_headers,
            json={
                "agent_name": "ingestion",
                "state_update": {"approved_indexes": [0, 2]},
            },
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["interrupted_at"] == []
        # Only 2 upserts hit memory-api
        assert upsert_route.call_count == 2
        # Verify the bodies contain the right contents (0 and 2, not 1)
        bodies = [json.loads(c.request.content) for c in upsert_route.calls]
        contents = [b["item"]["content"] for b in bodies]
        assert "fact 0" in contents
        assert "fact 2" in contents
        assert "fact 1" not in contents


async def test_pdf_b64_path_extracts_and_pauses(client, auth_headers, monkeypatch):
    """PDF source path: agent decodes b64, pypdf reads it, extract pauses for review."""
    pytest.importorskip("pypdf")
    from io import BytesIO

    from pypdf import PdfWriter

    # Build a tiny 1-page PDF in memory (blank — pypdf still gives ""/None text)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    pdf_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    payload = {"facts": [{"content": "from pdf", "confidence": 0.6}]}
    _patch_anthropic(monkeypatch, payload)

    with respx.mock(assert_all_called=False) as m:
        m.post("http://memory-api.test/v1/memory/upsert").respond(201, json={"id": "x"})
        r = await client.post(
            "/v1/agents/ingestion/run",
            headers=auth_headers,
            json={
                "initial_state": {
                    "source_type": "pdf",
                    "pdf_b64": pdf_b64,
                    "team_scope": "team-a",
                    "sub": "alice",
                }
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "await_review" in body["interrupted_at"]
        assert (body["state"].get("extracted_facts") or [{}])[0]["content"] == "from pdf"


async def test_extract_failure_returns_empty_facts_no_crash(
    client, auth_headers, monkeypatch
):
    """Anthropic returns garbage → 0 facts, graph still pauses cleanly at await_review."""
    # Patch with a payload that won't parse
    from app.tools import extract_facts as ef

    class _GarbageMessages:
        async def create(self, **_):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="not json at all")])

    monkeypatch.setattr(ef, "_client", SimpleNamespace(messages=_GarbageMessages()))
    monkeypatch.setattr(ef.settings, "ANTHROPIC_API_KEY", "test-key")

    with respx.mock(assert_all_called=False) as m:
        m.get("https://example.com/doc").respond(200, text="some content")
        m.post("http://memory-api.test/v1/memory/upsert").respond(201, json={"id": "x"})

        r = await client.post(
            "/v1/agents/ingestion/run",
            headers=auth_headers,
            json={
                "initial_state": {
                    "source_type": "url",
                    "source_url": "https://example.com/doc",
                    "team_scope": "team-a",
                    "sub": "alice",
                }
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Pipeline reached interrupt with empty facts list — no crash
        assert "await_review" in body["interrupted_at"]
        assert body["state"].get("extracted_facts") == []

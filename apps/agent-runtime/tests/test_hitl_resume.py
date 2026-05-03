"""HITL contract tests — interrupt + resume across HTTP requests.

These verify that:
  - /run pauses at interrupt_before and persists state to Postgres
  - /state correctly surfaces the pause point
  - /resume picks up where /run left off and finishes the graph
  - Two threads on the same agent maintain isolated state

Marker `integration` so unit-test runs without Docker still pass.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_run_creates_thread_and_interrupts_at_await_approval(client, auth_headers):
    r = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers=auth_headers,
        json={"initial_state": {"user_text": "hello world"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "echo-with-hitl"
    assert body["thread_id"]
    # draft_node ran → state has draft populated
    assert body["state"].get("draft") == "Echo: hello world"
    # graph paused before await_approval (interrupt_before)
    assert "await_approval" in body["interrupted_at"]
    # final not yet computed
    assert "final" not in body["state"] or body["state"].get("final") in (None, "")


async def test_state_endpoint_returns_state_after_interrupt(client, auth_headers):
    r1 = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers=auth_headers,
        json={"initial_state": {"user_text": "ping"}},
    )
    thread_id = r1.json()["thread_id"]

    r2 = await client.get(
        f"/v1/agents/{thread_id}/state",
        headers=auth_headers,
        params={"agent_name": "echo-with-hitl"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["thread_id"] == thread_id
    assert body["state"].get("draft") == "Echo: ping"
    assert "await_approval" in body["next"]


async def test_resume_with_approved_true_completes_to_final(client, auth_headers):
    r1 = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers=auth_headers,
        json={"initial_state": {"user_text": "approve me"}},
    )
    thread_id = r1.json()["thread_id"]

    r2 = await client.post(
        f"/v1/agents/{thread_id}/resume",
        headers=auth_headers,
        json={"agent_name": "echo-with-hitl", "state_update": {"approved": True}},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    # Graph finished — no pending interrupt
    assert body["interrupted_at"] == []
    assert body["state"].get("final") == "Echo: approve me"


async def test_resume_with_approved_false_outputs_rejected(client, auth_headers):
    r1 = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers=auth_headers,
        json={"initial_state": {"user_text": "reject me"}},
    )
    thread_id = r1.json()["thread_id"]

    r2 = await client.post(
        f"/v1/agents/{thread_id}/resume",
        headers=auth_headers,
        json={"agent_name": "echo-with-hitl", "state_update": {"approved": False}},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["state"].get("final") == "(rejected by human)"


async def test_two_threads_isolated_state(client, auth_headers):
    """Each thread_id maintains its own state — no cross-contamination."""
    r_a = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers=auth_headers,
        json={"initial_state": {"user_text": "alpha"}, "thread_id": "thread-A"},
    )
    r_b = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers=auth_headers,
        json={"initial_state": {"user_text": "beta"}, "thread_id": "thread-B"},
    )
    assert r_a.status_code == 200
    assert r_b.status_code == 200

    # Approve A, leave B pending
    await client.post(
        "/v1/agents/thread-A/resume",
        headers=auth_headers,
        json={"agent_name": "echo-with-hitl", "state_update": {"approved": True}},
    )

    state_a = (
        await client.get(
            "/v1/agents/thread-A/state",
            headers=auth_headers,
            params={"agent_name": "echo-with-hitl"},
        )
    ).json()
    state_b = (
        await client.get(
            "/v1/agents/thread-B/state",
            headers=auth_headers,
            params={"agent_name": "echo-with-hitl"},
        )
    ).json()

    assert state_a["state"].get("final") == "Echo: alpha"
    assert state_a["next"] == []  # done
    assert state_b["state"].get("draft") == "Echo: beta"
    assert "await_approval" in state_b["next"]  # still paused


async def test_unknown_agent_returns_404(client, auth_headers):
    r = await client.post(
        "/v1/agents/no-such-agent/run",
        headers=auth_headers,
        json={"initial_state": {}},
    )
    assert r.status_code == 404


async def test_missing_team_scope_header_returns_422_or_401(client):
    """Calling without X-Team-Scope must be rejected."""
    r = await client.post(
        "/v1/agents/echo-with-hitl/run",
        headers={"Authorization": "Bearer fake"},
        json={"initial_state": {"user_text": "x"}},
    )
    # Either FastAPI 422 (header missing) or 401 (auth) — both acceptable as guards
    assert r.status_code in (401, 422)

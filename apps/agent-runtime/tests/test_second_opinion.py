"""Second-opinion agent tests — parallel Claude+Grok with graceful degradation.

We monkeypatch `call_claude` / `call_grok` directly (rather than the SDK clients)
because constructing the underlying clients in-call makes per-call mocking awkward
and the function-level seam is exactly the contract we care about.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _stub_call(text: str, error: str | None = None):
    async def _f(prompt: str):
        return (text, error)

    return _f


async def test_both_apis_succeed_returns_combined_markdown(client, auth_headers, monkeypatch):
    from app.graphs import second_opinion as so

    monkeypatch.setattr(so, "call_claude", await _stub_call("Claude says X."))
    monkeypatch.setattr(so, "call_grok", await _stub_call("Grok says X too."))

    r = await client.post(
        "/v1/agents/second-opinion/run",
        headers=auth_headers,
        json={"initial_state": {"prompt": "explain X"}},
    )
    assert r.status_code == 200, r.text
    md = r.json()["state"]["final_markdown"]
    assert "## Claude" in md
    assert "Claude says X." in md
    assert "## Grok" in md
    assert "Grok says X too." in md
    assert "## Diff highlights" in md


async def test_claude_fails_grok_succeeds_returns_grok_with_note(
    client, auth_headers, monkeypatch
):
    from app.graphs import second_opinion as so

    monkeypatch.setattr(so, "call_claude", await _stub_call("", "RateLimitError: 429"))
    monkeypatch.setattr(so, "call_grok", await _stub_call("Grok answer here."))

    r = await client.post(
        "/v1/agents/second-opinion/run",
        headers=auth_headers,
        json={"initial_state": {"prompt": "p"}},
    )
    md = r.json()["state"]["final_markdown"]
    assert "_unavailable: RateLimitError" in md
    assert "Grok answer here." in md
    # Diff section can't really compare when one side is missing
    assert "can't diff" in md or "missing" in md


async def test_both_fail_returns_diagnostic(client, auth_headers, monkeypatch):
    from app.graphs import second_opinion as so

    monkeypatch.setattr(so, "call_claude", await _stub_call("", "AuthError: 401"))
    monkeypatch.setattr(so, "call_grok", await _stub_call("", "ConnectError: timeout"))

    r = await client.post(
        "/v1/agents/second-opinion/run",
        headers=auth_headers,
        json={"initial_state": {"prompt": "p"}},
    )
    md = r.json()["state"]["final_markdown"]
    assert "_unavailable: AuthError" in md
    assert "_unavailable: ConnectError" in md
    assert "can't diff" in md or "missing" in md


async def test_diff_highlights_length_difference(client, auth_headers, monkeypatch):
    """Trigger the length-delta heuristic: one short response, one long."""
    from app.graphs import second_opinion as so

    short = "Yes."
    long = "Yes. " + ("This is a very long explanation. " * 30)  # > 200 chars delta
    monkeypatch.setattr(so, "call_claude", await _stub_call(short))
    monkeypatch.setattr(so, "call_grok", await _stub_call(long))

    r = await client.post(
        "/v1/agents/second-opinion/run",
        headers=auth_headers,
        json={"initial_state": {"prompt": "p"}},
    )
    md = r.json()["state"]["final_markdown"]
    assert "Length differs significantly" in md


async def test_diff_highlights_none_when_responses_similar(
    client, auth_headers, monkeypatch
):
    """When responses are similar in length & tone, the diff heuristic surfaces 'no obvious'."""
    from app.graphs import second_opinion as so

    a = "Quantum entanglement links particles. Measuring one instantly affects the other."
    b = "Entanglement is when particles share state. Measure one and the other reflects it."
    monkeypatch.setattr(so, "call_claude", await _stub_call(a))
    monkeypatch.setattr(so, "call_grok", await _stub_call(b))

    r = await client.post(
        "/v1/agents/second-opinion/run",
        headers=auth_headers,
        json={"initial_state": {"prompt": "p"}},
    )
    md = r.json()["state"]["final_markdown"]
    assert "No obvious surface-level divergences" in md

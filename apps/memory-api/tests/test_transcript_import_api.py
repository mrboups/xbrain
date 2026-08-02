"""POST /v1/import/transcript and the scoped token that reaches it.

The test that matters here is not that the import token works. It is that it
does not work ANYWHERE ELSE — a credential that lives on a phone, inside a
shortcut that can be shared in one tap, has to be narrower than the account
that minted it. So:

* ``test_import_token_is_refused_at_*`` hits four unrelated authenticated
  endpoints with a valid, unrevoked import token and asserts 403 each time;
* ``test_no_route_in_the_app_is_reachable_except_the_import_endpoint``
  enumerates the LIVE route table, so a route added in a future phase is
  covered the day it ships rather than the day someone audits;
* ``test_a_revoked_import_token_is_refused_everywhere_including_import``
  closes the other half — revocation must kill it on its own endpoint too.

Everything else is the import contract: dedupe on re-import, a blocked member
refused, an oversized payload rejected rather than swallowed.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.services import token_capabilities as tc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TEAM = "team-a"
OTHER_TEAM = "team-b"


# ── fixtures ────────────────────────────────────────────────────────────────


def _chatgpt_export(conv_id: str = "conv-import-1") -> str:
    def node(nid, parent, children, role=None, text=None, ts=None):
        n = {"id": nid, "parent": parent, "children": list(children), "message": None}
        if role:
            n["message"] = {
                "id": nid,
                "author": {"role": role, "metadata": {}},
                "create_time": ts,
                "content": {"content_type": "text", "parts": [text]},
                "recipient": "all",
                "metadata": {},
            }
        return n

    nodes = [
        node("root", None, ["u1"]),
        node("u1", "root", ["a1"], "user",
             "Which VM size are we running the xbrain stack on?", 1_754_000_000.0),
        node("a1", "u1", [], "assistant",
             "An e2-standard-2 on GCP: 2 vCPU and 8 GB of RAM.", 1_754_000_010.0),
    ]
    return json.dumps([{
        "title": "Infra sizing",
        "create_time": 1_754_000_000.0,
        "mapping": {n["id"]: n for n in nodes},
        "current_node": "a1",
        "conversation_id": conv_id,
        "id": conv_id,
    }])


def _claude_code_session(session_id: str = "sess-import-1") -> str:
    records = [
        {"type": "summary", "summary": "Import wiring"},
        {
            "type": "user", "sessionId": session_id, "uuid": "u1",
            "timestamp": "2026-08-01T09:00:00.000Z",
            "message": {"role": "user", "content": "Where is the dedupe ledger stored?"},
        },
        {
            "type": "assistant", "sessionId": session_id, "uuid": "u2",
            "timestamp": "2026-08-01T09:00:05.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "In the transcript_imports table, keyed per team."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ]},
        },
    ]
    return "\n".join(json.dumps(r) for r in records) + '\n{"type":"user","sessi'


async def _mint(session, *, user_id, team_scope: str, capability: str | None) -> str:
    """Insert a token row directly and return the plaintext, as the routes do."""
    prefix = tc.TOKEN_PREFIX[capability] if capability else "xbt_"
    raw = prefix + secrets.token_urlsafe(32)
    await session.execute(sa.text("""
        INSERT INTO user_api_tokens (user_id, token_hash, team_scope, name, capability)
        VALUES (:uid, :hash, :ts, :name, :cap)
    """), {
        "uid": str(user_id),
        "hash": hashlib.sha256(raw.encode()).hexdigest(),
        "ts": team_scope,
        "name": f"test-{capability or 'full'}",
        "cap": capability,
    })
    await session.flush()
    return raw


@pytest_asyncio.fixture
async def imports(client: httpx.AsyncClient, session, seeded_two_teams) -> dict[str, Any]:
    """Alice (member of team-a), with an import token and a full token."""
    alice = seeded_two_teams["alice"]
    import_token = await _mint(session, user_id=alice.id, team_scope=TEAM, capability=tc.IMPORT)
    full_token = await _mint(session, user_id=alice.id, team_scope=TEAM, capability=None)
    await session.flush()
    return {
        "client": client,
        "session": session,
        "alice": alice,
        "bob": seeded_two_teams["bob"],
        "team_a": seeded_two_teams["team_a"],
        "team_b": seeded_two_teams["team_b"],
        "import_token": import_token,
        "full_token": full_token,
    }


def _headers(token: str, team: str = TEAM) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Team-Scope": team}


# ── the import itself ───────────────────────────────────────────────────────


async def test_import_accepts_a_chatgpt_export(imports):
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()) as fan:
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "application/json"},
            content=_chatgpt_export(),
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["format"] == "chatgpt"
    assert body["truth_level"] == "WORKING"
    assert body["totals"] == {
        "conversations": 1, "imported": 1, "duplicates": 0,
        "over_limit": 0, "turns": 2, "queued": 2,
    }
    assert body["conversations"][0]["dedupe_key"] == "chatgpt:conv-import-1"
    assert fan.call_count == 1


async def test_import_accepts_a_claude_code_session_as_raw_text(imports):
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=_claude_code_session(),
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["format"] == "claude-code"
    assert body["conversations"][0]["dedupe_key"] == "claude-code:sess-import-1"
    # The tool_use block and the truncated tail are not turns.
    assert body["totals"]["turns"] == 2


async def test_import_accepts_a_json_envelope(imports):
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers=_headers(imports["full_token"]),
            json={"format": "chatgpt", "content": _chatgpt_export("conv-envelope")},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["conversations"][0]["dedupe_key"] == "chatgpt:conv-envelope"


# ── dedupe ──────────────────────────────────────────────────────────────────


async def test_re_importing_the_same_conversation_is_a_no_op_that_says_so(imports):
    payload = _chatgpt_export("conv-dedupe")
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()) as fan:
        first = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=payload,
        )
        second = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=payload,
        )
    assert first.json()["totals"]["imported"] == 1
    body = second.json()
    assert second.status_code == 202
    assert body["status"] == "duplicate"
    assert body["totals"] == {
        "conversations": 1, "imported": 0, "duplicates": 1,
        "over_limit": 0, "turns": 2, "queued": 0,
    }
    assert body["conversations"][0]["status"] == "duplicate"
    # Nothing was handed to the brain the second time.
    assert fan.call_count == 1


async def test_a_re_export_that_grew_still_dedupes_on_the_conversation_id(imports):
    """Same conversation, one more turn. Still one identity, still no second copy."""
    base = json.loads(_chatgpt_export("conv-grown"))
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=json.dumps(base),
        )
        base[0]["mapping"]["a1"]["children"] = ["u2"]
        base[0]["mapping"]["u2"] = {
            "id": "u2", "parent": "a1", "children": [],
            "message": {"author": {"role": "user"}, "recipient": "all",
                        "content": {"content_type": "text", "parts": ["And the disk?"]},
                        "create_time": 1_754_000_020.0, "metadata": {}},
        }
        base[0]["current_node"] = "u2"
        again = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=json.dumps(base),
        )
    assert again.json()["totals"]["duplicates"] == 1


async def test_force_re_runs_an_already_imported_conversation(imports):
    payload = _chatgpt_export("conv-forced")
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()) as fan:
        await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=payload,
        )
        forced = await imports["client"].post(
            "/v1/import/transcript",
            headers=_headers(imports["full_token"]),
            json={"format": "chatgpt", "content": payload, "force": True},
        )
    assert forced.json()["totals"]["imported"] == 1
    assert fan.call_count == 2


async def test_the_same_conversation_can_go_into_two_different_teams(imports):
    """Team isolation cuts both ways: one copy per team, and two teams are two copies."""
    session = imports["session"]
    from app.repos import teams as teams_repo

    await teams_repo.add_member(
        session, team_id=imports["team_b"].id, user_id=imports["alice"].id, role="member"
    )
    await session.flush()

    payload = _chatgpt_export("conv-two-teams")
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        a = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"], TEAM), "Content-Type": "text/plain"},
            content=payload,
        )
        b = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"], OTHER_TEAM), "Content-Type": "text/plain"},
            content=payload,
        )
    # The full token was minted for team-a only, so team-b must be refused
    # on the token scope — mint a multi-team one to prove the ledger is
    # per-team rather than global.
    assert a.json()["totals"]["imported"] == 1
    assert b.status_code == 403

    multi = await _mint(session, user_id=imports["alice"].id, team_scope="", capability=None)
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        b2 = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(multi, OTHER_TEAM), "Content-Type": "text/plain"},
            content=payload,
        )
    assert b2.status_code == 202, b2.text
    assert b2.json()["totals"]["imported"] == 1


# ── membership ──────────────────────────────────────────────────────────────


async def test_a_non_member_cannot_import_into_a_team(imports):
    session = imports["session"]
    bob_token = await _mint(session, user_id=imports["bob"].id, team_scope="", capability=None)
    await session.flush()
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(bob_token, TEAM), "Content-Type": "text/plain"},
        content=_chatgpt_export(),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not a member of this team"


async def test_a_blocked_member_cannot_import(imports):
    """blocked_at refuses. This project has shipped a bypass at this seam once."""
    session = imports["session"]
    from app.repos import teams as teams_repo

    await teams_repo.block_member(
        session,
        team_id=imports["team_a"].id,
        user_id=imports["alice"].id,
        blocked_by=imports["alice"].id,
    )
    await session.flush()

    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
        content=_chatgpt_export("conv-blocked"),
    )
    assert resp.status_code == 403
    # Same message as the non-member case — no blocked-vs-absent oracle.
    assert resp.json()["detail"] == "not a member of this team"


async def test_a_blocked_member_cannot_import_with_a_scoped_import_token(imports):
    """The xbt_-branch gap in get_team_scope must not reopen through this endpoint."""
    session = imports["session"]
    from app.repos import teams as teams_repo

    await teams_repo.block_member(
        session,
        team_id=imports["team_a"].id,
        user_id=imports["alice"].id,
        blocked_by=imports["alice"].id,
    )
    await session.flush()

    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["import_token"]), "Content-Type": "text/plain"},
        content=_chatgpt_export("conv-blocked-scoped"),
    )
    assert resp.status_code == 403


async def test_an_import_token_cannot_write_into_another_team(imports):
    session = imports["session"]
    from app.repos import teams as teams_repo

    await teams_repo.add_member(
        session, team_id=imports["team_b"].id, user_id=imports["alice"].id, role="member"
    )
    await session.flush()
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["import_token"], OTHER_TEAM), "Content-Type": "text/plain"},
        content=_chatgpt_export(),
    )
    assert resp.status_code == 403


# ── size and shape ──────────────────────────────────────────────────────────


async def test_an_oversized_payload_is_refused_with_an_actionable_message(imports):
    from app.config import settings

    oversized = b"x" * (settings.TRANSCRIPT_IMPORT_MAX_BYTES + 1)
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
        content=oversized,
    )
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert "25 MB import limit" in detail
    assert "one conversation at a time" in detail


async def test_a_lying_content_length_does_not_get_past_the_cap(imports):
    """The declared length is a hint; the stream is the enforcement."""
    from app.config import settings

    oversized = b"y" * (settings.TRANSCRIPT_IMPORT_MAX_BYTES + 2048)

    async def _stream():
        step = 1 << 20
        for offset in range(0, len(oversized), step):
            yield oversized[offset:offset + step]

    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
        content=_stream(),
    )
    assert resp.status_code == 413


async def test_the_turn_budget_truncates_instead_of_fanning_out_forever(imports, monkeypatch):
    """A crafted file must not turn one request into an unbounded fan-out."""
    from app.config import settings

    monkeypatch.setattr(settings, "TRANSCRIPT_IMPORT_MAX_TURNS", 1)
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=_chatgpt_export("conv-budget"),
        )
    body = resp.json()
    assert body["totals"]["turns"] == 2
    assert body["totals"]["queued"] == 1


async def test_a_second_conversation_past_the_budget_is_reported_over_limit(imports, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "TRANSCRIPT_IMPORT_MAX_TURNS", 2)
    both = json.loads(_chatgpt_export("conv-first")) + json.loads(_chatgpt_export("conv-second"))
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=json.dumps(both),
        )
    totals = resp.json()["totals"]
    assert totals["imported"] == 1
    assert totals["over_limit"] == 1


async def test_an_empty_body_is_refused(imports):
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
        content=b"   ",
    )
    assert resp.status_code == 400
    assert "nothing to import" in resp.json()["detail"]


@pytest.mark.parametrize(
    "url",
    [
        "https://chatgpt.com/share/abc-123",
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/",
        "https://internal.corp.example/admin",
    ],
)
async def test_a_bare_url_is_explained_never_fetched(imports, url):
    """Sharing from the ChatGPT app hands over a URL, so this shape is routine.

    The server must say what to do instead — and must not turn an
    authenticated endpoint into an outbound fetcher pointed at the GCP
    metadata address.
    """
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()) as fan:
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
            content=url.encode(),
        )
    assert resp.status_code == 400
    assert "never fetches a URL" in resp.json()["detail"]
    assert fan.call_count == 0


async def test_the_import_route_contains_no_outbound_http_client():
    """Grep-level guard: the moment someone adds httpx here, this fails.

    Mirrors the source-scan pattern tests/test_push_endpoint_safety.py uses for
    the VAPID private key — the cheapest way to keep a documented ban true.
    """
    import inspect

    from app.routes import transcript_import as module

    source = inspect.getsource(module)
    for banned in ("httpx", "aiohttp", "requests.get", "urlopen"):
        assert banned not in source, (
            f"{banned} appears in the import route — fetching a caller-supplied "
            f"URL from this VM reaches the GCP metadata server"
        )


async def test_an_unreadable_file_is_a_400_with_a_message_a_person_can_act_on(imports):
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
        content=b"This is my grocery list, not a transcript.",
    )
    assert resp.status_code == 400
    assert "conversations.json" in resp.json()["detail"]


async def test_a_declared_format_that_does_not_match_the_file_is_a_400(imports):
    resp = await imports["client"].post(
        "/v1/import/transcript?format=chatgpt",
        headers={**_headers(imports["full_token"]), "Content-Type": "text/plain"},
        content=_claude_code_session("sess-mismatch"),
    )
    assert resp.status_code == 400


# ── the scoped token: where it works, and where it must not ─────────────────


async def test_an_import_token_reaches_the_import_endpoint(imports):
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(imports["import_token"]), "Content-Type": "text/plain"},
            content=_chatgpt_export("conv-scoped-ok"),
        )
    assert resp.status_code == 202, resp.text


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/v1/me", {}),
        ("GET", "/v1/teams/my-teams", {}),
        ("GET", "/v1/memory/search?q=deploy", {}),
        ("POST", "/v1/brain/ingest", {"json": {"content": "a fact worth storing here", "source": "x"}}),
        ("GET", "/v1/me/api-token", {}),
        ("GET", "/v1/me/import-tokens", {}),
        ("POST", "/v1/me/api-token", {"json": {"team_scope": TEAM, "name": "escalation"}}),
    ],
)
async def test_import_token_is_refused_at_unrelated_endpoints(imports, method, path, kwargs):
    """403, not 200-with-nothing. The token authenticates; it is simply not allowed here."""
    resp = await imports["client"].request(
        method, path, headers=_headers(imports["import_token"]), **kwargs
    )
    assert resp.status_code == 403, f"{method} {path} → {resp.status_code}: {resp.text}"
    assert "restricted to import" in resp.json()["detail"]


async def test_the_same_endpoints_work_with_an_unrestricted_token(imports):
    """Proves the 403s above are the capability gate, not a broken fixture.

    /v1/me/api-token is deliberately absent: it is session-only by its own
    rule (``_require_user`` in routes/me.py rejects a token principal), so it
    could not be a positive control. Its 403 above is still the capability
    gate — get_current_principal runs before any route body, and the detail
    string asserted there is the capability message, not that rule's.
    """
    for path in ("/v1/me", "/v1/teams/my-teams", "/v1/me/import-tokens", "/v1/memory/search?q=deploy"):
        resp = await imports["client"].get(path, headers=_headers(imports["full_token"]))
        assert resp.status_code == 200, f"{path} → {resp.status_code}: {resp.text}"


async def test_no_route_in_the_app_is_reachable_except_the_import_endpoint():
    """Enumerate the LIVE route table — a route added later is covered on day one."""
    from app.main import create_app

    paths = {r.path for r in create_app("saas").routes}
    assert tc.IMPORT_TRANSCRIPT_PATH in paths
    reachable = {p for p in paths if tc.is_path_allowed(tc.IMPORT, p)}
    assert reachable == {tc.IMPORT_TRANSCRIPT_PATH}, (
        f"a scoped import token can reach {sorted(reachable)}"
    )


async def test_a_revoked_import_token_is_refused_everywhere_including_import(imports):
    session = imports["session"]
    raw = imports["import_token"]
    await session.execute(sa.text(
        "UPDATE user_api_tokens SET revoked_at = now() WHERE token_hash = :h"
    ), {"h": hashlib.sha256(raw.encode()).hexdigest()})
    await session.flush()

    on_import = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(raw), "Content-Type": "text/plain"},
        content=_chatgpt_export("conv-revoked"),
    )
    assert on_import.status_code == 401
    assert "revoked" in on_import.json()["detail"].lower()

    for path in ("/v1/me", "/v1/teams/my-teams", "/v1/me/import-tokens"):
        resp = await imports["client"].get(path, headers=_headers(raw))
        assert resp.status_code == 401, f"{path} → {resp.status_code}"


async def test_an_unknown_token_is_refused(imports):
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers("xbi_" + secrets.token_urlsafe(32)), "Content-Type": "text/plain"},
        content=_chatgpt_export(),
    )
    assert resp.status_code == 401


# ── token CRUD ──────────────────────────────────────────────────────────────


async def test_mint_returns_the_plaintext_once_and_never_again(imports):
    created = await imports["client"].post(
        "/v1/me/import-tokens",
        headers=_headers(imports["full_token"]),
        json={"team_scope": TEAM, "name": "iPhone"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("xbi_")
    assert body["capability"] == "import"

    listed = await imports["client"].get(
        "/v1/me/import-tokens", headers=_headers(imports["full_token"])
    )
    assert listed.status_code == 200
    entries = listed.json()
    assert any(e["id"] == body["id"] for e in entries)
    assert all("token" not in e for e in entries)
    assert body["token"] not in listed.text


async def test_a_minted_token_actually_works_on_the_import_endpoint(imports):
    created = await imports["client"].post(
        "/v1/me/import-tokens",
        headers=_headers(imports["full_token"]),
        json={"team_scope": TEAM, "name": "iPhone"},
    )
    token = created.json()["token"]
    with patch("app.routes.transcript_import.import_ingest.fan_out", new=AsyncMock()):
        resp = await imports["client"].post(
            "/v1/import/transcript",
            headers={**_headers(token), "Content-Type": "text/plain"},
            content=_chatgpt_export("conv-minted"),
        )
    assert resp.status_code == 202, resp.text


async def test_cannot_mint_an_import_token_for_a_team_you_are_not_in(imports):
    resp = await imports["client"].post(
        "/v1/me/import-tokens",
        headers=_headers(imports["full_token"]),
        json={"team_scope": OTHER_TEAM, "name": "sneaky"},
    )
    assert resp.status_code == 403


async def test_revoking_a_token_stops_it_working(imports):
    created = await imports["client"].post(
        "/v1/me/import-tokens",
        headers=_headers(imports["full_token"]),
        json={"team_scope": TEAM, "name": "burner"},
    )
    body = created.json()
    revoked = await imports["client"].delete(
        f"/v1/me/import-tokens/{body['id']}", headers=_headers(imports["full_token"])
    )
    assert revoked.status_code == 204

    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={**_headers(body["token"]), "Content-Type": "text/plain"},
        content=_chatgpt_export("conv-after-revoke"),
    )
    assert resp.status_code == 401

    again = await imports["client"].delete(
        f"/v1/me/import-tokens/{body['id']}", headers=_headers(imports["full_token"])
    )
    assert again.status_code == 404


async def test_revoke_rejects_a_non_uuid_id_with_404_not_500(imports):
    resp = await imports["client"].delete(
        "/v1/me/import-tokens/not-a-uuid", headers=_headers(imports["full_token"])
    )
    assert resp.status_code == 404


async def test_you_cannot_revoke_someone_elses_token(imports):
    session = imports["session"]
    bob_token = await _mint(session, user_id=imports["bob"].id, team_scope="", capability=None)
    alice_import = (await session.execute(sa.text(
        "SELECT id FROM user_api_tokens WHERE user_id = :uid AND capability = :cap"
    ), {"uid": str(imports["alice"].id), "cap": tc.IMPORT})).scalar_one()
    await session.flush()

    resp = await imports["client"].delete(
        f"/v1/me/import-tokens/{alice_import}",
        headers={"Authorization": f"Bearer {bob_token}", "X-Team-Scope": OTHER_TEAM},
    )
    assert resp.status_code == 404


async def test_the_import_token_list_never_shows_full_access_tokens(imports):
    listed = await imports["client"].get(
        "/v1/me/import-tokens", headers=_headers(imports["full_token"])
    )
    assert all(e["capability"] == "import" for e in listed.json())


async def test_a_bridge_service_jwt_cannot_import(imports, bridge_jwt):
    """Import is a person's action. A service principal has no membership to check."""
    resp = await imports["client"].post(
        "/v1/import/transcript",
        headers={
            "Authorization": f"Bearer {bridge_jwt('svc', TEAM)}",
            "X-Team-Scope": TEAM,
            "Content-Type": "text/plain",
        },
        content=_chatgpt_export("conv-bridge"),
    )
    assert resp.status_code == 403

"""Phase 21 — THE definitive per-team summon gate (ALIAS-01, D-21-03).

This is the "gate lesson" made executable: a per-team alias that *should* summon
the agent proves nothing until a real message with that alias actually traverses
the REAL `team_chat` POST path against a REAL Postgres and reaches the agent
handler. A mocked detector or an in-memory assertion is worthless here.

Group 1 (`test_summon_*`), `@pytest.mark.integration` (Docker-gated):
A real POST to `/v1/teams/{id}/messages` runs the REAL
`mention_detector.effective_aliases` + `detect` resolution (team_chat.py:246-247)
against a real testcontainer Postgres. The ONLY things stubbed are the three
downstream fire-and-forget network callers:
  * `team_chat_agent.handle_claude_mention`  → replaced by a RECORDER (records
    WHICH team was summoned; never runs the real handler / never calls Anthropic).
  * `centrifugo_client.publish`              → inert async no-op.
  * `brain_ingest.ingest_team_message`       → inert async no-op.
The mention DECISION (effective_aliases + detect) is NEVER mocked — that is
precisely what this gate exists to prove (T-21-03-01).

SKIP=FAIL discipline (T-21-03-03): the `integration` marker lets CI's skip-grep capture
this file. A clean SKIP is legitimate ONLY when Docker is genuinely absent; when Docker
is present (CI) a SKIP is a FAILURE signal, not a pass — this file MUST run green.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (mirror test_admin_brain.py / test_agent_aliases_api.py) ──


def _install_principal(user, *, kind: str = "user") -> None:
    import types

    from app.deps import get_current_principal
    from app.main import app

    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=getattr(user, "source_user_id", None),
        email=getattr(user, "email", None),
        display_name=getattr(user, "display_name", None),
        github_username=getattr(user, "github_username", None),
        github_id=getattr(user, "github_id", None),
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": fake_user.source_user_id,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


# ── Group 1: the real POST → detect → enqueue summon gate ─────────────────────


async def test_summon_per_team_gate(client, seeded_two_teams, session, monkeypatch):
    """Real Postgres, real detection: custom alias summons ITS team only; @agent
    summons every team; @claude summons no team.

    Deterministic signal: whether `team_chat_agent.handle_claude_mention` is
    scheduled (recorded). The three fire-and-forget network callers are stubbed;
    the mention DECISION is executed for real against the DB-resolved alias list.
    """
    from app.repos import teams as teams_repo

    alice = seeded_two_teams["alice"]  # admin+member of team-a
    bob = seeded_two_teams["bob"]      # admin+member of team-b
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    # RECORDER replaces the real agent handler — records the summoned team_id and
    # returns immediately (no Anthropic call, no network). team_chat.py invokes it
    # via the module attribute, so patching the attribute intercepts the call.
    summoned: list[Any] = []

    async def _recorder(**kwargs):
        summoned.append(kwargs["team_id"])

    async def _noop_ingest(**kwargs):
        return None

    async def _noop_publish(**kwargs):
        return None

    monkeypatch.setattr("app.services.team_chat_agent.handle_claude_mention", _recorder)
    monkeypatch.setattr("app.services.brain_ingest.ingest_team_message", _noop_ingest)
    monkeypatch.setattr("app.services.centrifugo_client.publish", _noop_publish)

    # team-a sets a CUSTOM alias "wizard"; team-b sets NOTHING (defaults only).
    await teams_repo.set_agent_aliases(session, team_id=team_a.id, aliases_csv="wizard")
    await session.commit()

    async def _post_as(user, team_id, content) -> Any:
        """POST as `user`, then flush the loop so the fire-and-forget create_task
        (the recorder) runs before we assert. Returns the httpx response."""
        summoned.clear()
        _install_principal(user)
        try:
            r = await client.post(
                f"/v1/teams/{team_id}/messages", json={"content": content}
            )
        finally:
            _clear_principal()
        # The handler schedules the recorder via asyncio.create_task and returns
        # without awaiting it; yielding to the loop lets that task run one tick.
        for _ in range(5):
            await asyncio.sleep(0)
        return r

    # Case A (SC#1) — alice: "@wizard" in team-a → summons team-a (custom alias, own team).
    r = await _post_as(alice, team_a.id, "@wizard summarize")
    assert r.status_code == 201, r.text
    assert summoned == [team_a.id], (
        f"team-a's custom alias @wizard must summon team-a, got {summoned!r}"
    )

    # Case B (SC#1) — bob: "@wizard" in team-b → does NOT summon (team-b never set it).
    r = await _post_as(bob, team_b.id, "@wizard summarize")
    assert r.status_code == 201, r.text
    assert summoned == [], (
        f"@wizard must NOT summon team-b (it never set that alias), got {summoned!r}"
    )

    # Case C (SC#2) — "@agent" is the universal default → summons BOTH teams,
    # including team-b which has NO custom alias configured.
    r = await _post_as(alice, team_a.id, "@agent hi")
    assert r.status_code == 201, r.text
    assert summoned == [team_a.id], f"@agent must summon team-a, got {summoned!r}"

    r = await _post_as(bob, team_b.id, "@agent hi")
    assert r.status_code == 201, r.text
    assert summoned == [team_b.id], (
        f"@agent must summon team-b (no custom alias), got {summoned!r}"
    )

    # Case D (SC#2) — "@claude" is reserved and summons NO team, ever.
    r = await _post_as(alice, team_a.id, "@claude hi")
    assert r.status_code == 201, r.text
    assert summoned == [], f"@claude must NOT summon team-a, got {summoned!r}"

    r = await _post_as(bob, team_b.id, "@claude hi")
    assert r.status_code == 201, r.text
    assert summoned == [], f"@claude must NOT summon team-b, got {summoned!r}"

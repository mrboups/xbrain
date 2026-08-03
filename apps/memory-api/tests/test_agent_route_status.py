"""GET /v1/me/agent-route — the routing a person can actually see.

Nothing distinguished "your subscription is answering" from "an API key is being
billed", and those are very different facts to whoever pays. This reports the
routing that is IN EFFECT, resolved by the same code the agent runs, so the
status and the behaviour it describes cannot drift apart.

The alternative — a client deciding for itself, from whether an extension
answered a ping — eventually disagrees with the thing that routes the message,
and then the status is wrong in the one direction that destroys trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.services import team_keys

pytestmark = pytest.mark.asyncio


def _set_principal(user: Any, kind: str = "user") -> None:
    from app.deps import get_current_principal
    from app.main import app

    @dataclass
    class _U:
        id: Any
        source_user_id: str | None
        email: str | None = None
        display_name: str | None = None
        github_username: str | None = None
        github_id: int | None = None

    fake = _U(id=user.id, source_user_id=getattr(user, "source_user_id", None))

    async def _override():
        return {"kind": kind, "user": fake, "sub": fake.source_user_id}

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    team_keys._reset_cache_for_tests()
    yield
    team_keys._reset_cache_for_tests()
    _clear_principal()


def _bridge(monkeypatch, live: bool) -> None:
    async def _probe(user_sub):
        return live

    monkeypatch.setattr("app.services.team_chat_agent._user_has_live_bridge", _probe)


def _team_key(monkeypatch, value: str | None) -> None:
    async def _load(session, team_id, provider):
        return value

    monkeypatch.setattr(team_keys, "_load_team_key", _load)

    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(team_keys, "async_session_factory", lambda: _Ctx())


class TestItReportsTheRealRouting:
    async def test_a_live_bridge_reads_as_the_subscription(
        self, client, seeded_two_teams, monkeypatch
    ):
        _bridge(monkeypatch, True)
        _set_principal(seeded_two_teams["alice"])
        r = await client.get(
            "/v1/me/agent-route", params={"team_id": str(seeded_two_teams["team_a"].id)}
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"subscription_connected": True, "route": "user_promax"}

    async def test_no_bridge_with_a_team_key_reads_as_the_team_key(
        self, client, seeded_two_teams, monkeypatch
    ):
        _bridge(monkeypatch, False)
        _team_key(monkeypatch, "sk-ant-team-000")
        _set_principal(seeded_two_teams["alice"])
        r = await client.get(
            "/v1/me/agent-route", params={"team_id": str(seeded_two_teams["team_a"].id)}
        )
        assert r.json() == {"subscription_connected": False, "route": "team_key"}

    async def test_no_bridge_and_no_key_reads_as_unavailable(
        self, client, seeded_two_teams, monkeypatch
    ):
        _bridge(monkeypatch, False)
        _team_key(monkeypatch, None)
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", "")
        _set_principal(seeded_two_teams["alice"])
        r = await client.get(
            "/v1/me/agent-route", params={"team_id": str(seeded_two_teams["team_a"].id)}
        )
        assert r.json() == {"subscription_connected": False, "route": "unavailable"}

    async def test_the_answer_is_about_the_user_not_the_caller_s_device(
        self, client, seeded_two_teams, monkeypatch
    ):
        """The probe is handed a user sub and nothing else.

        No device, no origin, no extension id — a phone with no extension is
        connected whenever that person has a browser holding the socket
        somewhere, and this endpoint must be incapable of saying otherwise.
        """
        seen: dict = {}

        async def _probe(user_sub):
            seen["arg"] = user_sub
            return True

        monkeypatch.setattr("app.services.team_chat_agent._user_has_live_bridge", _probe)
        _set_principal(seeded_two_teams["alice"])
        await client.get(
            "/v1/me/agent-route", params={"team_id": str(seeded_two_teams["team_a"].id)}
        )
        assert seen["arg"] == "alice-sub"


class TestItLeaksNothing:
    async def test_the_body_carries_no_key_and_no_session_detail(
        self, client, seeded_two_teams, monkeypatch
    ):
        _bridge(monkeypatch, False)
        _team_key(monkeypatch, "sk-ant-team-SECRET-000")
        _set_principal(seeded_two_teams["alice"])
        r = await client.get(
            "/v1/me/agent-route", params={"team_id": str(seeded_two_teams["team_a"].id)}
        )
        # The exhaustive check is the key SET: no field can be added without
        # this failing, so no future addition slips through unreviewed.
        assert set(r.json()) == {"subscription_connected", "route"}
        assert "SECRET" not in r.text and "sk-ant" not in r.text
        for field in ["email", "extension_id", "last_seen", "key_enc", "api_key"]:
            assert field not in r.text

    async def test_a_non_member_is_refused(self, client, seeded_two_teams, monkeypatch):
        """The tier says whether the team has a key of its own — that belongs to
        the team, not to anyone who knows its id."""
        _bridge(monkeypatch, False)
        _set_principal(seeded_two_teams["bob"])
        r = await client.get(
            "/v1/me/agent-route", params={"team_id": str(seeded_two_teams["team_a"].id)}
        )
        assert r.status_code == 403

    async def test_an_unknown_team_is_404(self, client, seeded_two_teams, monkeypatch):
        _bridge(monkeypatch, False)
        _set_principal(seeded_two_teams["alice"])
        r = await client.get(
            "/v1/me/agent-route",
            params={"team_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert r.status_code == 404


@pytest.mark.usefixtures("_clean")
class TestItIsTheAgentsOwnResolution:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_the_endpoint_calls_the_agent_module_rather_than_re_deriving(self):
        import inspect

        from app.routes.me import agent_route

        source = inspect.getsource(agent_route)
        assert "_user_has_live_bridge" in source
        assert "resolve_fallback_key" in source
        assert "_ROUTED_VIA_BY_TIER" in source, (
            "a second routing table here would drift from the one that routes "
            "the message, and the status would be confidently wrong"
        )

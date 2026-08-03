"""The team's own key is the floor under the subscription, and it is never leaked.

The model: the subscription answers when a browser holds the bridge; a key
answers when none does. A team pays for the calls the subscription could not
take, and only for those.

Everything except the last link already existed — `team_api_keys`, its
encryption, its repo functions and its routes have been there since Phase 10 and
the agent never read any of it. These tests pin the link and the two rules that
make it safe: a refused team key does not fall through to the operator's, and no
key material can reach a log line or a rendered message.
"""
from __future__ import annotations

import inspect
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

from app.services import team_chat_agent, team_keys
from app.services.team_chat_agent import (
    AGENT_TEAM_KEY_REJECTED,
    FAILURE_CODE_CONFIGURATION,
    FAILURE_CODE_NO_ROUTE,
    FAILURE_CODE_TEAM_KEY_REJECTED,
    TeamKeyRejected,
    _as_team_key_failure,
    classify_stream_failure,
)
from app.services.team_keys import (
    TIER_NONE,
    TIER_PLATFORM,
    TIER_TEAM,
    FallbackKey,
)

TEAM_KEY = "sk-ant-team-0000000000000000"
PLATFORM_KEY = "sk-ant-platform-1111111111"


@pytest.fixture(autouse=True)
def _clean_cache():
    team_keys._reset_cache_for_tests()
    yield
    team_keys._reset_cache_for_tests()


@pytest.fixture
def fernet_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(team_keys.settings, "FERNET_KEY", key)
    return key


def _stub_team_key(monkeypatch, value: str | None):
    """Replace the DB read only. Tier ordering above it stays real."""

    async def _load(session, team_id, provider):
        return value

    async def _no_session():
        raise AssertionError("unreachable")

    monkeypatch.setattr(team_keys, "_load_team_key", _load)

    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(team_keys, "async_session_factory", lambda: _Ctx())


class TestTheTiersAreChosenInOrder:
    @pytest.mark.asyncio
    async def test_the_teams_own_key_wins(self, monkeypatch):
        _stub_team_key(monkeypatch, TEAM_KEY)
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", PLATFORM_KEY)
        got = await team_keys.resolve_fallback_key(team_id=uuid4())
        assert got.tier == TIER_TEAM
        assert got.key == TEAM_KEY

    @pytest.mark.asyncio
    async def test_the_deployment_key_is_the_next_floor(self, monkeypatch):
        _stub_team_key(monkeypatch, None)
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", PLATFORM_KEY)
        got = await team_keys.resolve_fallback_key(team_id=uuid4())
        assert got.tier == TIER_PLATFORM
        assert got.key == PLATFORM_KEY

    @pytest.mark.asyncio
    async def test_neither_is_no_key_at_all(self, monkeypatch):
        _stub_team_key(monkeypatch, None)
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", "")
        got = await team_keys.resolve_fallback_key(team_id=uuid4())
        assert got.tier == TIER_NONE
        assert got.key is None

    @pytest.mark.asyncio
    async def test_an_unreachable_database_still_lands_on_the_floor(self, monkeypatch):
        """A lookup that blows up must not take the agent turn with it."""

        def _boom():
            raise RuntimeError("connection refused")

        monkeypatch.setattr(team_keys, "async_session_factory", _boom)
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", PLATFORM_KEY)
        got = await team_keys.resolve_fallback_key(team_id=uuid4())
        assert got.tier == TIER_PLATFORM


class TestNoKeyAnywhereIsUnavailability:
    def test_it_is_not_an_error(self):
        """The tempting shape is an exception nobody wrote a sentence for."""
        failure = classify_stream_failure(team_chat_agent.AgentRouteUnavailable())
        assert failure["code"] == FAILURE_CODE_NO_ROUTE
        assert failure["retryable"] is False
        lowered = failure["message"].lower()
        assert "failed" not in lowered and "error" not in lowered

    def test_the_routing_table_maps_no_key_to_unavailable(self):
        assert team_chat_agent._ROUTED_VIA_BY_TIER[TIER_NONE] == "unavailable"
        assert team_chat_agent._ROUTED_VIA_BY_TIER[TIER_TEAM] == "team_key"
        assert team_chat_agent._ROUTED_VIA_BY_TIER[TIER_PLATFORM] == "team_api"


class TestARejectedTeamKeyDoesNotFallThrough:
    """A team that supplied its own key expects its own billing.

    Quietly spending the operator's key instead is a surprise they find in an
    invoice, so the refusal is reported and the turn stops.
    """

    def _refusal(self, status: int) -> Exception:
        request = httpx.Request("POST", "https://provider.example/v1/messages")
        return httpx.HTTPStatusError(
            "invalid x-api-key",
            request=request,
            response=httpx.Response(status, request=request),
        )

    @pytest.mark.parametrize("status", [401, 403])
    def test_the_teams_key_being_refused_says_so(self, status: int):
        team = FallbackKey(key=TEAM_KEY, tier=TIER_TEAM)
        renamed = _as_team_key_failure(self._refusal(status), team)
        assert isinstance(renamed, TeamKeyRejected)
        failure = classify_stream_failure(renamed)
        assert failure["code"] == FAILURE_CODE_TEAM_KEY_REJECTED
        assert failure["message"] == AGENT_TEAM_KEY_REJECTED
        assert failure["retryable"] is False

    @pytest.mark.parametrize("status", [401, 403])
    def test_the_deployment_key_being_refused_is_the_operators_problem(self, status: int):
        """Not the team's key, so not the team's message — and a team member
        cannot fix an operator's key by replacing one in team settings."""
        platform = FallbackKey(key=PLATFORM_KEY, tier=TIER_PLATFORM)
        same = _as_team_key_failure(self._refusal(status), platform)
        assert not isinstance(same, TeamKeyRejected)
        assert classify_stream_failure(same)["code"] == FAILURE_CODE_CONFIGURATION

    @pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
    def test_only_an_auth_refusal_is_renamed(self, status: int):
        team = FallbackKey(key=TEAM_KEY, tier=TIER_TEAM)
        assert not isinstance(_as_team_key_failure(self._refusal(status), team), TeamKeyRejected)

    def test_an_exception_with_no_status_is_left_alone(self):
        team = FallbackKey(key=TEAM_KEY, tier=TIER_TEAM)
        original = RuntimeError("socket closed")
        assert _as_team_key_failure(original, team) is original

    def test_the_agent_does_not_retry_with_the_other_key(self):
        source = inspect.getsource(team_chat_agent._do_handle)
        assert "ANTHROPIC_API_KEY" not in source, (
            "the agent is reaching for the deployment key directly — the tier "
            "resolver is the only thing that may choose one"
        )
        assert source.count("_stream_via_anthropic_api") == 1, (
            "a second provider call in the routing block is a fall-through: a "
            "refused team key must not be retried on the operator's key"
        )


class TestTheKeyNeverLeaves:
    def test_no_sentence_contains_key_material(self):
        for exc in [TeamKeyRejected(), TeamKeyRejected(TEAM_KEY)]:
            rendered = " ".join(str(v) for v in classify_stream_failure(exc).values())
            assert TEAM_KEY not in rendered
            assert "sk-ant" not in rendered

    def test_the_rejection_carries_no_free_text_field(self):
        failure = classify_stream_failure(TeamKeyRejected())
        assert set(failure) == {"code", "message", "retryable"}

    def test_the_resolver_logs_tiers_and_not_keys(self):
        # The LOG CALLS specifically — a blunt substring search over the module
        # also matches FallbackKey(key=...), which is the whole point of the file.
        import re

        source = inspect.getsource(team_keys)
        calls = re.findall(r"log\.\w+\((?:[^()]|\([^()]*\))*\)", source, re.S)
        assert calls, "the module must log something, or this test proves nothing"
        for call in calls:
            for forbidden in ["key=", "key_enc", "plaintext", "secret"]:
                assert forbidden not in call, (
                    f"{forbidden!r} in a log call would put key material in the logs: {call}"
                )
        assert 'log.warning("team_keys.decrypt_failed"' in source, (
            "a failed decrypt must be observable without the ciphertext"
        )

    def test_no_log_call_anywhere_names_the_key(self):
        """Same rule, applied to the caller that actually holds the plaintext."""
        import re

        source = inspect.getsource(team_chat_agent)
        for call in re.findall(r"log\.\w+\((?:[^()]|\([^()]*\))*\)", source, re.S):
            for forbidden in ["api_key=", "fallback.key", "key=fallback"]:
                assert forbidden not in call, f"key material in a log call: {call}"

    def test_the_streaming_helper_takes_the_key_as_an_argument(self):
        """Not read from settings inside: which key is a routing decision, and a
        helper that reached for a global would silently ignore the team's."""
        sig = inspect.signature(team_chat_agent._stream_via_anthropic_api)
        assert "api_key" in sig.parameters
        source = inspect.getsource(team_chat_agent._stream_via_anthropic_api)
        assert "settings.ANTHROPIC_API_KEY" not in source


class TestRotationBites:
    @pytest.mark.asyncio
    async def test_a_cached_key_is_reused_within_the_ttl(self, monkeypatch):
        calls = {"n": 0}

        async def _load(session, team_id, provider):
            calls["n"] += 1
            return TEAM_KEY

        monkeypatch.setattr(team_keys, "_load_team_key", _load)
        _stub_team_key(monkeypatch, TEAM_KEY)
        monkeypatch.setattr(team_keys, "_load_team_key", _load)

        team_id = uuid4()
        await team_keys.resolve_fallback_key(team_id=team_id)
        await team_keys.resolve_fallback_key(team_id=team_id)
        assert calls["n"] == 1, "the second turn in a burst must not re-read the row"

    @pytest.mark.asyncio
    async def test_invalidate_makes_the_next_turn_re_read(self, monkeypatch):
        keys = [TEAM_KEY, "sk-ant-rotated-2222222222"]

        async def _load(session, team_id, provider):
            return keys.pop(0) if keys else None

        _stub_team_key(monkeypatch, None)
        monkeypatch.setattr(team_keys, "_load_team_key", _load)

        team_id = uuid4()
        first = await team_keys.resolve_fallback_key(team_id=team_id)
        team_keys.invalidate(team_id)
        second = await team_keys.resolve_fallback_key(team_id=team_id)
        assert first.key != second.key, (
            "an operator who rotates a leaked key and finds it still working "
            "will not trust the feature again"
        )

    @pytest.mark.asyncio
    async def test_invalidating_one_team_leaves_another_alone(self, monkeypatch):
        _stub_team_key(monkeypatch, TEAM_KEY)
        a, b = uuid4(), uuid4()
        await team_keys.resolve_fallback_key(team_id=a)
        await team_keys.resolve_fallback_key(team_id=b)
        team_keys.invalidate(a)
        assert any(k[0] == str(b) for k in team_keys._CACHE)
        assert not any(k[0] == str(a) for k in team_keys._CACHE)

    def test_the_rotation_route_invalidates(self):
        from app.routes import teams as teams_routes

        source = inspect.getsource(teams_routes.upsert_api_keys)
        assert "team_keys.invalidate" in source, (
            "PUT /api-keys must drop the cached key or a rotation waits out a TTL"
        )


class TestTheLookupIsProviderAware:
    @pytest.mark.asyncio
    async def test_the_provider_reaches_the_query(self, monkeypatch, fernet_key):
        seen: dict = {}

        async def _load(session, team_id, provider):
            seen["provider"] = provider
            return None

        _stub_team_key(monkeypatch, None)
        monkeypatch.setattr(team_keys, "_load_team_key", _load)
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", "")
        await team_keys.resolve_fallback_key(team_id=uuid4(), provider="openai")
        assert seen["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_two_providers_do_not_share_a_cache_entry(self, monkeypatch):
        by_provider = {"anthropic": TEAM_KEY, "openai": "sk-openai-333"}

        async def _load(session, team_id, provider):
            return by_provider[provider]

        _stub_team_key(monkeypatch, None)
        monkeypatch.setattr(team_keys, "_load_team_key", _load)
        team_id = uuid4()
        a = await team_keys.resolve_fallback_key(team_id=team_id, provider="anthropic")
        o = await team_keys.resolve_fallback_key(team_id=team_id, provider="openai")
        assert a.key != o.key

    def test_the_query_matches_the_provider_column_case_insensitively(self):
        source = inspect.getsource(team_keys._load_team_key)
        assert "lower(provider) = lower(:provider)" in source, (
            "a key stored as 'Anthropic' would be invisible to a lookup for 'anthropic'"
        )

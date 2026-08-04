"""A team chooses which provider its agent falls back to, and that choice is honoured.

The store was already provider-aware and the agent only ever called Anthropic, so
an OpenAI or xAI key was accepted, encrypted, and never spent. These tests pin the
link and the four things that make it safe to have.

WHAT IS MOCKED, AND WHY IT MATTERS. The provider TRANSPORT is mocked — respx
intercepts the HTTP the SDKs actually make — and nothing of ours is. A test that
monkeypatched `_stream_via_openai_compatible_api` would pass with that function
deleted; one that asserts a request arrived at api.x.ai carrying the xAI key and
the configured Grok model cannot. Neither OpenAI nor xAI was exercised against a
real endpoint (no credentials, and the deployment's Anthropic key has no credit
either, so a live call would prove nothing there): what is proven here is the
request we emit and the response shape we parse, against the vendors' documented
wire format.

THE FOUR RULES.

  1. Each provider is reached with ITS OWN key, model and host. Crossing them is
     billing a team for a vendor they did not choose.
  2. A selection with no key is an unavailability that NAMES the provider, and it
     never quietly answers on a different one — the same reasoning already applied
     to a refused team key, one level up.
  3. `agent_name` is the model that produced the answer, on the streamed frame AND
     on the persisted row. A team on OpenAI whose history says claude-sonnet-4-6 is
     a record that lies about itself.
  4. No provider's error text reaches a message, for any of the three transports.
     Asserted against the SHAPE with hostile payloads, not against one sample.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import types
from uuid import uuid4

import httpx
import pytest
import respx

from app.services import team_chat_agent, team_keys
from app.services.team_chat_agent import (
    AGENT_UNAVAILABLE_NO_ROUTE,
    FAILURE_CODE_CONFIGURATION,
    FAILURE_CODE_NO_ROUTE,
    FAILURE_CODE_PROVIDER_KEY_MISSING,
    FAILURE_CODE_TIMEOUT,
    FAILURE_CODE_UNAVAILABLE,
    MODEL_SONNET,
    UNAVAILABILITY_CODES,
    AgentRouteUnavailable,
    ProviderKeyMissing,
    classify_stream_failure,
)

ANTHROPIC_KEY = "sk-ant-team-aaaaaaaaaaaaaaaaaaaa"
OPENAI_KEY = "sk-openai-team-bbbbbbbbbbbbbbbb"
XAI_KEY = "xai-team-cccccccccccccccccccc"

ALL_KEYS = (ANTHROPIC_KEY, OPENAI_KEY, XAI_KEY)


@pytest.fixture(autouse=True)
def _clean_cache():
    team_keys._reset_cache_for_tests()
    yield
    team_keys._reset_cache_for_tests()


# ── Wire fixtures: what each vendor's stream actually looks like ──────────────


def _openai_sse(text: str = "Hello from the fallback.") -> bytes:
    """An OpenAI Chat Completions stream. xAI emits the same shape."""
    chunks = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _anthropic_sse(text: str = "Hello from the fallback.") -> bytes:
    """An Anthropic Messages stream, in the event/data pairs the SDK parses."""
    events = [
        ("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_test", "type": "message", "role": "assistant",
                "model": "test-model", "content": [], "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 11, "output_tokens": 1},
            },
        }),
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 7},
        }),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ).encode()


def _sse_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=body
    )


async def _drain(stream) -> tuple[str, dict]:
    text_parts: list[str] = []
    usage: dict = {}
    async for chunk_text, chunk_usage in stream:
        if chunk_text:
            text_parts.append(chunk_text)
        if chunk_usage:
            usage = chunk_usage
    return "".join(text_parts), usage


# ── 1. Each provider is reached with its own key, model and host ─────────────


class TestEachProviderReachesItsOwnEndpoint:
    @pytest.mark.asyncio
    async def test_anthropic_goes_to_anthropic_with_the_anthropic_key(self, monkeypatch):
        monkeypatch.setattr(
            team_keys.settings, "AGENT_MODEL_ANTHROPIC", "claude-test-model"
        )
        with respx.mock(assert_all_called=True) as mock:
            route = mock.post("https://api.anthropic.com/v1/messages").mock(
                return_value=_sse_response(_anthropic_sse())
            )
            text, usage = await _drain(
                team_chat_agent._stream_via_fallback_provider(
                    provider=team_keys.PROVIDER_ANTHROPIC,
                    api_key=ANTHROPIC_KEY,
                    system_prompt="sys",
                    cached_memory_block="mem",
                    chat_history_block="hi",
                )
            )
        assert text == "Hello from the fallback."
        request = route.calls[0].request
        assert request.headers.get("x-api-key") == ANTHROPIC_KEY
        assert json.loads(request.content)["model"] == "claude-test-model"
        assert usage["usage"]["output_tokens"] == 7

    @pytest.mark.asyncio
    async def test_openai_goes_to_openai_with_the_openai_key(self, monkeypatch):
        monkeypatch.setattr(team_keys.settings, "AGENT_MODEL_OPENAI", "gpt-test-model")
        monkeypatch.setattr(team_keys.settings, "AGENT_OPENAI_BASE_URL", "")
        with respx.mock(assert_all_called=True) as mock:
            route = mock.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=_sse_response(_openai_sse())
            )
            text, usage = await _drain(
                team_chat_agent._stream_via_fallback_provider(
                    provider=team_keys.PROVIDER_OPENAI,
                    api_key=OPENAI_KEY,
                    system_prompt="sys",
                    cached_memory_block="mem",
                    chat_history_block="hi",
                )
            )
        assert text == "Hello from the fallback."
        request = route.calls[0].request
        assert request.headers.get("authorization") == f"Bearer {OPENAI_KEY}"
        body = json.loads(request.content)
        assert body["model"] == "gpt-test-model"
        assert body["stream"] is True
        # The Anthropic cache_control block array is Anthropic's. Sending it here
        # renders as "[object Object]" at best.
        assert isinstance(body["messages"][0]["content"], str)
        assert usage["usage"] == {"input_tokens": 11, "output_tokens": 7}

    @pytest.mark.asyncio
    async def test_xai_is_the_same_client_pointed_at_another_host(self, monkeypatch):
        """xAI implements the OpenAI wire protocol — one integration, two hosts."""
        monkeypatch.setattr(team_keys.settings, "AGENT_MODEL_XAI", "grok-test-model")
        monkeypatch.setattr(
            team_keys.settings, "AGENT_XAI_BASE_URL", "https://api.x.ai/v1"
        )
        with respx.mock(assert_all_called=True) as mock:
            route = mock.post("https://api.x.ai/v1/chat/completions").mock(
                return_value=_sse_response(_openai_sse())
            )
            text, _ = await _drain(
                team_chat_agent._stream_via_fallback_provider(
                    provider=team_keys.PROVIDER_XAI,
                    api_key=XAI_KEY,
                    system_prompt="sys",
                    cached_memory_block="mem",
                    chat_history_block="hi",
                )
            )
        assert text == "Hello from the fallback."
        request = route.calls[0].request
        assert request.headers.get("authorization") == f"Bearer {XAI_KEY}"
        assert json.loads(request.content)["model"] == "grok-test-model"

    @pytest.mark.asyncio
    async def test_a_self_hosted_openai_compatible_host_is_reachable(self, monkeypatch):
        """The knob that keeps the OpenAI path inside an operator's own network."""
        monkeypatch.setattr(
            team_keys.settings, "AGENT_OPENAI_BASE_URL", "http://vllm.internal:8000/v1"
        )
        with respx.mock(assert_all_called=True) as mock:
            mock.post("http://vllm.internal:8000/v1/chat/completions").mock(
                return_value=_sse_response(_openai_sse())
            )
            text, _ = await _drain(
                team_chat_agent._stream_via_fallback_provider(
                    provider=team_keys.PROVIDER_OPENAI,
                    api_key=OPENAI_KEY,
                    system_prompt="sys",
                    cached_memory_block="mem",
                    chat_history_block="hi",
                )
            )
        assert text == "Hello from the fallback."

    @pytest.mark.asyncio
    async def test_no_provider_is_ever_reached_with_another_providers_key(
        self, monkeypatch
    ):
        """The whole point, stated once as a property.

        Every host is mocked at the same time, so a dispatcher that sent the xAI
        key to OpenAI would be caught by the assertion rather than by an invoice.
        """
        monkeypatch.setattr(team_keys.settings, "AGENT_OPENAI_BASE_URL", "")
        monkeypatch.setattr(
            team_keys.settings, "AGENT_XAI_BASE_URL", "https://api.x.ai/v1"
        )
        expected = {
            team_keys.PROVIDER_ANTHROPIC: (
                "https://api.anthropic.com/v1/messages", ANTHROPIC_KEY, _anthropic_sse(),
            ),
            team_keys.PROVIDER_OPENAI: (
                "https://api.openai.com/v1/chat/completions", OPENAI_KEY, _openai_sse(),
            ),
            team_keys.PROVIDER_XAI: (
                "https://api.x.ai/v1/chat/completions", XAI_KEY, _openai_sse(),
            ),
        }
        for provider, (url, key, _body) in expected.items():
            # assert_all_called is OFF on purpose: the other two hosts are mocked
            # precisely so that reaching them would be RECORDED rather than raise
            # a connection error. Not calling them is the pass condition.
            with respx.mock(assert_all_called=False) as mock:
                routes = {
                    other_url: mock.post(other_url).mock(
                        return_value=_sse_response(other_body)
                    )
                    for _p, (other_url, _k, other_body) in expected.items()
                }
                await _drain(
                    team_chat_agent._stream_via_fallback_provider(
                        provider=provider,
                        api_key=key,
                        system_prompt="sys",
                        cached_memory_block="mem",
                        chat_history_block="hi",
                    )
                )
                # Exactly one host was contacted, and it was this provider's.
                called = [u for u, r in routes.items() if r.calls]
                assert called == [url], f"{provider} contacted {called}"
                sent = routes[url].calls[0].request
                raw = sent.content.decode() + str(dict(sent.headers))
                for foreign in [k for k in ALL_KEYS if k != key]:
                    assert foreign not in raw, (
                        f"{provider} was handed another provider's key"
                    )

    def test_the_dispatcher_is_the_only_thing_that_picks_a_provider(self):
        """Two provider calls in the routing block is a fall-through waiting to
        be written — the same defect a refused team key was fixed for."""
        source = inspect.getsource(team_chat_agent._do_handle)
        assert source.count("_stream_via_fallback_provider") == 1
        assert "AsyncOpenAI" not in source and "AsyncAnthropic" not in source

    def test_the_catch_up_summary_uses_the_same_dispatcher(self):
        """A summary that answered on a different vendor than every other agent
        turn would bill a team for a provider they did not choose, on the one
        path that persists nothing to explain it afterwards."""
        source = inspect.getsource(team_chat_agent.catch_me_up)
        # Count the CALL, not the mentions: the docstring names the dispatcher too.
        assert source.count("_stream_via_fallback_provider(") == 1
        assert "_stream_via_anthropic_api(" not in source


# ── 2. A selection with no key is an absence, and never a quiet switch ───────


class TestASelectionWithNoKeyNeverSwitches:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", [team_keys.PROVIDER_OPENAI, team_keys.PROVIDER_XAI])
    async def test_the_other_providers_key_is_not_reached_for(
        self, monkeypatch, provider: str
    ):
        """The resolver must not hand back Anthropic's key to an OpenAI turn.

        This is the load-bearing one. Every other guarantee in this file assumes
        the key that arrives belongs to the provider that was asked for.
        """

        async def _no_team_key(session, team_id, wanted):
            return None

        class _Ctx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(team_keys, "_load_team_key", _no_team_key)
        monkeypatch.setattr(team_keys, "async_session_factory", lambda: _Ctx())
        monkeypatch.setattr(team_keys.settings, "ANTHROPIC_API_KEY", ANTHROPIC_KEY)
        monkeypatch.setattr(team_keys.settings, "OPENAI_API_KEY", "")
        monkeypatch.setattr(team_keys.settings, "XAI_API_KEY", "")

        resolved = await team_keys.resolve_fallback_key(
            team_id=uuid4(), provider=provider
        )
        assert resolved.key is None, (
            "a team that chose one vendor was handed another vendor's key — they "
            "would find that out in an invoice"
        )
        assert resolved.tier == team_keys.TIER_NONE

    @pytest.mark.parametrize(
        "provider,expected_label",
        [(team_keys.PROVIDER_OPENAI, "OpenAI"), (team_keys.PROVIDER_XAI, "Grok")],
    )
    def test_the_message_names_the_provider_that_was_selected(
        self, provider: str, expected_label: str
    ):
        failure = classify_stream_failure(ProviderKeyMissing(provider))
        assert failure["code"] == FAILURE_CODE_PROVIDER_KEY_MISSING
        assert expected_label in failure["message"]
        assert failure["retryable"] is False, "a key that is absent stays absent"

    def test_it_reads_as_an_absence_and_not_as_a_malfunction(self):
        message = classify_stream_failure(
            ProviderKeyMissing(team_keys.PROVIDER_OPENAI)
        )["message"].lower()
        for verb in ["failed", "error", "went wrong", "could not answer"]:
            assert verb not in message, (
                f"{verb!r} — nothing was attempted, so nothing failed"
            )
        assert "team settings" in message, "an absence with no remedy is just bad news"
        assert "extension" in message, "the free route is still worth naming"

    def test_it_says_nothing_about_this_device(self):
        """The bridge is keyed by USER. A phone routes through whatever browser
        that person has open somewhere."""
        message = classify_stream_failure(
            ProviderKeyMissing(team_keys.PROVIDER_XAI)
        )["message"].lower()
        for word in ["this device", "phone", "mobile", "desktop", "laptop"]:
            assert word not in message

    def test_the_client_can_style_it_as_unavailability(self):
        assert FAILURE_CODE_PROVIDER_KEY_MISSING in UNAVAILABILITY_CODES
        assert FAILURE_CODE_PROVIDER_KEY_MISSING not in {
            FAILURE_CODE_TIMEOUT, FAILURE_CODE_UNAVAILABLE, FAILURE_CODE_CONFIGURATION
        }

    def test_an_unnameable_provider_degrades_instead_of_rendering_it(self):
        """The format slot must be unreachable from anything unvalidated.

        A row that somehow held a provider this build does not implement must not
        become a way to render that string to a whole team.
        """
        for junk in ["", "gemini", "<script>alert(1)</script>", "{leak}"]:
            failure = classify_stream_failure(ProviderKeyMissing(junk))
            assert failure["code"] == FAILURE_CODE_NO_ROUTE
            assert failure["message"] == AGENT_UNAVAILABLE_NO_ROUTE
            assert junk not in failure["message"] or junk == ""

    def test_the_default_provider_keeps_the_sentence_it_always_had(self):
        """A team that never chose is not told about a choice it did not make."""
        assert isinstance(
            team_chat_agent._no_route_for(team_keys.DEFAULT_PROVIDER),
            AgentRouteUnavailable,
        )
        for chosen in (team_keys.PROVIDER_OPENAI, team_keys.PROVIDER_XAI):
            assert isinstance(
                team_chat_agent._no_route_for(chosen), ProviderKeyMissing
            )

    def test_the_exception_itself_carries_no_renderable_text(self):
        """Even if a future caller forgets and stringifies it."""
        assert str(ProviderKeyMissing("openai")) == ""


# ── 3. No provider's error text reaches a message, on any transport ──────────


HOSTILE = [
    "Error code: 400 - {'error': {'message': 'Your credit balance is too low to "
    "access the Anthropic API.'}, 'request_id': 'req_011abc'}",
    "Incorrect API key provided: sk-ant-team-aaaaaaaaaaaaaaaaaaaa. You can find "
    "your API key at https://platform.openai.com/account/api-keys",
    "xai: 403 Forbidden — team quota exhausted for grok-3",
    "<html><body>502 Bad Gateway (nginx/1.25.3)</body></html>",
    "",
]


def _every_transports_exceptions(text: str) -> list[BaseException]:
    """One exception of each meaningful kind from each of the three transports."""
    import anthropic
    import openai

    request = httpx.Request("POST", "https://provider.example/v1/messages")
    leaky_response = httpx.Response(401, request=request, text=text)
    server_response = httpx.Response(500, request=request, text=text)
    return [
        # bridge (httpx)
        httpx.HTTPStatusError(text, request=request, response=leaky_response),
        httpx.ConnectError(text, request=request),
        httpx.ReadTimeout(text, request=request),
        RuntimeError(text),
        # anthropic
        anthropic.APIStatusError(text, response=leaky_response, body={"raw": text}),
        anthropic.APIStatusError(text, response=server_response, body={"raw": text}),
        anthropic.APIConnectionError(request=request),
        anthropic.APITimeoutError(request=request),
        # openai / xai (same client, so the same exception types)
        openai.APIStatusError(text, response=leaky_response, body={"raw": text}),
        openai.APIStatusError(text, response=server_response, body={"raw": text}),
        openai.APIConnectionError(request=request),
        openai.APITimeoutError(request=request),
    ]


class TestNoProvidersWordsReachATeam:
    @pytest.mark.parametrize("text", HOSTILE)
    def test_the_payload_has_no_free_text_field_for_any_transport(self, text: str):
        for exc in _every_transports_exceptions(text):
            failure = classify_stream_failure(exc)
            assert set(failure) == {"code", "message", "retryable"}, (
                f"{type(exc).__name__} produced an extra field a raw string "
                f"could be dropped into: {failure}"
            )
            assert isinstance(failure["retryable"], bool)

    @pytest.mark.parametrize("text", HOSTILE)
    def test_no_fragment_of_the_hostile_text_survives(self, text: str):
        fragments = [
            "credit balance", "request_id", "req_", "sk-ant", "sk-openai",
            "platform.openai.com", "grok-3", "quota", "nginx", "502", "401",
            "{", "}", "<html", "<script",
        ]
        for exc in _every_transports_exceptions(text):
            rendered = " ".join(str(v) for v in classify_stream_failure(exc).values())
            lowered = rendered.lower()
            for fragment in fragments:
                assert fragment not in lowered, (
                    f"{fragment!r} leaked from {type(exc).__name__}: {rendered}"
                )

    def test_every_transports_timeout_is_recognised_as_one(self):
        """openai.APITimeoutError is neither an asyncio nor an httpx timeout.

        Unmapped it would fall through to the catch-all — safe, but it would tell
        a team "worth trying again" without saying the attempt was stopped for
        taking too long, which is what makes the advice make sense.
        """
        import anthropic
        import openai

        request = httpx.Request("POST", "https://provider.example/v1/messages")
        for exc in [
            asyncio.TimeoutError(),
            httpx.ReadTimeout("slow", request=request),
            anthropic.APITimeoutError(request=request),
            openai.APITimeoutError(request=request),
        ]:
            assert classify_stream_failure(exc)["code"] == FAILURE_CODE_TIMEOUT, (
                f"{type(exc).__name__} was not recognised as a timeout"
            )

    def test_an_auth_refusal_on_any_transport_is_the_configuration_verdict(self):
        import openai

        request = httpx.Request("POST", "https://provider.example/v1/messages")
        exc = openai.APIStatusError(
            "Incorrect API key provided: sk-openai-team-bbbbbbbbbbbbbbbb",
            response=httpx.Response(401, request=request, text="leak"),
            body=None,
        )
        failure = classify_stream_failure(exc)
        assert failure["code"] == FAILURE_CODE_CONFIGURATION
        assert failure["retryable"] is False
        assert OPENAI_KEY not in failure["message"]

    def test_a_teams_own_key_being_refused_is_named_on_every_transport(self):
        """_as_team_key_failure reads the STATUS class, never the body — and the
        OpenAI SDK's status errors must be legible to it too."""
        import anthropic
        import openai

        from app.services.team_chat_agent import TeamKeyRejected, _as_team_key_failure
        from app.services.team_keys import TIER_TEAM, FallbackKey

        request = httpx.Request("POST", "https://provider.example/v1/messages")
        team = FallbackKey(key=OPENAI_KEY, tier=TIER_TEAM)
        for status in (401, 403):
            response = httpx.Response(status, request=request, text="secret body")
            for exc in [
                httpx.HTTPStatusError("x", request=request, response=response),
                anthropic.APIStatusError("x", response=response, body=None),
                openai.APIStatusError("x", response=response, body=None),
            ]:
                renamed = _as_team_key_failure(exc, team)
                assert isinstance(renamed, TeamKeyRejected), type(exc).__name__

    def test_no_log_call_in_the_new_streaming_path_names_the_key(self):
        import re

        source = inspect.getsource(team_chat_agent._stream_via_openai_compatible_api)
        for call in re.findall(r"log\.\w+\((?:[^()]|\([^()]*\))*\)", source, re.S):
            for forbidden in ["api_key", "key=", "fallback.key"]:
                assert forbidden not in call, f"key material in a log call: {call}"

    def test_the_new_streaming_helper_takes_its_key_as_an_argument(self):
        """Not read from settings inside: which key is a routing decision."""
        sig = inspect.signature(team_chat_agent._stream_via_openai_compatible_api)
        assert "api_key" in sig.parameters
        source = inspect.getsource(team_chat_agent._stream_via_openai_compatible_api)
        for global_key in ["settings.OPENAI_API_KEY", "settings.XAI_API_KEY"]:
            assert global_key not in source


# ── 4. Switching takes effect without waiting out the cache ─────────────────


class TestSwitchingBitesImmediately:
    @pytest.mark.asyncio
    async def test_the_selection_is_read_per_turn_and_not_cached_at_all(self):
        """The cache holds KEYS, keyed by (team, provider). The selection is read
        off the team row every turn, so a switch cannot be served stale."""
        team = types.SimpleNamespace(agent_provider="anthropic")
        assert team_chat_agent._selected_provider(team) == "anthropic"
        team.agent_provider = "xai"
        assert team_chat_agent._selected_provider(team) == "xai", (
            "the provider was captured somewhere instead of read from the row"
        )

    @pytest.mark.asyncio
    async def test_a_switch_does_not_serve_the_previous_providers_key(
        self, monkeypatch
    ):
        """Two providers never share a cache entry, so the first turn after a
        switch resolves the NEW provider rather than replaying the old one."""
        by_provider = {"anthropic": ANTHROPIC_KEY, "openai": OPENAI_KEY}

        async def _load(session, team_id, provider):
            return by_provider.get(provider)

        class _Ctx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(team_keys, "_load_team_key", _load)
        monkeypatch.setattr(team_keys, "async_session_factory", lambda: _Ctx())

        team_id = uuid4()
        before = await team_keys.resolve_fallback_key(
            team_id=team_id, provider="anthropic"
        )
        after = await team_keys.resolve_fallback_key(
            team_id=team_id, provider="openai"
        )
        assert before.key == ANTHROPIC_KEY
        assert after.key == OPENAI_KEY, (
            "the switch was served the previous provider's cached key"
        )

    def test_the_switch_route_drops_the_cached_keys(self):
        from app.routes import teams as teams_routes

        source = inspect.getsource(teams_routes.set_agent_provider_route)
        assert "team_keys.invalidate" in source

    def test_a_stale_row_value_resolves_to_the_default_rather_than_crashing(self):
        """Third line of defence, on the path that spends money."""
        for junk in [None, "", "gemini", "  ANTHROPIC  "]:
            resolved = team_chat_agent._selected_provider(
                types.SimpleNamespace(agent_provider=junk)
            )
            assert resolved in team_keys.SUPPORTED_PROVIDERS


# ── 5. The name is the model that answered ──────────────────────────────────


class TestTheNameIsTheModelThatAnswered:
    def test_each_provider_names_its_own_model(self, monkeypatch):
        monkeypatch.setattr(team_keys.settings, "AGENT_MODEL_ANTHROPIC", "claude-x")
        monkeypatch.setattr(team_keys.settings, "AGENT_MODEL_OPENAI", "gpt-x")
        monkeypatch.setattr(team_keys.settings, "AGENT_MODEL_XAI", "grok-x")
        assert team_chat_agent._answering_model(
            provider="anthropic", has_promax=False
        ) == "claude-x"
        assert team_chat_agent._answering_model(
            provider="openai", has_promax=False
        ) == "gpt-x"
        assert team_chat_agent._answering_model(
            provider="xai", has_promax=False
        ) == "grok-x"

    def test_the_subscription_path_names_what_the_bridge_was_asked_for(
        self, monkeypatch
    ):
        """A live bridge answers on the person's Claude session whatever the team
        selected for its FALLBACK — so naming the fallback's model there would be
        the same lie in the other direction."""
        monkeypatch.setattr(team_keys.settings, "AGENT_MODEL_OPENAI", "gpt-x")
        assert team_chat_agent._answering_model(
            provider="openai", has_promax=True
        ) == MODEL_SONNET
        body_source = inspect.getsource(team_chat_agent._stream_via_promax)
        assert '"model": MODEL_SONNET' in body_source

    def test_the_frame_and_the_row_read_one_variable(self):
        """The defect this replaces was the SAME constant written in two places,
        which stayed true right up until a team could answer on something else."""
        source = inspect.getsource(team_chat_agent._do_handle)
        assert source.count("agent_name = _answering_model(") == 1
        assert '"agent_name": agent_name' in source, "the streamed frame"
        assert "agent_name=agent_name" in source, "the persisted row"
        assert "agent_name=MODEL_SONNET" not in source
        assert '"agent_name": MODEL_SONNET' not in source

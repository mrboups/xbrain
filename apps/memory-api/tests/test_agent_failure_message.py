"""A provider's error text must never reach a team chat.

The bug this pins: a billing failure rendered in the chat as the agent's reply —

    🤖 claude-sonnet-4-6 / via team API / agent · from your brain
    (error: Error code: 400 - {'type': 'error', 'error': {'type':
     'invalid_request_error', 'message': 'Your credit balance is too low to
     access the Anthropic API. Please go to Plans & Billing...'}})

Two defects in one line. It leaked the vendor, the account's billing state and a
request id to everyone in the team; and it was attributed to the agent as though
the agent had written it.

These tests assert the SHAPE, not one sample string: any exception carrying any
message must produce a payload whose words come from this module's own fixed
vocabulary. A test pinned to the credit-balance sentence would pass on the day it
was written and say nothing about the next provider error.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.team_chat_agent import (
    AGENT_FAILURE_PERMANENT,
    AGENT_FAILURE_RETRYABLE,
    AGENT_FAILURE_TIMEOUT,
    FAILURE_CODE_CONFIGURATION,
    FAILURE_CODE_TIMEOUT,
    FAILURE_CODE_UNAVAILABLE,
    classify_stream_failure,
)

# Every sentence this module is allowed to put in front of a team.
SAFE_SENTENCES = {
    AGENT_FAILURE_RETRYABLE,
    AGENT_FAILURE_PERMANENT,
    AGENT_FAILURE_TIMEOUT,
}

# The real one, plus the kinds of thing providers say. None of these words may
# survive into a message.
LEAKY_TEXTS = [
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. Please go "
    "to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011abc'}",
    "401 Unauthorized: invalid x-api-key",
    "rate_limit_error: this organization has exceeded its tokens-per-minute quota",
    "connection to api.anthropic.com:443 failed: getaddrinfo ENOTFOUND",
    "Traceback (most recent call last): File '/app/app/services/x.py', line 12",
    "<html><body>502 Bad Gateway (nginx/1.25.3)</body></html>",
    "",
]

# Words that betray where the failure came from, or who to bill for it.
FORBIDDEN_FRAGMENTS = [
    "anthropic",
    "credit balance",
    "plans & billing",
    "api key",
    "x-api-key",
    "request_id",
    "req_",
    "error code",
    "invalid_request_error",
    "rate_limit_error",
    "traceback",
    "nginx",
    "getaddrinfo",
    "401",
    "400",
    "502",
    "{",
    "}",
]


def _failures_for(text: str):
    """One classified payload per exception kind, all carrying the same text."""
    request = httpx.Request("POST", "https://provider.example/v1/messages")
    return [
        classify_stream_failure(RuntimeError(text)),
        classify_stream_failure(ValueError(text)),
        classify_stream_failure(asyncio.TimeoutError(text)),
        classify_stream_failure(httpx.ReadTimeout(text, request=request)),
        classify_stream_failure(httpx.ConnectError(text, request=request)),
        classify_stream_failure(
            httpx.HTTPStatusError(
                text,
                request=request,
                response=httpx.Response(400, request=request, text=text),
            )
        ),
        classify_stream_failure(
            httpx.HTTPStatusError(
                text,
                request=request,
                response=httpx.Response(500, request=request, text=text),
            )
        ),
    ]


class TestNothingLeaks:
    @pytest.mark.parametrize("text", LEAKY_TEXTS)
    def test_the_message_is_always_one_of_our_own_sentences(self, text: str):
        # Asserted against the ALLOW-LIST, not against a blocklist of bad words:
        # a blocklist only catches the leaks somebody thought of.
        for failure in _failures_for(text):
            assert failure["message"] in SAFE_SENTENCES

    @pytest.mark.parametrize("text", LEAKY_TEXTS)
    def test_no_fragment_of_the_provider_text_survives(self, text: str):
        for failure in _failures_for(text):
            # Every value a client could render, joined — so a leak into `code`
            # counts exactly as much as one into `message`.
            rendered = " ".join(str(v) for v in failure.values()).lower()
            for fragment in FORBIDDEN_FRAGMENTS:
                assert fragment not in rendered, f"{fragment!r} leaked: {failure}"

    @pytest.mark.parametrize("text", LEAKY_TEXTS)
    def test_the_payload_carries_no_free_text_field_at_all(self, text: str):
        # The strongest version of the rule: there is no key a raw string could
        # be dropped into later without this test noticing.
        for failure in _failures_for(text):
            assert set(failure) == {"code", "message", "retryable"}
            assert failure["code"] in {
                FAILURE_CODE_TIMEOUT,
                FAILURE_CODE_UNAVAILABLE,
                FAILURE_CODE_CONFIGURATION,
            }
            assert isinstance(failure["retryable"], bool)

    def test_an_exception_with_no_message_still_produces_a_sentence(self):
        failure = classify_stream_failure(RuntimeError())
        assert failure["message"] in SAFE_SENTENCES


class TestTheAdviceIsHonest:
    """Whether to retry is the one useful thing the server can actually say."""

    def test_a_timeout_says_so_and_is_retryable(self):
        failure = classify_stream_failure(asyncio.TimeoutError())
        assert failure["code"] == FAILURE_CODE_TIMEOUT
        assert failure["retryable"] is True

    def test_a_4xx_is_the_servers_problem_and_is_not_retryable(self):
        # A bad key, an exhausted quota, a malformed request: nobody in the team
        # can fix it and retrying just repeats it.
        request = httpx.Request("POST", "https://provider.example/v1/messages")
        failure = classify_stream_failure(
            httpx.HTTPStatusError(
                "credit balance too low",
                request=request,
                response=httpx.Response(400, request=request),
            )
        )
        assert failure["code"] == FAILURE_CODE_CONFIGURATION
        assert failure["retryable"] is False
        assert "administrator" in failure["message"]

    def test_a_429_is_transient_despite_being_a_4xx(self):
        request = httpx.Request("POST", "https://provider.example/v1/messages")
        failure = classify_stream_failure(
            httpx.HTTPStatusError(
                "rate limited",
                request=request,
                response=httpx.Response(429, request=request),
            )
        )
        assert failure["retryable"] is True

    def test_an_unknown_failure_fails_toward_try_again(self):
        # The safe direction: suggesting a retry costs one retry, while saying
        # "do not bother" hides an outage that would have cleared on its own.
        assert classify_stream_failure(RuntimeError("???"))["retryable"] is True

    def test_no_sentence_invents_a_cause(self):
        # The server does not know WHY, and a chat that guesses teaches the team
        # to distrust the explanations that are real.
        for sentence in SAFE_SENTENCES:
            lowered = sentence.lower()
            for guess in ["because", "due to", "caused by", "billing", "network", "quota"]:
                assert guess not in lowered

    def test_every_sentence_reads_as_a_sentence(self):
        for sentence in SAFE_SENTENCES:
            assert sentence[0].isupper() and sentence.endswith(".")
            assert "_" not in sentence


class TestTheFailureIsNotTheReply:
    """A failed attempt must not be persisted as though the agent wrote it."""

    def test_the_stream_error_frame_names_our_sentence_not_the_exception(self):
        # Reads the source: the publish call sits inside a background coroutine
        # with a live DB session and a Centrifugo client, so the cheap, durable
        # way to pin the payload is the call site itself.
        import inspect

        from app.services import team_chat_agent

        source = inspect.getsource(team_chat_agent._do_handle)
        assert 'str(e)[:200]' not in source, (
            "the stream_error frame is publishing the raw exception again — "
            "that string is what every member of the team reads"
        )
        assert '"error": failure["message"]' in source

    def test_a_failure_is_flagged_on_the_persisted_row(self):
        import inspect

        from app.services import team_chat_agent

        source = inspect.getsource(team_chat_agent._do_handle)
        assert '"agent_failure"' in source, (
            "a failed attempt persisted without a flag re-renders as an ordinary "
            "answer from the agent on every reload"
        )
        assert "Claude stream interrupted" not in source, (
            "the raw exception is still being written into the message CONTENT"
        )

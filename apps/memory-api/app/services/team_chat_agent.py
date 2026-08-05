"""Team chat @agent mention handler — Pro/Max routing + a team-chosen fallback.

Quick task 260512-tcr Wave 2.5. When memory-api detects an `@agent` mention
in a posted team message, this handler runs in a background task:

  1. Resolve the triggering user via source_user_id.
  2. Probe the session-bridge to see if the user has a live WS connection.
     - If yes: route the chat via the user's Pro/Max subscription (sign a
       bridge JWT with `acting_user_sub=<user>` and POST bridge
       /v1/chat/completions). Cost = 0 to xbrain.
     - If no: fall back to the provider the TEAM selected (teams.agent_provider,
       migration 0033) with that provider's key. Cost billed to whoever's key
       resolved — the team's own if they supplied one, the operator's otherwise.
  3. Stream tokens to Centrifugo channel `team:<team_id>` so all subscribed
     clients (extension + future PWA) see the agent typing in realtime.
  4. Persist the final reply as one `team_messages` row with kind='agent',
     agent_name=<the model that actually answered>, routed_via='user_promax' |
     'team_key' | 'team_api'.

THE SUBSCRIPTION IS NOT A PROVIDER CHOICE. A team's selection governs step 2's
FALLBACK and nothing else. A live bridge answers through that person's Claude
Pro/Max session whatever the team selected, because that path is free and is the
whole point of the extension. Choosing OpenAI does not opt a team out of it.

AND THE NAME IS THE MODEL THAT ANSWERED. `agent_name` used to be the constant
`claude-sonnet-4-6` on both the streamed frame and the persisted row, which was
true right up until a team could answer on something else — after which a team on
OpenAI would have had every reply in its history labelled as Claude. It is now
decided ONCE per turn, before the first frame goes out, so a reload can never
disagree with what was on screen.

The Anthropic prompt is built around a CACHED memory block (5min TTL via
team_context_cache) so subsequent mentions in the same window get
~90% input cost reduction from Anthropic's ephemeral cache. The OpenAI-compatible
path has no equivalent breakpoint mechanism, so the same blocks are flattened to
one system string there.

Hard caps for v1:
  - 4k output tokens (decision #15)
  - 100k input budget (Sonnet supports 200k; we keep headroom)
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import anthropic
import httpx
import openai
import sqlalchemy as sa
import structlog
from anthropic import AsyncAnthropic
from authlib.jose import jwt as jose_jwt
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.models.team import Team
from app.models.team_message import TeamMessage
from app.models.user import User
from app.repos import team_messages as tm_repo
from app.services import centrifugo_client, team_context_cache, team_keys
from app.services.team_keys import FallbackKey

log = structlog.get_logger(__name__)


MODEL_SONNET = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 4000

# session-bridge, on the internal compose network. One base for both hops: the
# liveness probe and the streaming call must reach the SAME process, or the
# routing decision is about one bridge and the request goes to another.
BRIDGE_BASE_URL = "http://session-bridge:8105"
# Short on purpose. This sits in front of every agent turn, and a slow answer
# here is worth less than a fast wrong-but-safe one: the timeout falls through
# to the fallback key, which is where an unreachable bridge lands anyway.
BRIDGE_STATUS_TIMEOUT_S = 3.0

# ---------------------------------------------------------------------------
# What a team is told when the agent cannot answer
# ---------------------------------------------------------------------------
#
# A provider's error text is written for the person holding the account, and this
# is a TEAM chat: everyone in the team reads it. The one that prompted this code
# named the vendor, the account's billing state and a request id, to a room of
# people who can do nothing about any of it —
#
#   Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error',
#   'message': 'Your credit balance is too low to access the Anthropic API...'}}
#
# So nothing the provider says crosses this boundary. `classify_stream_failure`
# is the ONLY writer of user-visible failure words, its vocabulary is fixed here,
# and its fallback is deliberately vague — an error nobody has written a safe
# sentence for must degrade to a vague sentence, never to the raw string. The raw
# text stays in the structured log, where an operator can find it.
#
# The sentences say what happened and whether waiting helps. They never guess a
# CAUSE: the server does not know whether a 500 was the provider or the network,
# and a chat that invents one teaches the team to distrust the ones that are real.

AGENT_FAILURE_RETRYABLE = "The agent could not answer just now. Worth trying again."
AGENT_FAILURE_PERMANENT = (
    "The agent could not answer. Trying again will not help — this needs an "
    "administrator to look at the server."
)
AGENT_FAILURE_TIMEOUT = (
    "The agent took too long to answer and the attempt was stopped. "
    "Worth trying again."
)
# ---------------------------------------------------------------------------
# An answer that was never sent is not an answer
# ---------------------------------------------------------------------------
#
# The request went out, the transport reported success, and nothing came back.
# This used to be persisted as the agent's CONTENT, literally `(empty response)`
# — a parenthetical apology in the thread, attributed to the agent, and
# indistinguishable from the agent choosing to say nothing. Two of them landed in
# production thirty minutes apart while every log line said `team_chat_agent.done`.
#
# It gets its own code because the condition is genuinely distinct from a refused
# key and from an absent route, and the two transports fail this way for different
# reasons — so the sentence names which one came back empty. The row's
# `routed_via` says the same thing in machine-readable form, which is how a person
# can tell which path answered.
#
# IT IS PER-USER, WHICH IS THE WHOLE CLUE. Observed in production: two people in
# ONE team, both routed `user_promax`, one getting answers and one getting
# nothing — and the FRESHER bridge was the one failing (49 seconds vs three
# hours), which rules out liveness. session-bridge logged `chat.routed` and 200
# OK. The socket was alive, the browser ran the fetch, and claude.ai gave that
# browser nothing usable. docs/session-bridge-guide.md §9 names the likeliest
# reason first: not signed in to the provider in that browser profile, or the
# session expired.
#
# So the subscription sentence names a CHECK the person can perform in under a
# minute, not a cause the server is in no position to verify. It never says "you
# are logged out" — the server saw an empty body, nothing more — but it does say
# where to look, because the alternative is telling somebody a product is broken
# when they are one sign-in away from it working.
#
# WHY THE SUBSCRIPTION PATH DOES NOT FALL THROUGH TO A KEY.
#
# It is the tempting repair and it is wrong twice over.
#
# If the cause is a stale claude.ai session, the fall-through bills the team's API
# key for a LOGIN problem — money spent on something that was free and that the
# person could have fixed themselves, and they would find out from an invoice.
#
# If the cause is instead our own SSE parsing — the other failure §9 records, a
# CRLF delimiter against a client splitting on "\n\n": zero blocks parsed, 200 OK,
# empty reply — then it is not occasional but TOTAL, and the fall-through quietly
# pays per turn, forever, to route around a bug we control.
#
# The same reasoning already applies twice in this file: a refused team key is not
# retried on the operator's, and a selected provider with no key is not answered
# by a different one. Spending money the person did not ask for is what all three
# rules refuse.
AGENT_FAILURE_EMPTY_SUBSCRIPTION = (
    "The Claude subscription accepted the request and sent nothing back. Check "
    "that you are signed in to claude.ai in the browser where the xbrain "
    "extension is signed in, then send this again."
)
AGENT_FAILURE_EMPTY_PROVIDER = (
    "The provider accepted the request and sent nothing back. Worth trying again."
)

# ---------------------------------------------------------------------------
# Not available is not the same as broken
# ---------------------------------------------------------------------------
#
# There was one shape of bad news for every situation, and it always read as a
# malfunction. A team whose agent simply had nothing to run on was told the
# attempt had failed — so a configuration nobody had finished setting up looked
# like a product that did not work.
#
# The two sentences below describe an absence, and both say what would end it.
# Neither implies an attempt was made and lost.
#
# WHAT THEY DELIBERATELY DO NOT SAY: anything about this device. The bridge is
# keyed by USER, not by device — a phone with no extension routes through
# whatever browser that person has open somewhere, and answers perfectly. The
# only condition worth telling anyone about is "no live bridge for this user
# ANYWHERE", which is the same sentence on a laptop and on a phone.

AGENT_UNAVAILABLE_NO_ROUTE = (
    "The agent has no model to answer with. It runs on a Claude subscription "
    "through the xbrain extension — open the browser where that extension is "
    "signed in, or set a team API key, which is billed to the team."
)
AGENT_UNAVAILABLE_SUBSCRIPTION_LOST = (
    "The Claude subscription is no longer connected. Open the browser where the "
    "xbrain extension is signed in, then send this again."
)
# Whose key failed matters. Somebody who pasted a key into team settings needs
# to know it was theirs that was refused and not the product that broke — and
# the sentence still carries none of the provider's own words.
AGENT_TEAM_KEY_REJECTED = (
    "The team's own API key was refused. It needs to be replaced in team "
    "settings — trying again with the same key will not help."
)
# A team that CHOSE a provider and stored no key for it. Naming the provider is
# the point: the remedy is specific, and a team that picked Grok and reads a
# sentence about a missing key with no vendor in it will go looking in the wrong
# place. The name comes from team_keys.PROVIDER_LABELS — a fixed table of OUR
# words for a vendor — and never from a provider's own text or from anything a
# database happened to hold. An unrecognised value renders the generic sentence
# instead, so this template has no path to unvalidated input.
#
# The alternative to this state was answering on a DIFFERENT provider, which is
# a team discovering in an invoice that they were billed by a vendor they did
# not choose. Same reasoning as a refused team key, one level up.
AGENT_UNAVAILABLE_PROVIDER_KEY_MISSING = (
    "The agent is set to fall back to {provider}, and no API key is stored for "
    "it. Add one in team settings, or open the browser where the xbrain "
    "extension is signed in to use the Claude subscription."
)

# Failure codes are OURS, not the provider's. They exist so a client can style or
# count outcomes without parsing a sentence, and so the sentence can be reworded
# without breaking anything downstream.
FAILURE_CODE_TIMEOUT = "timeout"
FAILURE_CODE_UNAVAILABLE = "unavailable"
FAILURE_CODE_CONFIGURATION = "configuration"
# An attempt succeeded at the transport level and produced no text at all.
FAILURE_CODE_EMPTY_ANSWER = "empty_answer"
# The three that mean "there was nothing to try", not "the try went wrong".
FAILURE_CODE_NO_ROUTE = "no_route"
FAILURE_CODE_SUBSCRIPTION_LOST = "subscription_lost"
FAILURE_CODE_PROVIDER_KEY_MISSING = "provider_key_missing"
# An attempt WAS made and refused, so this is a failure — but a distinct one,
# because the fix belongs to the team rather than to an administrator.
FAILURE_CODE_TEAM_KEY_REJECTED = "team_key_rejected"

# Codes a client must render as unavailability rather than as a failure. Kept as
# a set so the client's copy of the same distinction has something to mirror.
UNAVAILABILITY_CODES = frozenset(
    {
        FAILURE_CODE_NO_ROUTE,
        FAILURE_CODE_SUBSCRIPTION_LOST,
        FAILURE_CODE_PROVIDER_KEY_MISSING,
    }
)


class AgentRouteUnavailable(Exception):
    """No live bridge for this user anywhere, and no usable fallback key.

    Raised BEFORE any provider is contacted, which is the whole point: nothing
    was attempted, so nothing failed.
    """


class BridgeSessionLost(Exception):
    """The bridge had the socket when we asked and not when we called.

    A window of milliseconds, and it widens to whole seconds whenever a laptop
    sleeps mid-turn. Distinguished from a generic outage because the person can
    act on it, and because retrying will now route via the fallback key instead.
    """


class TeamKeyRejected(Exception):
    """The provider refused the TEAM's own key (401/403).

    Carries no detail on purpose. It exists to name WHOSE key failed, and the
    key itself must not be anywhere near an exception that gets logged.
    """


class EmptyAnswer(Exception):
    """The transport reported success and streamed no text at all.

    Raised at the END of a clean stream rather than detected afterwards, so it
    travels the same path every other outcome does — one classifier, one publish,
    one persisted row. That is what makes the live frame and the reloaded row
    agree: there is no second way for a turn to end.

    Carries WHICH path came back empty, as a boolean, and no text. The two
    sentences it selects between are fixed.
    """

    def __init__(self, *, via_subscription: bool) -> None:
        super().__init__()
        self.via_subscription = via_subscription


class ProviderKeyMissing(Exception):
    """The team CHOSE a provider and stored no key for it.

    Raised BEFORE any provider is contacted, like AgentRouteUnavailable: nothing
    was attempted, so nothing failed. It is a separate exception because the
    remedy is specific to one vendor, and a team that picked Grok needs to be
    told about Grok.

    Carries the canonical provider id as an ATTRIBUTE and deliberately leaves the
    base exception message EMPTY. `str()` of it is "" — so even if a future caller
    forgets and stringifies it into something, there is nothing to leak. The id is
    turned into words only by team_keys.PROVIDER_LABELS, which refuses anything it
    does not recognise.
    """

    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = provider


# How a resolved key tier is labelled on the persisted row. "team_api" is kept
# for the deployment-wide key because rows already carry it; the team's own key
# is a NEW value, so the two can never be confused after the fact.
_ROUTED_VIA_BY_TIER = {
    team_keys.TIER_TEAM: "team_key",
    team_keys.TIER_PLATFORM: "team_api",
    team_keys.TIER_NONE: "unavailable",
}


def _http_status_of(exc: BaseException) -> int | None:
    """The status class of an exception, if it carries one. Never the body."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


# Every timeout type the three transports can raise, named EXPLICITLY.
#
# A new client's exception types are new. `openai.APITimeoutError` is not an
# `asyncio.TimeoutError` and not an `httpx.TimeoutException` — it subclasses
# `APIConnectionError` — so without this tuple every OpenAI and xAI timeout would
# have landed in the catch-all below. The catch-all is safe (it stringifies
# nothing), but it would have told a team "worth trying again" without saying the
# attempt was stopped for taking too long, which is the one detail that makes the
# advice make sense. Anthropic's own timeout type is here for the same reason:
# it was already falling through.
_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    httpx.TimeoutException,
    anthropic.APITimeoutError,
    openai.APITimeoutError,
)

# The status-carrying base of each SDK. Both expose `.status_code` and a
# `.response`; `_http_status_of` reads the STATUS and never the response, which
# is where a provider writes the account details. Named here so the property is
# asserted against the real types rather than assumed from duck-typing.
_STATUS_ERROR_TYPES: tuple[type[BaseException], ...] = (
    httpx.HTTPStatusError,
    anthropic.APIStatusError,
    openai.APIStatusError,
)


def _as_team_key_failure(exc: BaseException, fallback: FallbackKey) -> BaseException:
    """Rename a provider refusal when the key that was refused was the team's.

    Returns the ORIGINAL exception untouched in every other case, so this can
    only ever add information and never swallow one.
    """
    if fallback.is_team_key and _http_status_of(exc) in (401, 403):
        return TeamKeyRejected()
    return exc


def classify_stream_failure(exc: BaseException) -> dict[str, Any]:
    """Turn any streaming exception into a safe {code, message, retryable}.

    Classification reads the exception TYPE and, for HTTP errors, the status
    class — never the response body, which is where a provider puts the account
    details. 4xx is the server's own configuration (a key, a quota, a malformed
    request): the team cannot fix it and retrying repeats it. Everything else is
    treated as transient, which is the safe direction — telling somebody to try
    again costs a retry, while telling them not to bother hides an outage that
    would have cleared.

    The routing exceptions are matched by type FIRST. They are ours, they carry
    no provider text by construction, and they are the only inputs that produce
    an unavailability rather than a failure.

    THREE transports now reach this function — the bridge (httpx), Anthropic, and
    the OpenAI-compatible client that also serves xAI — and each raises its own
    types. Those types are enumerated in `_TIMEOUT_TYPES` above rather than left
    to the catch-all: an unmapped exception here is not a leak (the fallback
    stringifies nothing and returns a fixed sentence) but it IS wrong advice, and
    a client that has to guess is the thing this function exists to prevent.
    """
    if isinstance(exc, AgentRouteUnavailable):
        return {
            "code": FAILURE_CODE_NO_ROUTE,
            "message": AGENT_UNAVAILABLE_NO_ROUTE,
            # Nothing about trying again changes an absent configuration.
            "retryable": False,
        }

    if isinstance(exc, BridgeSessionLost):
        return {
            "code": FAILURE_CODE_SUBSCRIPTION_LOST,
            "message": AGENT_UNAVAILABLE_SUBSCRIPTION_LOST,
            # True, and honestly so: the next attempt finds no bridge and takes
            # the fallback key, or finds the reopened browser.
            "retryable": True,
        }

    if isinstance(exc, ProviderKeyMissing):
        # The ONLY parameterised sentence in this module, and the parameter comes
        # from a fixed table keyed on a value the route validated against a closed
        # set. `label_for` returns None for anything else, and an unnameable
        # provider degrades to the sentence that names none — so no string from a
        # database, a header or an exception can reach the format slot.
        label = team_keys.label_for(getattr(exc, "provider", ""))
        if label is None:
            return {
                "code": FAILURE_CODE_NO_ROUTE,
                "message": AGENT_UNAVAILABLE_NO_ROUTE,
                "retryable": False,
            }
        return {
            "code": FAILURE_CODE_PROVIDER_KEY_MISSING,
            "message": AGENT_UNAVAILABLE_PROVIDER_KEY_MISSING.format(provider=label),
            # A key that is not there will not be there on the next attempt.
            "retryable": False,
        }

    if isinstance(exc, EmptyAnswer):
        # Retryable, and honestly so: an empty stream is transient far more often
        # than not. If it is NOT transient — a changed SSE shape, say — every turn
        # comes back here, which is a far better signal than every turn quietly
        # spending a key.
        return {
            "code": FAILURE_CODE_EMPTY_ANSWER,
            "message": (
                AGENT_FAILURE_EMPTY_SUBSCRIPTION
                if getattr(exc, "via_subscription", False)
                else AGENT_FAILURE_EMPTY_PROVIDER
            ),
            "retryable": True,
        }

    if isinstance(exc, TeamKeyRejected):
        return {
            "code": FAILURE_CODE_TEAM_KEY_REJECTED,
            "message": AGENT_TEAM_KEY_REJECTED,
            "retryable": False,
        }

    if isinstance(exc, _TIMEOUT_TYPES):
        return {
            "code": FAILURE_CODE_TIMEOUT,
            "message": AGENT_FAILURE_TIMEOUT,
            "retryable": True,
        }

    status = _http_status_of(exc)
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        # 429 is excluded on purpose: rate limiting IS the transient 4xx, and
        # "an administrator must look at this" would be wrong for it.
        return {
            "code": FAILURE_CODE_CONFIGURATION,
            "message": AGENT_FAILURE_PERMANENT,
            "retryable": False,
        }

    return {
        "code": FAILURE_CODE_UNAVAILABLE,
        "message": AGENT_FAILURE_RETRYABLE,
        "retryable": True,
    }
# The preamble is byte-STABLE across turns on purpose. It sits in front of the
# cache_control'd product KB block, and Anthropic matches cached prefixes, so a
# preamble that varied per turn would invalidate the KB cache on every message.
# That is why the rule about web content lives here (always true) while the
# per-turn FACT of what was fetched lives in the uncached user turn — see
# NO_WEB_CONTENT_MARKER.
SYSTEM_PROMPT_PREAMBLE = (
    "You are the xbrain agent, embedded in the team chat for team {team_slug}. "
    "Answer based on the product knowledge base below, the team's memory "
    "items, and the recent chat history. Be concise, factual, and reference "
    "specific items when relevant. If you don't know, say so — do not "
    "hallucinate.\n\n"
    "You cannot browse the web. The ONLY web page content you can see is what "
    "appears under '## Fetched web content' in this conversation. If that "
    "section says nothing was fetched, you have not read the linked page: say "
    "plainly that you cannot see its contents and ask for the relevant text to "
    "be pasted in. Never state or imply that you fetched, opened, visited, or "
    "read a link — not even as a preamble to explaining that you could not."
)

# Path to the markdown product KB shipped in the repo. Loaded once at module
# import; restart memory-api to pick up edits. Anthropic cache_control:
# ephemeral makes this block free on every follow-up call within ~5 minutes.
_KB_PATH = Path(__file__).parent.parent / "knowledge" / "xbrain_product_kb.md"


def _load_product_kb() -> str:
    """Load the product KB markdown once at module import.

    Returns an empty string on failure so the agent still works (just without
    the product-context block). Logs a warning so ops can spot a deploy with
    the KB file missing.
    """
    try:
        return _KB_PATH.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("team_chat_agent.kb.load_failed", path=str(_KB_PATH), err=str(e))
        return ""


_PRODUCT_KB = _load_product_kb()


# --- Public entry point ------------------------------------------------------


async def handle_claude_mention(
    *,
    team_id: UUID,
    triggering_message_id: UUID,
    triggering_user_sub: str,
) -> None:
    """Background-task entry point. Never raises — logs on failure.

    Called via asyncio.create_task() from the team_chat POST route after
    the user message has been committed.
    """
    # Each call gets its own DB session so we don't share one with the
    # request-scoped session that committed the user message.
    try:
        async with async_session_factory() as session:
            await _do_handle(
                session=session,
                team_id=team_id,
                triggering_message_id=triggering_message_id,
                triggering_user_sub=triggering_user_sub,
            )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "team_chat_agent.error",
            team_id=str(team_id),
            err=str(e),
        )


async def _do_handle(
    *,
    session: AsyncSession,
    team_id: UUID,
    triggering_message_id: UUID,
    triggering_user_sub: str,
) -> None:
    team = (
        await session.execute(select(Team).where(Team.id == team_id))
    ).scalar_one_or_none()
    if team is None:
        log.warning("team_chat_agent.team_missing", team_id=str(team_id))
        return

    triggering_message = (
        await session.execute(
            select(TeamMessage).where(TeamMessage.id == triggering_message_id)
        )
    ).scalar_one_or_none()
    if triggering_message is None:
        log.warning(
            "team_chat_agent.triggering_message_missing",
            message_id=str(triggering_message_id),
        )
        return

    # Build the cacheable context block.
    bundle = await team_context_cache.get_team_memory_bundle(
        session, team_scope=team.slug, team_id=team.id
    )
    recent = await tm_repo.get_recent_messages_chronological(
        session, team_id=team.id, limit=20
    )

    system_prompt = _build_system_prompt(team_slug=team.slug)
    cached_memory_block = _format_memory_block(bundle["bundle"])
    chat_history_block = _format_chat_history(recent)

    # Pre-fetch the links this mention is plausibly about and append them to the
    # (uncached) user turn. The window is the recent conversation, not just the
    # summoning message: "paste a link, then ask about it" is how people actually
    # use a chat, and reading only the mention found nothing in exactly that case.
    #
    # The block is appended UNCONDITIONALLY. When there is nothing to fetch it
    # says so, because a prompt that silently omits the section invites the model
    # to claim a fetch that never happened — which is what it did.
    prefetch_urls = _recent_urls(triggering_message=triggering_message, recent=recent)
    chat_history_block = chat_history_block + await _build_fetched_web_block(
        prefetch_urls, team.slug
    )
    log.info(
        "team_chat_agent.web_prefetch",
        team_id=str(team_id),
        urls=len(prefetch_urls),
    )

    # Decide routing. The subscription is the preferred path; a key is the floor
    # under it; neither is an unavailability, not an error. The team's own key
    # outranks the deployment's — they pay for the calls the subscription could
    # not take, and only for those.
    #
    # The team's selection is read HERE, fresh from the row loaded this turn, so
    # switching provider takes effect on the very next mention with nothing to
    # invalidate. It governs the fallback ONLY: a live bridge answers on the
    # person's Claude subscription whatever was selected, because that path is
    # free and is why the extension exists.
    provider = _selected_provider(team)
    has_promax = await _user_has_live_bridge(triggering_user_sub)
    fallback = (
        FallbackKey(key=None, tier=team_keys.TIER_NONE)
        if has_promax
        else await team_keys.resolve_fallback_key(team_id=team.id, provider=provider)
    )
    routed_via = "user_promax" if has_promax else _ROUTED_VIA_BY_TIER[fallback.tier]
    # WHO ANSWERS, decided once, before the first frame leaves. Both the frame
    # below and the persisted row read this single variable, so a reload cannot
    # disagree with what was on screen — which is exactly what a hardcoded
    # constant in two places guaranteed it eventually would.
    agent_name = _answering_model(provider=provider, has_promax=has_promax)

    message_id = uuid.uuid4()
    started_at = time.monotonic()
    log.info(
        "team_chat_agent.start",
        team_id=str(team_id),
        triggering_user_sub=triggering_user_sub,
        routed_via=routed_via,
        provider=provider,
        agent_name=agent_name,
        message_id=str(message_id),
        memory_cached=bundle["cached"],
        memory_items=bundle["item_count"],
    )

    # Announce stream start so clients can render an empty bubble immediately.
    await centrifugo_client.publish(
        channel=f"team:{team_id}",
        data={
            "type": "agent_stream_start",
            "message_id": str(message_id),
            "agent_name": agent_name,
            "provider": provider,
            "routed_via": routed_via,
        },
    )

    full_text_parts: list[str] = []
    token_usage: dict[str, Any] = {}
    # None while the attempt is still good. Set once, by the handler below, and
    # read by both the persisted row and its metadata so the message and its
    # "this is a failure" flag can never disagree.
    failure: dict[str, Any] | None = None

    try:
        if has_promax:
            async for chunk_text, usage in _stream_via_promax(
                triggering_user_sub=triggering_user_sub,
                system_prompt=system_prompt,
                cached_memory_block=cached_memory_block,
                chat_history_block=chat_history_block,
            ):
                if chunk_text:
                    full_text_parts.append(chunk_text)
                    await centrifugo_client.publish(
                        channel=f"team:{team_id}",
                        data={
                            "type": "agent_stream_chunk",
                            "message_id": str(message_id),
                            "delta": chunk_text,
                        },
                    )
                if usage:
                    token_usage = usage
        elif fallback.key:
            try:
                async for chunk_text, usage in _stream_via_fallback_provider(
                    provider=provider,
                    api_key=fallback.key,
                    system_prompt=system_prompt,
                    cached_memory_block=cached_memory_block,
                    chat_history_block=chat_history_block,
                ):
                    if chunk_text:
                        full_text_parts.append(chunk_text)
                        await centrifugo_client.publish(
                            channel=f"team:{team_id}",
                            data={
                                "type": "agent_stream_chunk",
                                "message_id": str(message_id),
                                "delta": chunk_text,
                            },
                        )
                    if usage:
                        token_usage = usage
            except Exception as e:  # noqa: BLE001
                # A refused TEAM key is named as such and NOT retried against
                # the deployment key. Silently spending the operator's key on a
                # team that supplied their own is a surprise they would find in
                # an invoice. Status class only — the body is never read.
                renamed = _as_team_key_failure(e, fallback)
                if renamed is not e:
                    # The renamed exception carries nothing, deliberately, so the
                    # provider's own text would vanish with it. An operator still
                    # needs to see what was actually said — it goes HERE, in the
                    # log, and no further. Never the key: only what came back.
                    log.warning(
                        "team_chat_agent.team_key_refused",
                        team_id=str(team_id),
                        status=_http_status_of(e),
                        err=str(e),
                        err_type=type(e).__name__,
                    )
                raise renamed from None
        else:
            # Nothing to run on. Raised rather than returned so it travels the
            # same path every other outcome does — one classifier, one publish,
            # one persisted row, and no second way for a turn to end.
            #
            # NOT a quiet retry on some other provider's key: a team that chose
            # OpenAI and got billed for Anthropic is a surprise nobody asked for,
            # and the same reasoning already applies one level down to a refused
            # team key.
            raise _no_route_for(provider)

        if not "".join(full_text_parts).strip():
            # The transport said 200 and sent no text. Raised HERE, inside the
            # same try, so it is classified, published and persisted by the same
            # three lines every other outcome uses — which is what stops a reload
            # from disagreeing with the live view.
            #
            # It deliberately does NOT retry on the fallback key when the
            # subscription came back empty. See AGENT_FAILURE_EMPTY_SUBSCRIPTION
            # above: the likeliest cause is our own SSE parsing, that failure is
            # total rather than occasional, and silently paying per turn to route
            # around it is how a team finds out from an invoice.
            raise EmptyAnswer(via_subscription=has_promax)

    except Exception as e:  # noqa: BLE001
        # The team is told THAT the agent failed and whether waiting helps. The
        # provider's own words go to the log and no further — see the
        # classify_stream_failure block at the top of this module for why.
        failure = classify_stream_failure(e)
        log.warning(
            "team_chat_agent.stream_error",
            team_id=str(team_id),
            routed_via=routed_via,
            failure_code=failure["code"],
            err=str(e),  # the raw provider text lives HERE, for an operator
            err_type=type(e).__name__,
            partial_len=sum(len(p) for p in full_text_parts),
        )
        await centrifugo_client.publish(
            channel=f"team:{team_id}",
            data={
                "type": "agent_stream_error",
                "message_id": str(message_id),
                # Named `error` for the frames already in flight from older
                # servers, but it now carries OUR sentence, never the provider's.
                "error": failure["message"],
                "code": failure["code"],
                "retryable": failure["retryable"],
            },
        )

    # What the row says, and what it says it IS.
    #
    # A failed attempt used to be persisted as the agent's content, so the
    # provider's error re-rendered as the agent's reply on every reload — a
    # message the agent never wrote, attributed to it forever. Partial text is
    # still kept when some arrived: the team can see what came through, and the
    # flag below is what stops it reading as a complete answer.
    # There is no third branch any more. A clean stream that produced nothing
    # raised EmptyAnswer above, so `failure is None` now GUARANTEES text. The
    # parenthetical placeholder that used to live here was a failure wearing an
    # answer's clothes: it went into the thread as the agent's own words and read
    # as the agent choosing to say nothing.
    partial_text = "".join(full_text_parts).strip()
    full_text = partial_text if partial_text else failure["message"]  # type: ignore[index]

    elapsed_ms = int((time.monotonic() - started_at) * 1000)

    # Persist the agent message. We re-open a fresh session so the previous
    # rollback (if any) doesn't poison the insert.
    metadata: dict[str, Any] = {
        "elapsed_ms": elapsed_ms,
        "token_usage": token_usage,
        "triggering_user_sub": triggering_user_sub,
        # Which provider this turn was routed to. Not a secret — the team chose
        # it — and it is what makes an old row readable after a team switches.
        "provider": provider,
        "memory_cached": bundle["cached"],
        "memory_items": bundle["item_count"],
    }
    if failure is not None:
        # The row is a FAILURE, and it says so on disk. Without this the client
        # has only the live `agent_stream_error` frame to go on, so a reload — or
        # anyone who was not connected when it happened — reads the same row as
        # an ordinary answer from the agent. The raw cause is NOT here: this
        # column is served to every member of the team.
        metadata["agent_failure"] = {
            "code": failure["code"],
            "retryable": failure["retryable"],
            "partial": bool(partial_text),
        }

    async with async_session_factory() as persist_session:
        agent_msg = await tm_repo.insert_agent_message(
            persist_session,
            team_id=team_id,
            # The SAME variable the stream_start frame carried. A reload reads
            # the row; a live client read the frame; they must name the same
            # model or one of them is lying.
            agent_name=agent_name,
            content=full_text,
            routed_via=routed_via,
            parent_message_id=triggering_message_id,
            metadata=metadata,
        )
        # Carry over the pre-generated message_id so clients can correlate
        # the final persisted row with the streaming chunks they saw.
        await persist_session.execute(
            sa.text("UPDATE team_messages SET id = :new_id WHERE id = :old_id"),
            {"new_id": str(message_id), "old_id": str(agent_msg.id)},
        )
        await persist_session.commit()

    # Final stream_end frame — clients seal the bubble.
    await centrifugo_client.publish(
        channel=f"team:{team_id}",
        data={
            "type": "agent_stream_end",
            "message_id": str(message_id),
            "elapsed_ms": elapsed_ms,
            "token_usage": token_usage,
        },
    )

    log.info(
        "team_chat_agent.done",
        team_id=str(team_id),
        message_id=str(message_id),
        routed_via=routed_via,
        elapsed_ms=elapsed_ms,
        output_chars=len(full_text),
    )


# --- Helpers -----------------------------------------------------------------


def _build_system_prompt(*, team_slug: str) -> str:
    return SYSTEM_PROMPT_PREAMBLE.format(team_slug=team_slug)


def _selected_provider(team: Team) -> str:
    """The team's chosen fallback provider, normalised, defaulting safely.

    A row written before migration 0033, or one somehow holding a value this
    build does not implement, resolves to the default rather than to a crash. The
    column has a CHECK constraint and the route validates, so this is the third
    line of defence — but it is the one running on the path that spends money, and
    an agent turn that dies on an unexpected string is a worse outcome than one
    that answers on the documented default.
    """
    return team_keys.normalize_provider(
        getattr(team, "agent_provider", None)
    ) or team_keys.DEFAULT_PROVIDER


def _answering_model(*, provider: str, has_promax: bool) -> str:
    """The model that will actually produce this turn's answer.

    The Pro/Max path is claude.ai through the extension and the request body pins
    MODEL_SONNET, so that is the honest name there — whatever the team selected
    for its FALLBACK, which this turn is not using. Every other turn names the
    model the selected provider is configured to answer with.
    """
    if has_promax:
        return MODEL_SONNET
    return team_keys.default_model_for(provider)


def _no_route_for(provider: str) -> Exception:
    """The right kind of "nothing to run on" for this provider.

    The DEFAULT provider is not a choice anybody made, so a team that never
    touched the setting gets the sentence it has always got — one that explains
    the subscription as well as the key, because for Claude both are real routes.

    A team that DID choose gets its choice named back, so the remedy points at the
    right settings field instead of at a generic absence.
    """
    if provider == team_keys.DEFAULT_PROVIDER:
        return AgentRouteUnavailable()
    return ProviderKeyMissing(provider)


def _format_memory_block(bundle_str: str) -> str:
    return f"## Team memory snapshot\n\n{bundle_str}\n"


def _format_chat_history(messages: list[TeamMessage]) -> str:
    if not messages:
        return "## Recent chat\n\n(no recent messages)\n"
    lines = ["## Recent chat (oldest first)", ""]
    for m in messages:
        if m.kind == "user":
            who = f"user[{m.author_user_id}]"
        else:
            who = f"agent[{m.agent_name or '?'}]"
        snippet = (m.content or "").strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200].rstrip() + "…"
        lines.append(f"{who}: {snippet}")
    return "\n".join(lines) + "\n"


async def _user_has_live_bridge(user_sub: str) -> bool:
    """Does session-bridge hold a live WebSocket for this user, right now?

    Asked, not inferred. This used to read `user_external_sessions.last_seen_at`
    and call the bridge alive if the row was under 90 seconds old — a guess in
    both directions, and one that failed in the expensive direction: a heartbeat
    stopped being written while the socket stayed up, the row went thirty
    minutes stale, and every mention routed to a fallback key with no credit.

    Widening that window would have been the wrong repair. It does not make a
    live bridge easier to find; it makes a dead one look alive for longer,
    turning a fast honest failure into a slow one.

    The bridge holds the sockets in memory, so it is the authority. An
    unreachable bridge answers False: if this hop cannot be made, neither can
    the streaming one right behind it, so there is no live subscription to route
    through whatever a timestamp might have claimed.

    NOTE ON DEVICE. The bridge keys sockets by USER, never by device. A phone
    with no extension routes through whatever browser that person has open
    somewhere. There is deliberately no "is there a bridge HERE" question in
    this codebase — it would have no consumer and would invite copy that tells
    a phone user their subscription is unavailable when it is answering fine.
    """
    try:
        token = _sign_bridge_jwt_acting(user_sub, ttl_s=30)
        async with httpx.AsyncClient(timeout=BRIDGE_STATUS_TIMEOUT_S) as client:
            r = await client.get(
                f"{BRIDGE_BASE_URL}/v1/internal/bridge-status/{quote(user_sub, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code != 200:
            log.info(
                "team_chat_agent.bridge_status.non_200",
                status=r.status_code,
            )
            return False
        return bool(r.json().get("live"))
    except Exception as e:  # noqa: BLE001
        log.info("team_chat_agent.bridge_status.unreachable", err=str(e))
        return False


def _sign_bridge_jwt_acting(acting_user_sub: str, ttl_s: int = 120) -> str:
    """Sign a short-lived bridge JWT with `acting_user_sub` for /v1/chat."""
    now = int(time.time())
    payload = {
        "iss": "memory-api",
        "sub": "team-chat-agent",
        "scope": "bridge",
        "acting_user_sub": acting_user_sub,
        "iat": now,
        "exp": now + ttl_s,
    }
    tok = jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    )
    if isinstance(tok, bytes):
        tok = tok.decode()
    return tok


# --- URL pre-fetch helpers (260601-3is) --------------------------------------

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_TRAILING_PUNCT = set(".,;:!?)]}\"'>")
_MAX_FETCHED_CHARS = 6000
_MAX_URLS = 3

# HOW FAR BACK A LINK COUNTS AS "the one being asked about".
#
# The pre-fetch used to read the summoning message and nothing else, which is
# exactly as written and wrong in practice: pasting a link and THEN asking about
# it is the normal way people use a chat. Nobody composes
# "@agent what is https://… about" in one breath. A real transcript —
#
#   23:15  someone   https://pitch.example/
#   23:18  someone   @agent When is it and where?
#
# — fetched nothing at all, and the agent then answered about a page it had never
# seen.
#
# Both bounds exist to stop the opposite error. Re-fetching a link from last week
# because it is still in the history buffer is a different bug, and one that
# spends a scraper call plus 6000 characters of context on something nobody
# asked about. Ten messages is comfortably wider than the "paste, then ask"
# pattern; thirty minutes is a conversation, not a backlog.
_URL_LOOKBACK_MESSAGES = 10
_URL_LOOKBACK_S = 30 * 60


def _extract_urls(text: str) -> list[str]:
    """Extract up to 3 unique http(s) URLs from text, stripping trailing punctuation."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip("".join(_TRAILING_PUNCT))
        if url not in seen:
            seen.add(url)
            result.append(url)
            if len(result) >= _MAX_URLS:
                break
    return result


def _within_lookback(anchor: datetime | None, created: datetime | None) -> bool:
    """Is `created` recent enough relative to the summoning message?

    Unknown timestamps are treated as in-window: the message-count bound still
    applies, and dropping a link because a row had no `created_at` would fail in
    the direction of not answering.
    """
    if anchor is None or created is None:
        return True
    try:
        return (anchor - created).total_seconds() <= _URL_LOOKBACK_S
    except TypeError:
        # Mixed naive/aware datetimes. Both come from the same TIMESTAMPTZ column
        # so this should not happen; falling back to "in window" keeps a turn
        # working rather than killing it over a comparison.
        return True


def _recent_urls(*, triggering_message: TeamMessage, recent: list[TeamMessage]) -> list[str]:
    """The links this mention is plausibly about, newest first, capped at 3.

    Scans backwards from the summoning message through the recent window the
    agent already loaded for context, so a link pasted a moment earlier is found.
    Newest first, because when a thread carries more links than the cap the newest
    are the ones being asked about.

    HUMAN messages only. The agent's own replies quote links it produced, and
    fetching those would let one hallucinated URL turn into a fetch, then into
    context, then into the next answer.
    """
    anchor = getattr(triggering_message, "created_at", None)
    ordered: list[TeamMessage] = [triggering_message]
    ordered += [
        m for m in reversed(recent) if getattr(m, "id", None) != triggering_message.id
    ]

    picked: list[str] = []
    seen: set[str] = set()
    for examined, message in enumerate(ordered):
        if examined >= _URL_LOOKBACK_MESSAGES:
            break
        if getattr(message, "kind", None) != "user":
            continue
        if not _within_lookback(anchor, getattr(message, "created_at", None)):
            # Ordered newest-first, so everything past here is older still.
            break
        for url in _extract_urls(message.content or ""):
            if url in seen:
                continue
            seen.add(url)
            picked.append(url)
            if len(picked) >= _MAX_URLS:
                return picked
    return picked


async def _fetch_url_via_scraper(url: str, team_scope: str) -> str | None:
    """POST to mcp-gateway scraper and return the text, capped at 6000 chars.

    Reuses _sign_bridge_jwt_acting for the bridge JWT (no team_scope claim in
    bridge JWTs, so we also send X-Team-Scope header — gateway falls back to it).
    Uses the real tool name "scrape" so it works even without Task 1 gateway fix.
    Fail-soft: any error returns None.
    """
    try:
        jwt_token = _sign_bridge_jwt_acting("team-chat-agent")
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{settings.MCP_GATEWAY_URL}/tools/scraper/call",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "X-Team-Scope": team_scope,
                    "Content-Type": "application/json",
                },
                json={"name": "scrape", "arguments": {"url": url}},
            )
        data = r.json()
        if isinstance(data, dict) and data.get("isError"):
            return None
        texts = [
            c.get("text", "")
            for c in (data.get("content") or [])
            if isinstance(c, dict) and c.get("text")
        ]
        joined = "\n".join(texts)
        if not joined:
            return None
        return joined[:_MAX_FETCHED_CHARS]
    except Exception as exc:  # noqa: BLE001
        log.warning("team_chat_agent.fetch_failed", url=url[:100], error=str(exc))
        return None


# The exact words the model reads when nothing was fetched. It is a STATEMENT,
# not an omission, and that is the whole point: a prompt that simply leaves the
# section out invites the model to imagine one. The turn this replaces opened
# with "I was able to fetch the URL from your message" and contradicted itself
# three sentences later — someone reading only the first line believes the page
# was read.
NO_WEB_CONTENT_MARKER = (
    "(no web page content was fetched for this turn — you have NOT seen any "
    "linked page)"
)


async def _build_fetched_web_block(urls: list[str], team_scope: str) -> str:
    """Fetch each URL via the scraper and render the block the model reads.

    ALWAYS returns a section, even with nothing to put in it. Each URL gets its
    own heading; a failed fetch renders as "(could not fetch this URL)" and never
    stops the turn, because an unreachable page is worth less than an answer
    about everything else the person asked.
    """
    sections: list[str] = ["\n\n## Fetched web content"]
    if not urls:
        sections.append(f"\n{NO_WEB_CONTENT_MARKER}\n")
        return "".join(sections)
    for url in urls:
        content = await _fetch_url_via_scraper(url, team_scope)
        sections.append(f"\n### {url}\n{content or '(could not fetch this URL)'}\n")
    return "".join(sections)


# --- Streaming via Pro/Max bridge --------------------------------------------


def _build_system_blocks(system_prompt: str, cached_memory_block: str) -> list[dict[str, Any]]:
    """Compose the system block list with up to 3 cache_control breakpoints:
      1. Preamble (no cache — small, role description with team slug)
      2. Product KB (CACHED — byte-stable across all teams and mentions)
      3. Team memory bundle (CACHED — 5-min TTL per team)

    Anthropic accepts up to 4 cache_control breakpoints per request, and they
    are matched as prefix segments. Putting the KB BEFORE the team memory
    means cross-team mentions still hit the KB cache even when the team
    memory differs.
    """
    blocks: list[dict[str, Any]] = [{"type": "text", "text": system_prompt}]
    if _PRODUCT_KB:
        blocks.append({
            "type": "text",
            "text": f"## xbrain product knowledge base\n\n{_PRODUCT_KB}",
            "cache_control": {"type": "ephemeral"},
        })
    blocks.append({
        "type": "text",
        "text": cached_memory_block,
        "cache_control": {"type": "ephemeral"},
    })
    return blocks


async def _stream_via_promax(
    *,
    triggering_user_sub: str,
    system_prompt: str,
    cached_memory_block: str,
    chat_history_block: str,
):
    """POST bridge /v1/chat/completions with a bridge JWT acting_user_sub.

    The bridge looks up the user's WS pool entry and routes the request via
    their claude.ai WebSocket. Yields (chunk_text, usage_dict) tuples.
    """
    bridge_jwt = _sign_bridge_jwt_acting(triggering_user_sub)
    user_message = chat_history_block  # last message in chat_history IS the @groove mention
    # The Pro/Max path streams through claude.ai (via the extension), which
    # expects a plain-string system message — it does NOT honor Anthropic
    # `cache_control` blocks. Flatten the cached system blocks to text so the
    # extension doesn't coerce a block array to "[object Object]".
    system_text = "\n\n".join(
        b["text"]
        for b in _build_system_blocks(system_prompt, cached_memory_block)
        if b.get("text")
    )
    body = {
        "model": MODEL_SONNET,
        "stream": True,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_message},
        ],
    }
    bridge_url = f"{BRIDGE_BASE_URL}/v1/chat/completions"

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            bridge_url,
            json=body,
            headers={
                "Authorization": f"Bearer {bridge_jwt}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
        ) as response:
            if response.status_code == 503:
                # The bridge answers 503 when the user's socket is not in its
                # pool. We only got here because it said the socket WAS there,
                # so it went away between the two calls — a real state, and one
                # the person can act on. Read as a generic outage it used to say
                # "worth trying again" with no hint of what to try.
                await response.aread()
                raise BridgeSessionLost()
            if response.status_code >= 400:
                err_body = await response.aread()
                raise RuntimeError(
                    f"bridge route failed: HTTP {response.status_code} "
                    f"{err_body.decode(errors='replace')[:200]}"
                )
            # "The socket is alive" and "the socket returned an answer" are
            # different claims, and only the first is checked before we get here.
            # These three counters make the difference visible in one log line
            # WITHOUT reading a body: lines that arrived at all, lines that looked
            # like SSE data, and payloads that parsed into JSON. The failure this
            # is aimed at is the one docs/session-bridge-guide.md §9 records —
            # claude.ai changed its delimiter to CRLF, a client split on "\n\n",
            # zero blocks parsed, 200 OK, empty reply. That shows up here as
            # `lines` high and `parsed` zero, which is a diagnosis rather than a
            # mystery.
            lines_seen = 0
            data_lines = 0
            parsed = 0
            text_deltas = 0
            async for line in response.aiter_lines():
                lines_seen += 1
                if not line.startswith("data: "):
                    continue
                data_lines += 1
                payload = line[len("data: ") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                parsed += 1
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        text_deltas += 1
                        yield (text, {})
                usage = chunk.get("usage")
                if usage:
                    yield ("", {"usage": usage})
            if text_deltas == 0:
                log.warning(
                    "team_chat_agent.bridge_stream_empty",
                    status=response.status_code,
                    lines=lines_seen,
                    data_lines=data_lines,
                    parsed=parsed,
                )


# --- Streaming via the provider the team selected ----------------------------


def _stream_via_fallback_provider(
    *,
    provider: str,
    api_key: str,
    system_prompt: str,
    cached_memory_block: str,
    chat_history_block: str,
):
    """THE one place a provider is chosen, and it is chosen exactly once.

    A single call site in the routing block is not a style preference — it is what
    makes "a refused key is never retried on another key" checkable by reading the
    function. Two provider calls in that block would be a fall-through waiting to
    be written.

    Anthropic gets its own client because its cache_control breakpoints are worth
    real money on a repeated team-memory block and have no OpenAI equivalent.
    OpenAI and xAI share one, differing only in base URL, because xAI speaks the
    OpenAI wire protocol — the same trick apps/agent-runtime already uses for its
    Grok second opinion.

    Returns the async generator rather than delegating with `async for` so a
    caller's `async for` drives the provider's stream directly, with no extra
    frame between the chunk and the publish.
    """
    model = team_keys.default_model_for(provider)
    if provider == team_keys.PROVIDER_ANTHROPIC:
        return _stream_via_anthropic_api(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            cached_memory_block=cached_memory_block,
            chat_history_block=chat_history_block,
        )
    return _stream_via_openai_compatible_api(
        api_key=api_key,
        model=model,
        base_url=team_keys.base_url_for(provider),
        system_prompt=system_prompt,
        cached_memory_block=cached_memory_block,
        chat_history_block=chat_history_block,
    )


async def _stream_via_anthropic_api(
    *,
    api_key: str,
    model: str = "",
    system_prompt: str,
    cached_memory_block: str,
    chat_history_block: str,
):
    """Fallback: call Anthropic directly with a key.

    Used when the triggering user has no live bridge. The key is passed IN
    rather than read from settings here: which key is a routing decision, made
    once by the caller, and a helper that reached for a global would silently
    ignore it. `model` is passed in for the same reason.

    The key never appears in a log line, a message, or an exception raised by
    this function.
    """
    if not api_key:
        # The caller is responsible for not getting here; this is the assertion,
        # not the user-facing path (see AgentRouteUnavailable).
        raise AgentRouteUnavailable()
    client = AsyncAnthropic(api_key=api_key)
    async with client.messages.stream(
        model=model or team_keys.default_model_for(team_keys.PROVIDER_ANTHROPIC),
        max_tokens=MAX_OUTPUT_TOKENS,
        system=_build_system_blocks(system_prompt, cached_memory_block),
        messages=[{"role": "user", "content": chat_history_block}],
    ) as stream:
        async for event in stream:
            # text_delta events carry the streaming chunks
            if event.type == "content_block_delta":
                delta = getattr(event, "delta", None)
                text = getattr(delta, "text", None) if delta else None
                if text:
                    yield (text, {})
        # Final usage available after stream completes
        final = await stream.get_final_message()
        usage = getattr(final, "usage", None)
        if usage:
            yield (
                "",
                {
                    "usage": {
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None),
                        "cache_creation_input_tokens": getattr(
                            usage, "cache_creation_input_tokens", None
                        ),
                        "cache_read_input_tokens": getattr(
                            usage, "cache_read_input_tokens", None
                        ),
                    }
                },
            )


async def _stream_via_openai_compatible_api(
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    system_prompt: str,
    cached_memory_block: str,
    chat_history_block: str,
):
    """Fallback: call an OpenAI-compatible endpoint directly with a key.

    ONE function for two providers. xAI implements the OpenAI Chat Completions
    request and SSE shapes, so Grok is this client pointed at a different host —
    verified against the pattern apps/agent-runtime/app/graphs/second_opinion.py
    has been using for its Grok call. `base_url=None` leaves the SDK on its own
    default host, which is also what lets an operator aim the OpenAI path at a
    local vLLM/LiteLLM/Ollama with one environment variable.

    Two deliberate omissions.

    The system blocks are FLATTENED to one string. Anthropic's `cache_control`
    breakpoints are Anthropic's; sending the block array here would at best be
    ignored and at worst be rendered as "[object Object]" — the same flattening
    the Pro/Max path already does for the same reason.

    `stream_options={"include_usage": True}` is NOT sent. It is the documented way
    to get token counts out of a streamed OpenAI response, but this client also
    points at xAI and at whatever an operator configured, and a parameter an
    endpoint does not implement fails the whole request. Token counts are
    diagnostic; the answer is not. So usage is read only when a provider
    volunteers it in a chunk, and its absence costs a metadata field rather than
    the reply.

    `max_tokens` (not `max_completion_tokens`) is what goes on the wire: it is the
    field every OpenAI-compatible server accepts today. OpenAI's own reasoning
    models reject it — an operator who points AGENT_MODEL_OPENAI at one gets a
    4xx, which this module reports as needing an administrator, rather than a
    silent truncation.

    The key never appears in a log line, a message, or an exception raised by
    this function.
    """
    if not api_key:
        raise AgentRouteUnavailable()
    client = AsyncOpenAI(api_key=api_key, base_url=base_url) if base_url else AsyncOpenAI(
        api_key=api_key
    )
    system_text = "\n\n".join(
        block["text"]
        for block in _build_system_blocks(system_prompt, cached_memory_block)
        if block.get("text")
    )
    stream = await client.chat.completions.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        stream=True,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": chat_history_block},
        ],
    )
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta is not None else None
            if text:
                yield (text, {})
        usage = getattr(chunk, "usage", None)
        if usage:
            # Named to match the Anthropic path's keys so a consumer does not have
            # to branch on provider to read a token count. The cache fields have
            # no equivalent here and are simply absent rather than faked as zero.
            yield (
                "",
                {
                    "usage": {
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                    }
                },
            )


# --- Catch me up — ephemeral, opt-in summary since last visit (Phase 23) ------


CATCHUP_SUMMARY_INSTRUCTION = (
    "You are catching a returning team member up on what they MISSED in this "
    "team chat since they were last here. Summarize what happened: highlight "
    "decisions made, questions directed at the returning member, and any "
    "VALIDATED/CANONICAL facts. Be concise — a tight briefing, not a transcript."
)


def _format_catchup_user_turn(messages: list[TeamMessage]) -> str:
    """Render the catch-me-up USER turn: an instruction header followed by the
    chronological since-window (reuses _format_chat_history's per-message
    rendering so the two never diverge)."""
    return f"{CATCHUP_SUMMARY_INSTRUCTION}\n\n" + _format_chat_history(messages)


async def catch_me_up(
    *,
    team_id: UUID,
    caller_user_sub: str,
    since: datetime | None,
) -> None:
    """Ephemeral, opt-in "Catch me up" summary streamed to the CALLER only.

    Reuses the EXACT @agent streaming path (brain bundle + _stream_via_promax /
    _stream_via_fallback_provider, so it honours the team's selected provider and
    its unavailability without a second copy of the routing rules) but:
      - streams to the caller's PRIVATE `user:<caller_sub>` Centrifugo channel,
        NEVER `team:<id>` (D-23-04);
      - persists NOTHING — it deliberately never inserts an agent-message row,
        so the summary is never a `team_messages` row everyone sees
        (D-23-05, T-23-06);
      - is bounded to CATCHUP_MAX_MESSAGES and filters out the caller's OWN
        messages so the gathered window agrees with the unread count that gated
        it ("catch me up on what you MISSED", not what you sent).

    Never raises — logs on failure (mirrors handle_claude_mention). Invoked via
    asyncio.create_task() from POST /catch-me-up AFTER membership + rate-limit +
    non-empty-window gates.
    """
    channel = f"user:{caller_user_sub}"
    message_id = uuid.uuid4()
    try:
        async with async_session_factory() as session:
            team = (
                await session.execute(select(Team).where(Team.id == team_id))
            ).scalar_one_or_none()
            if team is None:
                log.warning("team_chat_agent.catchup.team_missing", team_id=str(team_id))
                return

            # Gather the since-window (newest-first, capped) then reverse to
            # chronological (mirror get_recent_messages_chronological). A None
            # `since` (first visit) returns the latest CATCHUP_MAX_MESSAGES.
            msgs = await tm_repo.list_messages(
                session,
                team_id=team.id,
                after_created_at=since,
                limit=settings.CATCHUP_MAX_MESSAGES,
            )
            msgs.reverse()

            # Filter out the caller's OWN messages so the summary window agrees
            # with count_unread_since's exclude_user_id. Resolve the caller's
            # user.id from their sub (agent frames have a NULL author_user_id and
            # so are never dropped here).
            caller = (
                await session.execute(
                    select(User).where(User.source_user_id == caller_user_sub)
                )
            ).scalar_one_or_none()
            if caller is not None:
                msgs = [m for m in msgs if m.author_user_id != caller.id]

            # Empty window → one "nothing new" end frame, NO LLM spend.
            if not msgs:
                await centrifugo_client.publish(
                    channel=channel,
                    data={
                        "type": "catchup_stream_end",
                        "message_id": str(message_id),
                        "note": "nothing new since your last visit",
                    },
                )
                return

            # Brain bundle — TEAM-SCOPED (the isolation boundary, T-23-02): a
            # different team's messages/facts are structurally unreachable.
            bundle = await team_context_cache.get_team_memory_bundle(
                session, team_scope=team.slug, team_id=team.id
            )

        # Build prompts by REUSING the module helpers (no duplicated streaming).
        system_prompt = _build_system_prompt(team_slug=team.slug)
        cached_memory_block = _format_memory_block(bundle["bundle"])
        chat_history_block = _format_catchup_user_turn(msgs)

        # Route exactly like the @agent path — including the team's selected
        # provider. A summary that quietly answered on a different vendor than
        # every other agent turn would bill the team for a provider they did not
        # choose, on the one path that persists nothing to explain it afterwards.
        provider = _selected_provider(team)
        has_promax = await _user_has_live_bridge(caller_user_sub)
        fallback = (
            FallbackKey(key=None, tier=team_keys.TIER_NONE)
            if has_promax
            else await team_keys.resolve_fallback_key(
                team_id=team.id, provider=provider
            )
        )
        routed_via = "user_promax" if has_promax else _ROUTED_VIA_BY_TIER[fallback.tier]
        agent_name = _answering_model(provider=provider, has_promax=has_promax)

        started_at = time.monotonic()
        log.info(
            "team_chat_agent.catchup.start",
            team_id=str(team_id),
            caller_user_sub=caller_user_sub,
            routed_via=routed_via,
            provider=provider,
            agent_name=agent_name,
            message_id=str(message_id),
            window=len(msgs),
        )

        # Stream start → the caller's OWN channel only.
        await centrifugo_client.publish(
            channel=channel,
            data={
                "type": "catchup_stream_start",
                "message_id": str(message_id),
                "agent_name": agent_name,
                "provider": provider,
                "routed_via": routed_via,
            },
        )

        try:
            if has_promax:
                stream = _stream_via_promax(
                    triggering_user_sub=caller_user_sub,
                    system_prompt=system_prompt,
                    cached_memory_block=cached_memory_block,
                    chat_history_block=chat_history_block,
                )
            elif fallback.key:
                stream = _stream_via_fallback_provider(
                    provider=provider,
                    api_key=fallback.key,
                    system_prompt=system_prompt,
                    cached_memory_block=cached_memory_block,
                    chat_history_block=chat_history_block,
                )
            else:
                raise _no_route_for(provider)
            produced_any = False
            async for chunk_text, _usage in stream:
                if chunk_text:
                    produced_any = True
                    await centrifugo_client.publish(
                        channel=channel,
                        data={
                            "type": "catchup_stream_chunk",
                            "message_id": str(message_id),
                            "delta": chunk_text,
                        },
                    )
            if not produced_any:
                # Same condition, same verdict as the @agent path. This one has no
                # persisted row to inspect afterwards, so without it a silent
                # "Summarizing…" simply resolves to an empty panel and the person
                # is left guessing whether there was nothing to say.
                raise EmptyAnswer(via_subscription=has_promax)
        except Exception as e:  # noqa: BLE001
            # Rule 1: this published `str(e)[:200]` — the provider's own words,
            # straight into a frame a person reads. It is the same leak the
            # @agent path was fixed for, on a summary nobody thought of, and it
            # would now render an empty bubble for the routing exceptions, whose
            # str() is deliberately blank. One classifier for both paths.
            failure = classify_stream_failure(_as_team_key_failure(e, fallback))
            log.warning(
                "team_chat_agent.catchup.stream_error",
                team_id=str(team_id),
                failure_code=failure["code"],
                err=str(e),  # the raw text lives HERE, for an operator
                err_type=type(e).__name__,
            )
            await centrifugo_client.publish(
                channel=channel,
                data={
                    "type": "catchup_stream_error",
                    "message_id": str(message_id),
                    "error": failure["message"],
                    "code": failure["code"],
                    "retryable": failure["retryable"],
                },
            )

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        # EPHEMERAL by construction: deliberately no agent-message row is
        # persisted and NO `team:<id>` publish is made — the summary is never a
        # row everyone sees (D-23-04/05, T-23-06). Only the caller's own user
        # channel gets a closing frame.
        await centrifugo_client.publish(
            channel=channel,
            data={
                "type": "catchup_stream_end",
                "message_id": str(message_id),
                "elapsed_ms": elapsed_ms,
            },
        )
        log.info(
            "team_chat_agent.catchup.done",
            team_id=str(team_id),
            message_id=str(message_id),
            elapsed_ms=elapsed_ms,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "team_chat_agent.catchup.error",
            team_id=str(team_id),
            err=str(e),
        )
        # WR-02: a failure BEFORE the stream started (team/caller lookup, message
        # gathering, brain bundle) would otherwise leave the client stuck on its
        # "Summarizing…" placeholder forever with no error. Best-effort publish a
        # terminal error + end frame to the caller's own channel so the panel
        # resolves. Never raises (the function must not break the request path).
        try:
            await centrifugo_client.publish(
                channel=channel,
                data={
                    "type": "catchup_stream_error",
                    "message_id": str(message_id),
                    "error": "summary failed",
                },
            )
            await centrifugo_client.publish(
                channel=channel,
                data={"type": "catchup_stream_end", "message_id": str(message_id)},
            )
        except Exception:  # noqa: BLE001
            pass

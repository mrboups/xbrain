"""Team chat @claude mention handler — Pro/Max routing + Anthropic fallback.

Quick task 260512-tcr Wave 2.5. When memory-api detects a `@claude` mention
in a posted team message, this handler runs in a background task:

  1. Resolve the triggering user via source_user_id.
  2. Probe the session-bridge to see if the user has a live WS connection.
     - If yes: route the chat via the user's Pro/Max subscription (sign a
       bridge JWT with `acting_user_sub=<user>` and POST bridge
       /v1/chat/completions). Cost = 0 to xbrain.
     - If no: fall back to Anthropic SDK with the team API key. Cost
       absorbed by xbrain.
  3. Stream tokens to Centrifugo channel `team:<team_id>` so all subscribed
     clients (extension + future PWA) see Claude typing in realtime.
  4. Persist the final reply as one `team_messages` row with kind='agent',
     agent_name='claude-sonnet-4-6', routed_via='user_promax' | 'team_api'.

The Anthropic prompt is built around a CACHED memory block (5min TTL via
team_context_cache) so subsequent mentions in the same window get
~90% input cost reduction from Anthropic's ephemeral cache.

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
from uuid import UUID

import httpx
import sqlalchemy as sa
import structlog
from anthropic import AsyncAnthropic
from authlib.jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.models.team import Team
from app.models.team_message import TeamMessage
from app.models.user import User
from app.repos import team_messages as tm_repo
from app.services import centrifugo_client, team_context_cache

log = structlog.get_logger(__name__)


MODEL_SONNET = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 4000

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

# Failure codes are OURS, not the provider's. They exist so a client can style or
# count outcomes without parsing a sentence, and so the sentence can be reworded
# without breaking anything downstream.
FAILURE_CODE_TIMEOUT = "timeout"
FAILURE_CODE_UNAVAILABLE = "unavailable"
FAILURE_CODE_CONFIGURATION = "configuration"


def classify_stream_failure(exc: BaseException) -> dict[str, Any]:
    """Turn any streaming exception into a safe {code, message, retryable}.

    Classification reads the exception TYPE and, for HTTP errors, the status
    class — never the response body, which is where a provider puts the account
    details. 4xx is the server's own configuration (a key, a quota, a malformed
    request): the team cannot fix it and retrying repeats it. Everything else is
    treated as transient, which is the safe direction — telling somebody to try
    again costs a retry, while telling them not to bother hides an outage that
    would have cleared.
    """
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return {
            "code": FAILURE_CODE_TIMEOUT,
            "message": AGENT_FAILURE_TIMEOUT,
            "retryable": True,
        }

    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
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
SYSTEM_PROMPT_PREAMBLE = (
    "You are Claude, embedded in the xbrain team chat for team {team_slug}. "
    "Answer based on the product knowledge base below, the team's memory "
    "items, and the recent chat history. Be concise, factual, and reference "
    "specific items when relevant. If you don't know, say so — do not "
    "hallucinate."
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

    # Pre-fetch URLs mentioned in the triggering message and append to the
    # (uncached) user turn so @claude can read linked pages. Fail-soft.
    web_block = await _build_fetched_web_block(
        triggering_message.content or "", team.slug
    )
    if web_block:
        chat_history_block = chat_history_block + web_block
        log.info(
            "team_chat_agent.web_prefetch",
            team_id=str(team_id),
            urls=web_block.count("### "),
        )

    # Decide routing.
    has_promax = await _user_has_live_bridge(triggering_user_sub)
    routed_via = "user_promax" if has_promax else "team_api"

    message_id = uuid.uuid4()
    started_at = time.monotonic()
    log.info(
        "team_chat_agent.start",
        team_id=str(team_id),
        triggering_user_sub=triggering_user_sub,
        routed_via=routed_via,
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
            "agent_name": MODEL_SONNET,
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
        else:
            async for chunk_text, usage in _stream_via_anthropic_api(
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
    partial_text = "".join(full_text_parts).strip()
    if failure is None:
        full_text = partial_text or "(empty response)"
    elif partial_text:
        full_text = partial_text
    else:
        full_text = failure["message"]

    elapsed_ms = int((time.monotonic() - started_at) * 1000)

    # Persist the agent message. We re-open a fresh session so the previous
    # rollback (if any) doesn't poison the insert.
    metadata: dict[str, Any] = {
        "elapsed_ms": elapsed_ms,
        "token_usage": token_usage,
        "triggering_user_sub": triggering_user_sub,
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
            agent_name=MODEL_SONNET,
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
    """Best-effort probe: does the session-bridge currently hold a live WS
    for this user?

    For v1 we read the persisted user_external_sessions row — if last_seen_at
    is within the last 90 seconds we assume the WS is alive (extension pings
    every 20s + 60s grace). Phase 2 may add a dedicated bridge healthz
    endpoint for synchronous truth.
    """
    try:
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        """
                        SELECT s.last_seen_at
                        FROM user_external_sessions s
                        JOIN users u ON u.id = s.user_id
                        WHERE u.source_user_id = :sub
                          AND s.provider = 'claude'
                          AND s.last_seen_at > now() - interval '90 seconds'
                        LIMIT 1
                        """
                    ),
                    {"sub": user_sub},
                )
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
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


async def _build_fetched_web_block(text: str, team_scope: str) -> str:
    """Extract URLs from text, fetch each via scraper, return formatted block.

    Returns "" if no URLs found. Each URL gets its own section under
    "## Fetched web content". Failed fetches render as "(could not fetch this URL)".
    """
    urls = _extract_urls(text)
    if not urls:
        return ""
    sections: list[str] = ["\n\n## Fetched web content"]
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
    bridge_url = "http://session-bridge:8105/v1/chat/completions"

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
            if response.status_code >= 400:
                err_body = await response.aread()
                raise RuntimeError(
                    f"bridge route failed: HTTP {response.status_code} "
                    f"{err_body.decode(errors='replace')[:200]}"
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield (text, {})
                usage = chunk.get("usage")
                if usage:
                    yield ("", {"usage": usage})


# --- Streaming via direct Anthropic SDK fallback -----------------------------


async def _stream_via_anthropic_api(
    *,
    system_prompt: str,
    cached_memory_block: str,
    chat_history_block: str,
):
    """Fallback: call Anthropic directly with the team API key.

    Used when the triggering user doesn't have a live Pro/Max bridge.
    Cost goes to xbrain's Anthropic budget.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not configured — cannot fall back from Pro/Max"
        )
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    async with client.messages.stream(
        model=MODEL_SONNET,
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
    _stream_via_anthropic_api) but:
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

        # Route exactly like the @agent path.
        has_promax = await _user_has_live_bridge(caller_user_sub)
        routed_via = "user_promax" if has_promax else "team_api"

        started_at = time.monotonic()
        log.info(
            "team_chat_agent.catchup.start",
            team_id=str(team_id),
            caller_user_sub=caller_user_sub,
            routed_via=routed_via,
            message_id=str(message_id),
            window=len(msgs),
        )

        # Stream start → the caller's OWN channel only.
        await centrifugo_client.publish(
            channel=channel,
            data={
                "type": "catchup_stream_start",
                "message_id": str(message_id),
                "agent_name": MODEL_SONNET,
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
            else:
                stream = _stream_via_anthropic_api(
                    system_prompt=system_prompt,
                    cached_memory_block=cached_memory_block,
                    chat_history_block=chat_history_block,
                )
            async for chunk_text, _usage in stream:
                if chunk_text:
                    await centrifugo_client.publish(
                        channel=channel,
                        data={
                            "type": "catchup_stream_chunk",
                            "message_id": str(message_id),
                            "delta": chunk_text,
                        },
                    )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "team_chat_agent.catchup.stream_error",
                team_id=str(team_id),
                err=str(e),
            )
            await centrifugo_client.publish(
                channel=channel,
                data={
                    "type": "catchup_stream_error",
                    "message_id": str(message_id),
                    "error": str(e)[:200],
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

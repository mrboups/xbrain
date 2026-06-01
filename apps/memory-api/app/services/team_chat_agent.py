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
from app.repos import team_messages as tm_repo
from app.services import centrifugo_client, team_context_cache

log = structlog.get_logger(__name__)


MODEL_SONNET = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 4000
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
        # Surface the error in the chat AND log. The partial text gets persisted
        # if we have any so the team can see what came through.
        error_msg = f"(Claude stream interrupted: {e})"
        log.warning(
            "team_chat_agent.stream_error",
            team_id=str(team_id),
            routed_via=routed_via,
            err=str(e),
            partial_len=sum(len(p) for p in full_text_parts),
        )
        await centrifugo_client.publish(
            channel=f"team:{team_id}",
            data={
                "type": "agent_stream_error",
                "message_id": str(message_id),
                "error": str(e)[:200],
            },
        )
        if not full_text_parts:
            full_text_parts.append(error_msg)

    full_text = "".join(full_text_parts).strip() or "(empty response)"
    elapsed_ms = int((time.monotonic() - started_at) * 1000)

    # Persist the agent message. We re-open a fresh session so the previous
    # rollback (if any) doesn't poison the insert.
    async with async_session_factory() as persist_session:
        agent_msg = await tm_repo.insert_agent_message(
            persist_session,
            team_id=team_id,
            agent_name=MODEL_SONNET,
            content=full_text,
            routed_via=routed_via,
            parent_message_id=triggering_message_id,
            metadata={
                "elapsed_ms": elapsed_ms,
                "token_usage": token_usage,
                "triggering_user_sub": triggering_user_sub,
                "memory_cached": bundle["cached"],
                "memory_items": bundle["item_count"],
            },
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
    user_message = chat_history_block  # last message in chat_history IS the @claude mention
    body = {
        "model": MODEL_SONNET,
        "stream": True,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {
                "role": "system",
                "content": _build_system_blocks(system_prompt, cached_memory_block),
            },
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

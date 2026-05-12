"""/v1/teams/{team_id}/messages — team chat realtime endpoints.

Quick task 260512-tcr Wave 2.3:
  POST /v1/teams/{team_id}/messages
      → insert user message + Centrifugo publish + maybe enqueue agent task
  GET  /v1/teams/{team_id}/messages?before=<iso>&limit=50
      → paginated history (newest first), excluding deleted_at IS NOT NULL
  POST /v1/me/centrifugo-token
      → issue HS256-signed client JWT scoped to the caller's team channels
  GET  /v1/teams/{team_id}/agent-context-bundle
      → internal endpoint hit by agent-runtime; requires kind=bridge

Auth model:
  - user-facing endpoints accept kind=user OR kind=user_api_token, with team
    membership checked (T-09-04-02 isolation pattern reused).
  - agent-context-bundle accepts kind=bridge ONLY — gates the team memory
    context behind the BRIDGE_SHARED_SECRET so untrusted code can't dump
    a team's memory.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_principal, get_session
from app.models.team import Team
from app.repos import team_messages as tm_repo
from app.repos import teams as teams_repo
from app.services import centrifugo_client, mention_detector, team_context_cache

log = structlog.get_logger(__name__)
router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────


def _require_user_principal(principal: dict[str, Any]):
    kind = principal.get("kind")
    if kind not in ("user", "user_api_token"):
        raise HTTPException(403, "user-only endpoint")
    user = principal.get("user")
    if user is None:
        raise HTTPException(403, "user principal missing user object")
    return user


async def _resolve_team_and_check_membership(
    session: AsyncSession,
    user_id: UUID,
    team_id: UUID,
) -> Team:
    team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise HTTPException(404, "team not found")
    membership = await teams_repo.get_membership(session, team_id=team_id, user_id=user_id)
    if membership is None:
        raise HTTPException(403, "not a member of this team")
    return team


def _serialize_message(m) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "team_id": str(m.team_id),
        "author_user_id": str(m.author_user_id) if m.author_user_id else None,
        "agent_name": m.agent_name,
        "kind": m.kind,
        "content": m.content,
        "created_at": m.created_at.isoformat(),
        "routed_via": m.routed_via,
        "metadata": m.metadata_ or {},
        "parent_message_id": str(m.parent_message_id) if m.parent_message_id else None,
        "edited_at": m.edited_at.isoformat() if m.edited_at else None,
    }


# ── /v1/me/centrifugo-token ──────────────────────────────────────────────────


@router.post("/me/centrifugo-token")
async def issue_centrifugo_token(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Issue an HS256-signed Centrifugo client token for the caller.

    The token grants subscribe rights to:
      - team:<team_id> for every team the user belongs to
      - user:<source_user_id> for direct notifications (Phase 2)

    Centrifugo enforces the channels claim server-side — attempting to
    subscribe to another channel is rejected without us doing anything.
    """
    user = _require_user_principal(principal)
    teams = await teams_repo.get_all_teams_for_user(session, user_id=user.id)
    channels = [f"team:{t.id}" for t in teams]
    channels.append(f"user:{user.source_user_id}")
    return centrifugo_client.issue_client_token(
        user_sub=user.source_user_id,
        user_id=user.id,
        display_name=getattr(user, "display_name", None),
        email=getattr(user, "email", None),
        channels=channels,
    )


# ── /v1/teams/{team_id}/messages — history ───────────────────────────────────


@router.get("/teams/{team_id}/messages")
async def list_team_messages(
    team_id: UUID,
    before: datetime | None = Query(None, description="ISO timestamp — return messages older than this"),
    limit: int = Query(50, ge=1, le=200),
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return paginated history for a team, newest first.

    The extension calls this with no `before` for the initial 50, then
    with `before=<oldest.created_at>` for each scroll-up page.
    """
    user = _require_user_principal(principal)
    await _resolve_team_and_check_membership(session, user.id, team_id)
    messages = await tm_repo.list_messages(
        session, team_id=team_id, before_created_at=before, limit=limit
    )
    return {
        "messages": [_serialize_message(m) for m in messages],
        "next_before": messages[-1].created_at.isoformat() if messages else None,
    }


# ── /v1/teams/{team_id}/messages — post ──────────────────────────────────────


class PostMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(..., min_length=1, max_length=16000)
    parent_message_id: UUID | None = None  # Phase 2 threads — accepted now, ignored by readers


@router.post("/teams/{team_id}/messages", status_code=201)
async def post_team_message(
    team_id: UUID,
    body: PostMessageBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Insert a user message, publish to Centrifugo, optionally trigger Claude.

    Flow:
      1. Authorize (user kind, team member)
      2. INSERT team_messages row (kind='user')
      3. COMMIT (so the row exists before we publish — readers loading
         history get a consistent view)
      4. Centrifugo publish to team:<id> — fire-and-forget, doesn't block
         the response
      5. Mention detection — if @claude/@c/@cl found, fire-and-forget POST
         to agent-runtime
      6. Return the serialized message
    """
    user = _require_user_principal(principal)
    await _resolve_team_and_check_membership(session, user.id, team_id)

    msg = await tm_repo.insert_user_message(
        session,
        team_id=team_id,
        author_user_id=user.id,
        content=body.content,
        parent_message_id=body.parent_message_id,
    )
    await session.commit()
    payload = _serialize_message(msg)

    # Realtime fan-out — never block the response.
    asyncio.create_task(
        centrifugo_client.publish(
            channel=f"team:{team_id}",
            data={"type": "message", "message": payload},
        )
    )

    # @claude mention → enqueue agent task.
    mention = mention_detector.detect(body.content)
    if mention is not None:
        asyncio.create_task(
            _enqueue_agent_task(
                team_id=team_id,
                triggering_message_id=msg.id,
                triggering_user_sub=user.source_user_id,
                agent_name=mention["agent_name"],
            )
        )
        log.info(
            "team_chat.mention.enqueued",
            team_id=str(team_id),
            triggering_user_sub=user.source_user_id,
            agent=mention["agent_name"],
        )

    return payload


async def _enqueue_agent_task(
    *,
    team_id: UUID,
    triggering_message_id: UUID,
    triggering_user_sub: str,
    agent_name: str,
) -> None:
    """Fire-and-forget POST to agent-runtime. Logs failures, never raises."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{settings.AGENT_RUNTIME_INTERNAL_URL}/v1/agents/team_chat_mention/run",
                json={
                    "team_id": str(team_id),
                    "triggering_message_id": str(triggering_message_id),
                    "triggering_user_sub": triggering_user_sub,
                    "agent_name": agent_name,
                },
            )
        if r.status_code >= 400:
            log.warning(
                "team_chat.mention.dispatch_failed",
                status=r.status_code,
                body=r.text[:200],
            )
    except Exception as e:  # noqa: BLE001
        log.warning("team_chat.mention.dispatch_error", err=str(e))


# ── /v1/teams/{team_id}/agent-context-bundle — internal (bridge JWT) ─────────


@router.get("/teams/{team_id}/agent-context-bundle")
async def get_agent_context_bundle(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the cached team memory bundle + last 20 chat messages.

    Hit by agent-runtime just before calling Anthropic. Auth: bridge JWT
    only (kind=bridge) — gates the team memory dump behind
    BRIDGE_SHARED_SECRET.

    Response shape:
      {
        "memory_block": "<markdown bullet list>",
        "memory_item_count": int,
        "memory_cached": bool,
        "last_messages": [serialized_message, ...],     # oldest first
        "team": {"id", "slug", "display_name"}
      }
    """
    if principal.get("kind") != "bridge":
        raise HTTPException(403, "internal endpoint — bridge JWT required")
    team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise HTTPException(404, "team not found")

    bundle = await team_context_cache.get_team_memory_bundle(
        session, team_scope=team.slug, team_id=team.id
    )
    recent = await tm_repo.get_recent_messages_chronological(
        session, team_id=team.id, limit=20
    )
    return {
        "memory_block": bundle["bundle"],
        "memory_item_count": bundle["item_count"],
        "memory_cached": bundle["cached"],
        "last_messages": [_serialize_message(m) for m in recent],
        "team": {
            "id": str(team.id),
            "slug": team.slug,
            "display_name": team.display_name,
        },
    }

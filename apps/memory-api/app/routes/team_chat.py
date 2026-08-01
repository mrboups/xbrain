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

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_principal, get_session
from app.models.team import Team
from app.models.user import User
from app.repos import team_messages as tm_repo
from app.repos import teams as teams_repo
from app.repos import users as users_repo
from app.routes.media_helpers import mint_media_token
from app.services import (
    brain_ingest,
    centrifugo_client,
    mention_detector,
    rate_limit,
    team_chat_agent,
    team_context_cache,
    url_safety,
    web_push,
)
from app.services.user_label import LAST_RESORT_LABEL, resolve_user_label

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
    # get_membership() uses (user_id, team_slug) — call it with team.slug we just resolved.
    membership = await teams_repo.get_membership(
        session, user_id=user_id, team_slug=team.slug,
    )
    if membership is None:
        raise HTTPException(403, "not a member of this team")
    # CR BLOCKER: an admin's "block member" (team_members.blocked_at) must revoke access
    # here too, matching the canonical deps.get_team_scope gate (Phase-10 GHA-03). Without
    # this a blocked member could still create/list boards, mint board tokens, catch-me-up,
    # or nudge. Same generic message as the non-member case — no blocked-vs-absent oracle.
    if membership.blocked_at is not None:
        raise HTTPException(403, "not a member of this team")
    return team


async def _author_labels_for(session: AsyncSession, messages) -> dict[str, str]:
    """Resolve every author's chat label in ONE query, keyed by user id string.

    Two properties this shape buys, both load-bearing:

    * **No N+1 and no lazy load.** TeamMessage has no `author` relationship, and
      adding one would not help: a lazy attribute touched on a detached instance
      inside async SQLAlchemy raises MissingGreenlet, which this codebase has
      already been bitten by. One explicit `IN (...)` select over the distinct
      author ids sidesteps both.
    * **It queries `users`, never `team_members`.** A person who has LEFT the
      team, or who has been blocked, still authored their past messages and
      those messages keep their name. Blocking hides someone from the roster; it
      does not rewrite history. This is precisely the case a client-side name
      cache built from GET /members can never handle, because the author is not
      in that list any more.
    """
    ids = {m.author_user_id for m in messages if m.author_user_id is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {str(u.id): resolve_user_label(u) for u in rows}


def _serialize_message(
    m, team_slug: str = "", author_labels: dict[str, str] | None = None
) -> dict[str, Any]:
    """Serialize a TeamMessage row to a dict suitable for API responses.

    When team_slug is provided and the message metadata contains a media item,
    a fresh signed URL is minted so the client can render the attachment with a
    bare <img src> (no Bearer header required).

    `author_label` (resolved through app/services/user_label.py, supplied via
    `author_labels` keyed by author id) makes a message SELF-DESCRIBING. Before
    it, the payload carried only `author_user_id` and every client had to
    resolve names out of band from GET /v1/teams/{id}/members into a local
    cache — a race the client cannot win (the cache is filled after an await, so
    history that renders first renders nameless) and one that fails permanently
    for an author who has since left. Both are why "Teammate" was reaching the
    screen for accounts that have a perfectly good name.

    The label is the ONLY identity field added here: it is the string chat
    already displays. No email, no bio, nothing else about the author travels
    with a message.
    """
    raw_metadata: dict[str, Any] = dict(m.metadata_) if m.metadata_ else {}

    # Enrich media metadata with a freshly-minted signed URL (BL-003 slice 4).
    # The client uses this URL directly for <img src> or <a href> without Bearer.
    if team_slug and "media" in raw_metadata:
        media = raw_metadata["media"]
        item_id = media.get("item_id") if isinstance(media, dict) else None
        if item_id:
            token = mint_media_token(str(item_id), team_slug)
            enriched_media = dict(media)
            enriched_media["url"] = f"/v1/media/{item_id}/img?t={token}"
            raw_metadata = dict(raw_metadata)
            raw_metadata["media"] = enriched_media

    # Agent frames are labelled by the client from `agent_name` (🤖 prefix), so a
    # human label would be meaningless there — null, not a guess.
    author_label: str | None = None
    if m.kind != "agent":
        author_label = (author_labels or {}).get(
            str(m.author_user_id) if m.author_user_id else "",
            # Reached only when the author row is gone entirely (account deleted →
            # author_user_id SET NULL). A nameless record is exactly what the
            # last-resort string is for.
            LAST_RESORT_LABEL,
        )

    return {
        "id": str(m.id),
        "team_id": str(m.team_id),
        "author_user_id": str(m.author_user_id) if m.author_user_id else None,
        "author_label": author_label,
        "agent_name": m.agent_name,
        "kind": m.kind,
        "content": m.content,
        "created_at": m.created_at.isoformat(),
        "routed_via": m.routed_via,
        "metadata": raw_metadata,
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
    team = await _resolve_team_and_check_membership(session, user.id, team_id)
    messages = await tm_repo.list_messages(
        session, team_id=team_id, before_created_at=before, limit=limit
    )
    labels = await _author_labels_for(session, messages)
    return {
        "messages": [_serialize_message(m, team.slug, labels) for m in messages],
        "next_before": messages[-1].created_at.isoformat() if messages else None,
    }


# ── /v1/teams/{team_id}/messages — post ──────────────────────────────────────


class PostMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(..., min_length=1, max_length=16000)
    parent_message_id: UUID | None = None  # Phase 2 threads — accepted now, ignored by readers
    media: dict[str, Any] | None = None  # BL-003: when set, carries {item_id, mime, size, filename} for an uploaded media item


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
      5. Mention detection — if the team's agent alias (@agent + the team's
         defaults/custom) is found, fire-and-forget POST to agent-runtime
      6. Return the serialized message
    """
    user = _require_user_principal(principal)
    team = await _resolve_team_and_check_membership(session, user.id, team_id)

    msg = await tm_repo.insert_user_message(
        session,
        team_id=team_id,
        author_user_id=user.id,
        content=body.content,
        parent_message_id=body.parent_message_id,
        metadata={"media": body.media} if body.media else None,
    )
    await session.commit()
    # The author is the authenticated caller, already loaded — no query needed, and
    # resolved through the SAME helper the history path uses so the frame that
    # arrives live carries the identical label to the one a reload produces.
    payload = _serialize_message(
        msg, team.slug, {str(user.id): resolve_user_label(user)}
    )

    # Resolve THIS team's effective mention aliases ONCE (env defaults ∪
    # team.agent_aliases; @agent always, @claude never) — used by BOTH the brain
    # ingest filter (skip agent-command messages, WR-01) and the summon detector
    # below, so the two never diverge on what counts as a mention.
    aliases = mention_detector.effective_aliases(team.agent_aliases)

    # Core differentiator (MEM-04 / CHAT-03): every substantive human chat
    # message lands in the searchable brain. Fire-and-forget — upserts a
    # WORKING memory_item + Qdrant vector that the agent context bundle and MCP
    # memory_search already read from. Never blocks or breaks the send.
    asyncio.create_task(
        brain_ingest.ingest_team_message(
            team_scope=team.slug,
            team_id=team_id,
            content=body.content,
            author_sub=user.source_user_id,
            aliases=aliases,
        )
    )

    # Realtime fan-out — never block the response.
    asyncio.create_task(
        centrifugo_client.publish(
            channel=f"team:{team_id}",
            data={"type": "message", "message": payload},
        )
    )

    # Agent mention → run the agent handler in the background. Detection is
    # TEAM-SCOPED (Phase 21 / D-21-01): resolve THIS team's effective alias list
    # (env defaults ∪ team.agent_aliases; @agent always present, @claude never)
    # and match against it, so one team's custom name never summons on another.
    # We deliberately run inline (memory-api process) rather than dispatching
    # to agent-runtime — v1 simplification. See Wave 2.5 plan for the
    # tradeoff. The handler never raises; it logs and surfaces errors as
    # `agent_stream_error` frames on the Centrifugo channel.
    mention = mention_detector.detect(body.content, aliases)
    if mention is not None:
        asyncio.create_task(
            team_chat_agent.handle_claude_mention(
                team_id=team_id,
                triggering_message_id=msg.id,
                triggering_user_sub=user.source_user_id,
            )
        )
        log.info(
            "team_chat.mention.enqueued",
            team_id=str(team_id),
            triggering_user_sub=user.source_user_id,
            agent=mention["agent_name"],
        )

    # Phase 27 (D-27-06) — push on the two events that already matter, and ONLY those.
    # NOT on every team message: a group chat that pushes every message trains people to
    # turn notifications off, and then the two that mattered are lost along with the
    # noise. The other event is the Phase-22 nudge, wired in nudge_open below.
    #
    # Placement is load-bearing: this runs AFTER session.commit() (the message row must
    # exist before anyone is told about it) and every send is a create_task, so a slow
    # or failing push service can neither delay nor fail the send. The "@" pre-check
    # keeps the member query off the hot path for the ~all messages that mention nobody
    # — the detector cannot match without one.
    if "@" in body.content and web_push.push_is_configured():
        try:
            rows = await teams_repo.list_members_with_user_info(session, team_id=team_id)
            members = [
                {
                    "user_id": str(u.id),
                    "display_name": u.display_name,
                    "email": u.email,
                    "github_username": u.github_username,
                }
                for (m, u, _b) in rows
                if m.blocked_at is None  # a blocked member is not a notification target
            ]
            for mentioned_id in mention_detector.detect_user_mentions(body.content, members):
                if mentioned_id == str(user.id):
                    continue  # never push someone their own message
                asyncio.create_task(
                    web_push.send_to_user_bg(
                        user_id=UUID(mentioned_id),
                        payload=web_push.build_mention_payload(
                            team_slug=team.slug,
                            # From the AUTHENTICATED sender's row, never the request body:
                            # a forged name on a lock screen is a phishing surface.
                            author_label=resolve_user_label(user),
                            content=body.content,
                            url=f"{settings.APP_PUBLIC_URL.rstrip('/')}/app/",
                        ),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            # The message is committed and published by now. A notification problem must
            # never turn a delivered message into a 500 for the person who sent it.
            log.warning("team_chat.mention_push_failed", team_id=str(team_id), err=str(exc))

    return payload


# ── /v1/teams/{team_id}/mark-read + unread-summary — read cursor (CATCHUP-01) ─


@router.post("/teams/{team_id}/mark-read", status_code=200)
async def mark_team_read(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Advance the CALLER'S OWN read cursor for this team to now().

    D-23-01: `last_read_at` is a PRIVATE per-member cursor — there is NO request
    body and NO target-user field, so a caller can only ever move their own
    cursor (T-23-04, cursor-spoof mitigation). Membership is proven first (403
    for a non-member) BEFORE any write. The extension calls this on chat focus /
    scroll-to-bottom; it is idempotent (repeated calls advance the cursor forward
    to the current time).
    """
    user = _require_user_principal(principal)
    await _resolve_team_and_check_membership(session, user.id, team_id)
    await teams_repo.set_last_read(session, team_id=team_id, user_id=user.id)
    await session.commit()
    return {"status": "ok"}


@router.get("/teams/{team_id}/unread-summary")
async def get_unread_summary(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return {count, since, threshold} for the caller's own unread-that-matters.

    `count` = the number of OTHER members' user messages since the caller's read
    cursor (excludes the caller's OWN messages AND agent frames — see
    count_unread_since, D-23-02). `since` = the caller's cursor ISO timestamp
    (null = never read). `threshold` = CATCHUP_UNREAD_THRESHOLD so the client can
    decide whether to surface the "Catch me up" affordance (D-23-03).

    Membership-gated (403 non-member, T-23-01). Exposes ONLY the caller's own
    cursor — no other member's read state is ever returned, so there is no
    "seen by" / read-receipt surface (T-23-05, D-23-05).
    """
    user = _require_user_principal(principal)
    team = await _resolve_team_and_check_membership(session, user.id, team_id)
    membership = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    cursor = membership.last_read_at if membership else None
    count = await tm_repo.count_unread_since(
        session, team_id=team_id, after_created_at=cursor, exclude_user_id=user.id
    )
    return {
        "count": count,
        "since": cursor.isoformat() if cursor else None,
        "threshold": settings.CATCHUP_UNREAD_THRESHOLD,
    }


# ── /v1/teams/{team_id}/catch-me-up — ephemeral opt-in summary (CATCHUP-01) ──


@router.post("/teams/{team_id}/catch-me-up", status_code=202)
async def catch_me_up(
    team_id: UUID,
    response: Response,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Opt-in "Catch me up": stream an ephemeral, brain-grounded summary of the
    messages since the caller's read cursor to the caller's OWN user channel.

    Ordering is load-bearing — every rejection returns BEFORE the summarizer is
    scheduled, so a rejected request provably starts NO LLM work:
      1. caller must be a member of team_id           → 403 (T-23-01)
      2. per-caller rate limit must not be exhausted  → 429 (T-23-03)
      3. window must be non-empty                     → 200 nothing_to_summarize
         (0-unread short-circuits, NO create_task, never auto-run — D-23-03)
      4. THEN fire-and-forget the ephemeral summarizer → 202 accepted

    The summary streams to `user:<caller_sub>` and is NEVER persisted as a
    `team_messages` row (D-23-04/05, T-23-06).
    """
    user = _require_user_principal(principal)

    # 1. Membership (also resolves the Team → 404 if it doesn't exist).
    team = await _resolve_team_and_check_membership(session, user.id, team_id)

    # 2. Per-caller rate limit — opt-in summaries are LLM-expensive (T-23-03).
    #    Bucket keyed by the caller's sub (NOT client IP — mirrors nudge_open).
    if not rate_limit.check_rate(settings.CATCHUP_RATE_LIMIT, "catchup", user.source_user_id):
        raise HTTPException(429, "catch-me-up is rate-limited — try again later")

    # 3. Resolve the caller's cursor and peek the window. A 0-unread request
    #    short-circuits with NO LLM call and NO create_task — the summary never
    #    auto-runs and never fires on an empty window (D-23-03).
    membership = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    cursor = membership.last_read_at if membership else None
    count = await tm_repo.count_unread_since(
        session, team_id=team_id, after_created_at=cursor, exclude_user_id=user.id
    )
    if count == 0:
        response.status_code = 200
        return {"status": "nothing_to_summarize"}

    # 4. Fire-and-forget the ephemeral summarizer — streams to the caller's OWN
    #    `user:<sub>` channel and persists nothing.
    asyncio.create_task(
        team_chat_agent.catch_me_up(
            team_id=team_id,
            caller_user_sub=user.source_user_id,
            since=cursor,
        )
    )
    log.info(
        "team_chat.catch_me_up.scheduled",
        team_id=str(team_id),
        caller_user_sub=user.source_user_id,
        unread=count,
    )
    return {"status": "accepted"}


# ── /v1/teams/{team_id}/nudge-open — push-a-link (NUDGE-01, D-22-01) ─────────


class PostNudgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: UUID
    # Hard server cap; the TUNABLE gate is settings.NUDGE_MAX_URL_LENGTH, applied
    # by is_safe_nudge_url below. Field bounds only reject the absurdly large early.
    url: str = Field(..., min_length=1, max_length=4096)


@router.post("/teams/{team_id}/nudge-open", status_code=202)
async def nudge_open(
    team_id: UUID,
    body: PostNudgeBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Nudge a SAME-TEAM member to open a URL (consent-gated on the client).

    Publishes an `open_url` event to the target's own `user:<source_user_id>`
    Centrifugo channel. The channel is derived SERVER-side from a verified
    membership — the request body never names a channel (T-22-01), and the URL is
    validated purely lexically with NO server-side fetch/DNS/expansion (T-22-04,
    D-22-03). The extension shows a notification and only opens the tab on the
    recipient's explicit click (D-22-02).

    Ordering is load-bearing — every rejection returns BEFORE the publish is
    scheduled, so a rejected request provably publishes nothing:
      1. sender must be a member of team_id            → 403 (reuse the helper)
      2. target must be a member of the SAME team      → 403 (covers non-member
         AND a different team's member; T-22-02)
      3. URL must be a safe http/https string          → 422 (T-22-03)
      4. per-sender rate limit must not be exhausted   → 429 (T-22-05)
      5. THEN fire-and-forget publish                  → 202
    """
    sender = _require_user_principal(principal)

    # 1. Sender membership (also resolves the Team → 404 if it doesn't exist).
    team = await _resolve_team_and_check_membership(session, sender.id, team_id)

    # 2. Resolve the target and assert SAME-TEAM, NON-BLOCKED membership. A single
    #    uniform 403 covers "user isn't a member", "belongs to a different team",
    #    AND "is blocked" (WR-03) — a blocked member must not be a nudge target,
    #    consistent with the block-enforcement pattern elsewhere (deps.py). The
    #    identical error text also avoids an existence-enumeration oracle.
    target = await users_repo.get_user_by_id(session, body.target_user_id)
    target_membership = (
        None
        if target is None
        else await teams_repo.get_membership(
            session, user_id=target.id, team_slug=team.slug
        )
    )
    if (
        target is None
        or target_membership is None
        or target_membership.blocked_at is not None
    ):
        raise HTTPException(403, "target is not a member of this team")

    # 2b. No self-nudge (IN-01) — server-side, not just the UI guard. Nudging
    #     yourself is meaningless and would notify your own other sessions.
    if target.id == sender.id:
        raise HTTPException(422, "cannot nudge yourself")

    # 3. URL safety (D-22-03) — lexical only; read the tunable cap at REQUEST time
    #    so a test monkeypatching the settings singleton takes effect.
    if not url_safety.is_safe_nudge_url(body.url, max_len=settings.NUDGE_MAX_URL_LENGTH):
        raise HTTPException(422, "url must be a well-formed http/https link")

    # 4. Per-sender rate limit (D-22-04, T-22-05) — bucket keyed by the SENDER's sub
    #    (NOT client IP: enforce_rate_limit keys on IP and must not be used here).
    if not rate_limit.check_rate(settings.NUDGE_RATE_LIMIT, "nudge", sender.source_user_id):
        raise HTTPException(429, "too many link requests — try again shortly")

    # 5. Fire-and-forget publish to the target's OWN user channel — never block the
    #    response. The url is the literal, un-expanded string the sender supplied.
    asyncio.create_task(
        centrifugo_client.publish(
            channel=f"user:{target.source_user_id}",
            data={
                "type": "open_url",
                "url": body.url,
                "from": {
                    "display_name": sender.display_name,
                    "sub": sender.source_user_id,
                },
                "team_id": str(team_id),
                "team_slug": team.slug,
            },
        )
    )

    # Phase 27 (D-27-06) — the second and LAST event that sends a push: the recipient may
    # not have the app open, which is the whole reason a nudge exists.
    #
    # The notification's `url` is the APP, never `body.url`. An OS notification that
    # navigated straight to a teammate-supplied link on tap would bypass the consent gate
    # D-22-02 exists to enforce — a message from another user never moves the recipient's
    # browser without their click on a surface that showed them the destination. The link
    # travels as the notification BODY (so they see it), and the tap lands them in the app
    # on the existing consent prompt.
    asyncio.create_task(
        web_push.send_to_user_bg(
            user_id=target.id,
            payload=web_push.build_nudge_payload(
                sender_label=resolve_user_label(sender),
                target_url=body.url,
                app_url=f"{settings.APP_PUBLIC_URL.rstrip('/')}/app/",
            ),
        )
    )
    log.info(
        "team_chat.nudge_open.scheduled",
        team_id=str(team_id),
        from_sub=sender.source_user_id,
        target_sub=target.source_user_id,
    )
    return {"status": "accepted"}


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
    labels = await _author_labels_for(session, recent)
    return {
        "memory_block": bundle["bundle"],
        "memory_item_count": bundle["item_count"],
        "memory_cached": bundle["cached"],
        "last_messages": [_serialize_message(m, team.slug, labels) for m in recent],
        "team": {
            "id": str(team.id),
            "slug": team.slug,
            "display_name": team.display_name,
        },
    }

"""/v1/teams/{team_id}/messages — team chat realtime endpoints.

Quick task 260512-tcr Wave 2.3:
  POST /v1/teams/{team_id}/messages
      → insert user message + Centrifugo publish + maybe enqueue agent task
  GET  /v1/teams/{team_id}/messages?before=<iso>&limit=50
      → paginated history (newest first), excluding deleted_at IS NOT NULL
  DELETE /v1/teams/{team_id}/messages/{message_id}?scope=message|message_and_brain
      → soft-delete the message (author or team admin) and, on the wider scope,
        the memory items it seeded — including their linked children
  PUT  /v1/teams/{team_id}/messages/{message_id}/star  {"starred": bool}
      → set or clear the ONE truth level a person sets (CANONICAL), on the
        message and on the memory items it seeded. Any non-blocked member; no
        machine principal, ever
  POST /v1/me/centrifugo-token
      → issue HS256-signed client JWT scoped to the caller's team channels
  GET  /v1/teams/{team_id}/agent-context-bundle
      → internal endpoint hit by agent-runtime; requires kind=bridge

Auth model:
  - user-facing endpoints accept kind=user OR kind=user_api_token, with team
    membership checked (T-09-04-02 isolation pattern reused).
  - agent-context-bundle accepts kind=bridge ONLY, and additionally requires
    the JWT's team_scope claim to equal the path team's slug. The shared secret
    gates untrusted code out; the claim comparison is what keeps one trusted
    service from reading every OTHER team's memory through the same door.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import settings
from app.deps import get_current_principal, get_memory_provider, get_session
from app.models.team import Team
from app.models.user import User
from app.repos import team_messages as tm_repo
from app.repos import teams as teams_repo
from app.repos import users as users_repo
from app.routes.media_helpers import mint_media_token
from app.services import (
    background,
    brain_ingest,
    centrifugo_client,
    importance,
    mention_detector,
    message_brain_links,
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
        # The one level a person sets, as a boolean rather than the raw enum.
        # A client renders a star or it does not; what CANONICAL means to the
        # retrieval ranking is the server's business, and leaking the enum would
        # invite a client to write it. Present on history AND on the live frame,
        # so a reload and a realtime arrival agree.
        "starred": m.truth_level == tm_repo.STAR_LEVEL,
        # The brain tag, as a boolean rather than the owner's id. The client
        # needs to know "mark this bubble", never WHOSE it is — and a row only
        # reaches a serializer after the read filter has already decided the
        # viewer may see it, so the id would be redundant and the leak would be
        # free. Present on history AND on the live frame so a reload and a
        # realtime arrival agree.
        "private": m.private_to_user_id is not None,
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
        session,
        team_id=team_id,
        viewer_user_id=user.id,
        before_created_at=before,
        limit=limit,
    )
    labels = await _author_labels_for(session, messages)
    membership = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    return {
        "messages": [_serialize_message(m, team.slug, labels) for m in messages],
        "next_before": messages[-1].created_at.isoformat() if messages else None,
        # What THIS caller may do to OTHER people's messages in this team — the
        # server stating its own delete policy so a client never draws a control
        # the server would refuse (and never hides one it would allow).
        #
        # It rides on the history response, NOT on each message, because the same
        # serialized message is published to the whole team over Centrifugo: a
        # per-message permission field would broadcast the SENDER's rights to
        # every recipient. This response has exactly one reader.
        "viewer": {
            "user_id": str(user.id),
            "can_moderate": bool(membership is not None and membership.role == "admin"),
        },
    }


# ── /v1/teams/{team_id}/messages — post ──────────────────────────────────────


class PostMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(..., min_length=1, max_length=16000)
    parent_message_id: UUID | None = None  # Phase 2 threads — accepted now, ignored by readers
    media: dict[str, Any] | None = None  # BL-003: when set, carries {item_id, mime, size, filename} for an uploaded media item
    # The brain tag. True keeps the message out of the team chat — out of the
    # CHAT, not out of the team's brain: the note is ingested exactly as any
    # other and every member can still recall it. Optional with a default, so an
    # extension built before this field still sends (the model forbids extras,
    # so the compatibility that matters is server-deploys-first, not this).
    private: bool = False


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
        private_to_user_id=user.id if body.private else None,
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
    background.spawn(
        brain_ingest.ingest_team_message(
            team_scope=team.slug,
            team_id=team_id,
            content=body.content,
            author_sub=user.source_user_id,
            aliases=aliases,
            # The back-link that makes "remove this from the brain too" exact
            # rather than a content match — see services/message_brain_links.py.
            message_id=msg.id,
        ),
        name="brain_ingest.team_message",
    )

    # Realtime fan-out — never block the response.
    #
    # THE CHANNEL IS THE ACCESS CONTROL. There is no display-side filtering that
    # can save a wrong choice here: the Centrifugo `team` namespace keeps 100
    # frames for 7 days with force_recovery, so a private note published on
    # `team:` is not a momentary slip — it is replayed to every member on their
    # next reconnect, for a week, and no hotfix takes it back.
    background.spawn(
        centrifugo_client.publish(
            channel=(
                f"user:{user.source_user_id}" if body.private else f"team:{team_id}"
            ),
            # team_id rides along because the per-user channel is CROSS-TEAM: one
            # socket carries every team the person belongs to, so without it the
            # client cannot tell which thread a private frame belongs in.
            data={
                "type": "message",
                "team_id": str(team_id),
                "message": payload,
            },
        ),
        name="centrifugo.publish_message",
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
        background.spawn(
            team_chat_agent.handle_claude_mention(
                team_id=team_id,
                triggering_message_id=msg.id,
                triggering_user_sub=user.source_user_id,
            ),
            name="team_chat_agent.handle_claude_mention",
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
    # `not body.private` is not an optimisation. A push is a read of the content
    # delivered to someone else's lock screen — the one disclosure path no repo
    # filter can reach, because it happens on the WRITE side. A private note that
    # says "@bob" must not light up Bob's phone with its first line.
    if not body.private and "@" in body.content and web_push.push_is_configured():
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
                background.spawn(
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
                    ),
                    name="web_push.mention",
                )
        except Exception as exc:  # noqa: BLE001
            # The message is committed and published by now. A notification problem must
            # never turn a delivered message into a 500 for the person who sent it.
            log.warning("team_chat.mention_push_failed", team_id=str(team_id), err=str(exc))

    return payload


# ── /v1/teams/{team_id}/messages/{message_id} — delete ───────────────────────


class DeleteScope(str, Enum):
    """What a deletion is allowed to reach.

    Two values, and they are genuinely different outcomes rather than a flag on
    one action. `message` takes the bubble out of the chat and leaves the team's
    memory able to answer from what was said; `message_and_brain` also removes the
    memory items the message seeded. Someone deleting a mistake or something
    sensitive needs those to be distinguishable, and the audit trail records them
    as two different actions for the same reason.

    Both are SOFT deletes with a 30-day retention window (the janitor hard-purges
    after that), which is what the UI copy says. Neither is erasure.
    """

    MESSAGE = "message"
    MESSAGE_AND_BRAIN = "message_and_brain"


#: How many item ids the audit payload spells out in full. A chunked document can
#: produce hundreds; the COUNTS are always exact, the id list is a sample beyond
#: this point and says so via `item_ids_truncated`.
_AUDIT_ID_SAMPLE = 100

#: The two audit actions. Named, not built inline, so the sibling work that will
#: log starring and sharing under the same `team_message.` prefix has something to
#: copy — the whole trail is then greppable as one story.
AUDIT_ACTION_DELETE = "team_message.delete"
AUDIT_ACTION_DELETE_WITH_BRAIN = "team_message.delete_with_brain"


async def _assert_can_delete_message(
    session: AsyncSession,
    *,
    user,
    team: Team,
    message,
) -> str:
    """Raise 403 unless this caller may delete this message. Returns WHY they may.

    THE RULE, stated once:

      * the AUTHOR of the message — always. It is their sentence.
      * a TEAM ADMIN (`team_members.role = 'admin'` for THIS team) — always. A
        group chat with no way to remove a teammate's message is a group chat
        where anything posted by mistake is permanent for everyone.
      * anybody else — no. Including a plain member, and including an ordinary
        member who happens to be an env superadmin: cross-team moderation already
        has a route (`DELETE /v1/brain/events/team_message/{id}`, audited under
        `brain.soft_delete`), and widening a member-facing endpoint to reach every
        team buys nothing and costs blast radius.

    AGENT frames have `author_user_id IS NULL`, so nobody can be their author and
    only a team admin can remove one — the same answer `assert_can_edit_brain_event`
    gives for rows with no author column.

    Blocked membership never reaches here: `_resolve_team_and_check_membership`
    already refused it, which is the check this project has shipped a bypass on
    once and now applies on every team-scoped path.

    The returned string ("author" / "team_admin") goes in the audit payload, so a
    row says not just who deleted but on what authority.
    """
    if message.author_user_id is not None and message.author_user_id == user.id:
        return "author"
    membership = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    if membership is not None and membership.role == "admin":
        return "team_admin"
    raise HTTPException(
        403,
        "You can only delete your own messages. A team admin can delete any "
        "message in this team.",
    )


@router.delete("/teams/{team_id}/messages/{message_id}")
async def delete_team_message(
    team_id: UUID,
    message_id: UUID,
    scope: DeleteScope = Query(
        DeleteScope.MESSAGE,
        description=(
            "message = remove the bubble only; message_and_brain = also remove "
            "the memory items this message seeded, and their linked children"
        ),
    ),
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    provider=Depends(get_memory_provider),
) -> dict[str, Any]:
    """Soft-delete one team-chat message, optionally with what it put in the brain.

    Ordering is load-bearing — every rejection returns BEFORE the first write, so
    a refused deletion provably changes nothing and, just as deliberately, writes
    NO audit row (a trail that records attempts as if they were actions is worse
    than none):

      1. caller must be a user principal                → 403
      2. caller must be a non-blocked member of team_id → 403 / 404 (no team)
      3. the message must exist, live, IN THIS TEAM     → 404 (no existence oracle)
      4. caller must be its author or a team admin      → 403
      5. THEN: collect the linked memory items, flip the message, flip the items
         and write the audit row — one transaction, one commit
      6. AFTER the commit: Qdrant, the context cache, and the realtime fan-out

    Step 5 being one transaction is the point of doing the audit here rather than
    afterwards: a deletion that succeeded while its audit row was lost is exactly
    the case the trail exists for.

    Step 6 runs after because Postgres is the source of truth. Qdrant is
    best-effort (the nightly janitor reconciles); the publish is awaited rather
    than detached so a client that gets a 200 knows the other clients were told —
    a message that vanishes for one person and stays for everyone else is worse
    than no feature at all.

    THE ACTOR IS ALWAYS A HUMAN HERE. `_require_user_principal` refuses bridge and
    service JWTs, so `actor_user_id` on a `team_message.delete*` row is never NULL
    by design — a NULL on one of these rows means a bug, not an agent. Nothing
    non-human can reach this path.
    """
    user = _require_user_principal(principal)

    # 1-2. Membership + block check (also resolves the Team → 404 if absent).
    team = await _resolve_team_and_check_membership(session, user.id, team_id)

    # 3. The message must exist, be live, belong to THIS team, and be visible to
    #    the caller. One 404 covers all four so a member cannot probe another
    #    team's message ids — nor another member's brain-tag row.
    message = await tm_repo.get_live_message(
        session, team_id=team_id, message_id=message_id, viewer_user_id=user.id
    )
    if message is None:
        raise HTTPException(404, "message not found")

    # 4. Authorization — the server is the authority, never the client's UI.
    actor_role = await _assert_can_delete_message(
        session, user=user, team=team, message=message
    )

    # 5. Everything below is one transaction.
    #
    #    The linked items are collected BEFORE the message row is flipped: the
    #    collector reads the message's own content and metadata, and it is the
    #    only chance to learn what this message owns.
    linked = message_brain_links.LinkedItems()
    if scope is DeleteScope.MESSAGE_AND_BRAIN:
        author_sub = None
        if message.author_user_id is not None:
            author = await users_repo.get_user_by_id(session, message.author_user_id)
            author_sub = getattr(author, "source_user_id", None) if author else None
        linked = await message_brain_links.collect_linked_items(
            session,
            team_scope=team.slug,
            message_id=message.id,
            content=message.content,
            author_sub=author_sub,
            message_metadata=message.metadata_,
        )

    deleted_at = await tm_repo.soft_delete_message(
        session, team_id=team_id, message_id=message_id, deleted_by=user.id
    )
    if deleted_at is None:
        # Vanished between the fetch and the update — a concurrent delete. Surface
        # it as the same 404 rather than reporting a removal this call did not do.
        raise HTTPException(404, "message not found")

    removed_ids: list[str] = []
    if scope is DeleteScope.MESSAGE_AND_BRAIN and linked.all_ids:
        removed_ids = await message_brain_links.soft_delete_items(
            session,
            team_scope=team.slug,
            item_ids=linked.all_ids,
            deleted_by=user.id,
            deleted_at=deleted_at,
        )

    audit_payload: dict[str, Any] = {
        # Named explicitly rather than inferred from the absence of something.
        "actor_kind": principal.get("kind"),
        "actor_sub": getattr(user, "source_user_id", None),
        "actor_role": actor_role,
        "team_id": str(team_id),
        "team_scope": team.slug,
        "message_id": str(message_id),
        "message_kind": message.kind,
        "message_author_user_id": (
            str(message.author_user_id) if message.author_user_id else None
        ),
        "message_agent_name": message.agent_name,
        "scope": scope.value,
        "deleted_at": deleted_at.isoformat(),
    }
    if scope is DeleteScope.MESSAGE_AND_BRAIN:
        # What actually went with it. Counts are exact; the id list is capped
        # because a chunked document is legitimately hundreds of rows, and it says
        # when it was capped rather than looking complete.
        audit_payload["brain"] = {
            "items_removed": len(removed_ids),
            "root_count": len(linked.roots),
            "child_count": len(linked.children),
            "item_ids": removed_ids[:_AUDIT_ID_SAMPLE],
            "item_ids_truncated": len(removed_ids) > _AUDIT_ID_SAMPLE,
            "matched_legacy_text": linked.matched_legacy_text,
            "link_scan_truncated": linked.truncated,
        }

    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action=(
            AUDIT_ACTION_DELETE_WITH_BRAIN
            if scope is DeleteScope.MESSAGE_AND_BRAIN
            else AUDIT_ACTION_DELETE
        ),
        target_id=str(message_id),
        payload=audit_payload,
    )
    await session.commit()

    # 6. After the commit.
    if removed_ids:
        await message_brain_links.mark_deleted_in_qdrant(
            provider, removed_ids, deleted_at
        )
        # The agent's memory block is cached for 5 minutes. Without this it would
        # keep quoting the removed item for up to that long, to the person who
        # just watched it disappear.
        try:
            team_context_cache.invalidate(team_id)
        except Exception:  # noqa: BLE001 — a cache miss is not a failed deletion
            pass

    # Tell every other client. AWAITED, not detached like the send path: the whole
    # point of the feature is that it reaches the other screens, so a publish that
    # failed must not be reported as a clean success. It still cannot FAIL the
    # deletion — that is already committed — so the outcome travels in the 200 as
    # `broadcast`, and a client that sees false can say "reload to be sure".
    #
    # The channel is the ACTIVE TEAM's, derived server-side from the verified
    # membership. The realtime token grants every team the caller belongs to, so a
    # deletion routed on a bare `team:` prefix would reach threads it has nothing
    # to do with; the client half refuses anything but the open team channel and
    # the caller's own user channel, and this half never names anything else.
    broadcast = False
    try:
        broadcast = bool(
            await centrifugo_client.publish(
                # A brain-tag row is reachable here only by its owner (the read
                # filter 404s everyone else), so the frame announcing what
                # happened to it goes to that one person. On `team:` it would
                # broadcast the id of a message nobody else can see — an
                # existence signal, and a bubble no other client can resolve.
                channel=(
                    f"user:{user.source_user_id}"
                    if message.private_to_user_id is not None
                    else f"team:{team_id}"
                ),
                data={
                    "type": "message_deleted",
                    "message_id": str(message_id),
                    "team_id": str(team_id),
                    "scope": scope.value,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001 — publish() swallows its own, this is belt-and-braces
        log.warning(
            "team_chat.message_deleted.publish_failed",
            team_id=str(team_id),
            message_id=str(message_id),
            err=str(exc),
        )

    log.info(
        "team_chat.message.deleted",
        team_id=str(team_id),
        message_id=str(message_id),
        actor_user_id=str(user.id),
        actor_role=actor_role,
        scope=scope.value,
        brain_items_removed=len(removed_ids),
    )
    return {
        "status": "deleted",
        "message_id": str(message_id),
        "scope": scope.value,
        "deleted_at": deleted_at.isoformat(),
        "brain_items_removed": len(removed_ids),
        "broadcast": broadcast,
    }


# ── /v1/teams/{team_id}/messages/{message_id}/star — the human level ─────────


class StarBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starred: bool


#: Starring and un-starring, under the same prefix as the two delete actions, so
#: `team_message.` greps as one story: what happened to this message, and who
#: did it. Sharing will join the same prefix.
AUDIT_ACTION_STAR = "team_message.star"
AUDIT_ACTION_UNSTAR = "team_message.unstar"


@router.put("/teams/{team_id}/messages/{message_id}/star")
async def set_team_message_star(
    team_id: UUID,
    message_id: UUID,
    body: StarBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Star a message, or take the star off. The ONE level a person sets.

    Of the four truth levels, three are the AI's: it discards what is not
    interesting, stores the rest at WORKING, and flags what it judges final as
    VALIDATED — an opinion it may also withdraw. **Starred (CANONICAL) is the
    only level a person sets**, and that is exactly what gives it weight. No
    automation can reach it: `services/importance.py` names WORKING and
    VALIDATED in its WHERE clauses and therefore cannot write or clear a star.

    UN-STARRING IS ALLOWED. A person may undo their own judgement — that is
    still a person judging, which is the whole test. Nothing else may.

    WHO. Any non-blocked member of the team, and only a member. Deliberately
    wider than deletion, which is author-or-admin: deletion takes something away
    from everybody, while a star only says "this one matters" about a message the
    whole team already shares. Narrower in one direction though — a bridge or
    service principal is refused outright, because a token starring things would
    make the level a machine can set, which is precisely what it must not be.

    ORDERING, as in the delete path: every rejection returns BEFORE the first
    write, so a refused star provably changes nothing and writes NO audit row.

      1. caller must be a user principal                → 403
      2. caller must be a non-blocked member of team_id → 403 / 404 (no team)
      3. the message must exist, live, IN THIS TEAM     → 404 (no existence oracle)
      4. THEN: flip the message, move the memory items it seeded, write the
         audit row — one transaction, one commit
      5. AFTER the commit: the context cache and the realtime fan-out

    A repeat of the state it is already in is not an event: nothing is written,
    no row is audited, no frame is published, and the reply says `changed: false`.
    """
    user = _require_user_principal(principal)

    # 1-2. Membership + block check (also resolves the Team → 404 if absent).
    team = await _resolve_team_and_check_membership(session, user.id, team_id)

    # 3. Exists, live, belongs to THIS team, and is visible to the caller. One
    #    404 covers all four.
    message = await tm_repo.get_live_message(
        session, team_id=team_id, message_id=message_id, viewer_user_id=user.id
    )
    if message is None:
        raise HTTPException(404, "message not found")

    # 4. One transaction.
    moved = await tm_repo.set_message_star(
        session, team_id=team_id, message_id=message_id, starred=body.starred
    )
    if not moved:
        # Already in the requested state. Report the state truthfully and leave
        # the trail alone — a second tap is not a second decision.
        #
        # Returning without committing is the whole cleanup: the UPDATE matched
        # no rows and nothing else here writes, so the session closes on an empty
        # transaction. An explicit rollback would be the same outcome for this
        # request and a worse one for any caller that handed us its own session.
        return {
            "status": "unchanged",
            "message_id": str(message_id),
            "starred": body.starred,
            "changed": False,
            "brain_items": 0,
            "broadcast": False,
        }

    # The memory items this message seeded — resolved through the SAME collector
    # the delete path uses, so "what this message owns" has one definition. A
    # document's body chunks and an image's description travel with it.
    author_sub = None
    if message.author_user_id is not None:
        author = await users_repo.get_user_by_id(session, message.author_user_id)
        author_sub = getattr(author, "source_user_id", None) if author else None
    linked = await message_brain_links.collect_linked_items(
        session,
        team_scope=team.slug,
        message_id=message.id,
        content=message.content,
        author_sub=author_sub,
        message_metadata=message.metadata_,
    )
    moved_items = await importance.apply_star_to_items(
        session,
        team_scope=team.slug,
        item_ids=linked.all_ids,
        starred=body.starred,
    )

    await write_audit(
        session,
        # Never NULL on this action, by construction: `_require_user_principal`
        # refuses everything that is not a person, and a star with no human
        # behind it would not be a star.
        actor_user_id=user.id,
        team_scope=team.slug,
        action=AUDIT_ACTION_STAR if body.starred else AUDIT_ACTION_UNSTAR,
        target_id=str(message_id),
        payload={
            "actor_kind": principal.get("kind"),
            "actor_sub": getattr(user, "source_user_id", None),
            "team_id": str(team_id),
            "team_scope": team.slug,
            "message_id": str(message_id),
            "message_kind": message.kind,
            "starred": body.starred,
            "truth_level": tm_repo.STAR_LEVEL if body.starred else tm_repo.UNSTAR_LEVEL,
            # What moved with it, in the shape `team_message.delete_with_brain`
            # already uses. Counts are exact; the id list is capped and says so.
            "brain": {
                "items_moved": len(moved_items),
                "item_ids": moved_items[:_AUDIT_ID_SAMPLE],
                "item_ids_truncated": len(moved_items) > _AUDIT_ID_SAMPLE,
                "link_scan_truncated": linked.truncated,
            },
        },
    )
    await session.commit()

    # 5. After the commit. The agent's memory block is cached for 5 minutes, and
    # the whole point of a star is that the agent reads it first — without this
    # it would keep ranking the item as ordinary for up to that long.
    try:
        team_context_cache.invalidate(team_id)
    except Exception:  # noqa: BLE001 — a cache miss is not a failed star
        pass

    # Tell the other screens, on the ACTIVE team's channel and no other. Awaited
    # like the delete frame rather than detached like a send: a star that shows
    # for one person and not the rest is worse than none.
    broadcast = False
    try:
        broadcast = bool(
            await centrifugo_client.publish(
                # A brain-tag row is reachable here only by its owner (the read
                # filter 404s everyone else), so the frame announcing what
                # happened to it goes to that one person. On `team:` it would
                # broadcast the id of a message nobody else can see — an
                # existence signal, and a bubble no other client can resolve.
                channel=(
                    f"user:{user.source_user_id}"
                    if message.private_to_user_id is not None
                    else f"team:{team_id}"
                ),
                data={
                    "type": "message_starred",
                    "message_id": str(message_id),
                    "team_id": str(team_id),
                    "starred": body.starred,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001 — publish() swallows its own
        log.warning(
            "team_chat.message_starred.publish_failed",
            team_id=str(team_id),
            message_id=str(message_id),
            err=str(exc),
        )

    log.info(
        "team_chat.message.starred",
        team_id=str(team_id),
        message_id=str(message_id),
        actor_user_id=str(user.id),
        starred=body.starred,
        brain_items_moved=len(moved_items),
    )
    return {
        "status": "starred" if body.starred else "unstarred",
        "message_id": str(message_id),
        "starred": body.starred,
        "changed": True,
        "brain_items": len(moved_items),
        "broadcast": broadcast,
    }


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
    background.spawn(
        team_chat_agent.catch_me_up(
            team_id=team_id,
            caller_user_sub=user.source_user_id,
            since=cursor,
        ),
        name="team_chat_agent.catch_me_up",
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
    background.spawn(
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
        ),
        name="centrifugo.publish_nudge",
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
    background.spawn(
        web_push.send_to_user_bg(
            user_id=target.id,
            payload=web_push.build_nudge_payload(
                sender_label=resolve_user_label(sender),
                target_url=body.url,
                app_url=f"{settings.APP_PUBLIC_URL.rstrip('/')}/app/",
            ),
        ),
        name="web_push.nudge",
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

    Hit by agent-runtime just before calling Anthropic. Auth: bridge JWT only
    (kind=bridge) AND that JWT's own team_scope claim must name the team in the
    path — BRIDGE_SHARED_SECRET proves "a service", never "which team".

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

    # Bridge-kind alone was the ENTIRE gate here, which made the path team_id a
    # free parameter: one bridge JWT — any of them, they are all signed with the
    # same shared secret — dumped the memory bundle and the last twenty messages
    # of every team on the box, one id at a time. The claim the caller was issued
    # must equal the team it is asking for, and a JWT carrying no team_scope at
    # all is a mismatch rather than a wildcard: an unnamed team cannot be the
    # team you were authorised for.
    if principal.get("team_scope") != team.slug:
        raise HTTPException(403, "bridge JWT team_scope does not match this team")

    bundle = await team_context_cache.get_team_memory_bundle(
        session, team_scope=team.slug, team_id=team.id
    )
    # viewer_user_id=None until this endpoint carries an acting user. It has no
    # notion of WHO the bundle is for, so it cannot filter for anyone — None is
    # the closed answer (team-visible rows only) rather than the open one.
    # Giving it an acting user is its own task; leaving it unfiltered was not
    # an option.
    recent = await tm_repo.get_recent_messages_chronological(
        session, team_id=team.id, viewer_user_id=None, limit=20
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

"""/v1/import/transcript — bring an exported AI conversation into the team brain.

Three endpoints, one feature:

    POST   /v1/import/transcript      the import itself
    POST   /v1/me/import-tokens       mint a token that can ONLY do the above
    GET    /v1/me/import-tokens       list them (never the plaintext)
    DELETE /v1/me/import-tokens/{id}  revoke one

Why the import endpoint does not use ``Depends(get_team_scope)``
---------------------------------------------------------------
``get_team_scope`` has a documented gap for single-team ``xbt_`` tokens: it
checks ``blocked_at`` but deliberately does NOT require a membership row to
exist, on the reasoning that the scope was validated when the token was minted.
For a credential that lives on a phone and outlives the membership that
justified it, that reasoning does not hold. ``_require_import_team`` below is
the stricter gate this project already applies in
``team_chat._resolve_team_and_check_membership``: the membership row must
exist AND not be blocked, with one indistinguishable error for both cases so
the response is not an oracle for "does this team exist / am I merely blocked".
This project has shipped a bypass at exactly this seam once already.

Why the body is streamed rather than declared as a Pydantic model
-----------------------------------------------------------------
Declaring a body model makes FastAPI read the whole request before the handler
runs — an unbounded read of something a person picked on their phone. A
ChatGPT export is tens of megabytes. The handler reads the stream itself and
aborts the moment the cap is passed, and the JSON/parse work runs in
``asyncio.to_thread``: this codebase has already frozen its API once by parsing
a document synchronously at ``UVICORN_WORKERS=1``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import settings
from app.deps import get_current_principal, get_session
from app.repos.teams import get_membership
from app.services import token_capabilities, url_safety
from app.services.transcript_import import (
    SOURCE_TAGS,
    SUPPORTED_FORMATS,
    TranscriptParseError,
    parse_transcript,
)
from app.services.transcript_import import ingest as import_ingest

log = structlog.get_logger(__name__)
router = APIRouter()

_MB = 1024 * 1024


# ── auth helpers ────────────────────────────────────────────────────────────


def _require_user(principal: dict[str, Any]):
    """Only a person (or a token acting as one) imports. Never a bridge service."""
    if principal.get("kind") not in ("user", "user_api_token"):
        raise HTTPException(403, "user-only endpoint")
    user = principal.get("user")
    if user is None:
        raise HTTPException(403, "user principal missing user object")
    return user


async def _assert_member(session: AsyncSession, user_id, team_scope: str) -> None:
    """Membership must EXIST and not be blocked. One message for both cases."""
    membership = await get_membership(session, user_id=user_id, team_slug=team_scope)
    if membership is None or membership.blocked_at is not None:
        raise HTTPException(403, "not a member of this team")


async def _require_import_team(
    principal: dict[str, Any] = Depends(get_current_principal),
    x_team_scope: str = Header(..., alias="X-Team-Scope"),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Resolve and authorise the destination team for an import."""
    user = _require_user(principal)
    if principal.get("kind") == "user_api_token":
        scope = principal.get("api_token_team_scope")
        # A token minted for one team cannot write into another, whatever the
        # header says. '' is the multi-team sentinel and falls through to the
        # membership check like a session principal.
        if scope and scope != x_team_scope:
            raise HTTPException(403, "API token team_scope mismatch with X-Team-Scope header")
    await _assert_member(session, user.id, x_team_scope)
    return x_team_scope


# ── POST /v1/import/transcript ──────────────────────────────────────────────


async def _read_capped_body(request: Request, max_bytes: int) -> bytes:
    """Read the body, aborting mid-stream past ``max_bytes``.

    The declared Content-Length is checked first so an oversized upload is
    refused before a single byte is buffered; the streaming check is what
    handles a client that lies about it (or does not declare one at all).
    """
    limit_mb = max(1, max_bytes // _MB)
    too_big = HTTPException(
        413,
        f"This file is larger than the {limit_mb} MB import limit. Import one "
        f"conversation at a time instead of the whole export, or ask an admin to "
        f"raise TRANSCRIPT_IMPORT_MAX_BYTES.",
    )

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise too_big

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            # Stop reading. Nothing beyond the cap is ever held in memory.
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_request(body: bytes, content_type: str, fallback_format: str) -> dict[str, Any]:
    """Turn the raw body into ``{format, content, project_scope, force}``.

    Two shapes are accepted, because two very different clients call this:
      * ``application/json`` — the web app and the extension, which have a
        format to declare and options to pass;
      * anything else — an iOS Shortcut, whose "Get Contents of URL" sends the
        shared text as-is. The whole body IS the transcript and the format
        comes from ``?format=`` (default ``auto``).

    CPU-bound for a large export; call through ``asyncio.to_thread``.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover — decode(errors=) does not raise
            raise TranscriptParseError("This file is not readable as text.") from exc

    if "json" not in content_type.lower():
        return {"format": fallback_format, "content": text, "project_scope": None, "force": False}

    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        # A .json export posted with Content-Type: application/json but no
        # envelope is the common mistake. Treat the body as the transcript.
        return {"format": fallback_format, "content": text, "project_scope": None, "force": False}

    if not isinstance(payload, dict) or "content" not in payload:
        # A bare conversations.json array IS a valid transcript, not an envelope.
        return {"format": fallback_format, "content": text, "project_scope": None, "force": False}

    content = payload.get("content")
    if not isinstance(content, str):
        raise TranscriptParseError('"content" must be the transcript text.')

    return {
        "format": payload.get("format") or fallback_format,
        "content": content,
        "project_scope": payload.get("project_scope") or None,
        "force": bool(payload.get("force", False)),
    }


def _reject_bare_link(body: bytes) -> None:
    """A share URL is the likeliest wrong thing to send. Say so, don't fetch it.

    Sharing from the ChatGPT app hands the share sheet a URL, not the
    conversation, so this body shape will happen constantly. The server does
    NOT fetch it — see the "no chatgpt-share-link parser" note in
    app/services/transcript_import/__init__.py: url_safety is a lexical guard
    with no DNS or IP-range check, and fetching a caller-supplied URL from this
    VM reaches the GCP metadata server. ``is_safe_nudge_url`` is reused here
    only to RECOGNISE a bare URL, never to authorise reaching one.
    """
    if len(body) > 2048:
        return
    try:
        candidate = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return
    if "\n" in candidate or " " in candidate:
        return
    if not url_safety.is_safe_nudge_url(candidate):
        return
    raise HTTPException(
        400,
        "That is a link, not a conversation. This server never fetches a URL "
        "on your behalf. Open the link, copy the conversation, and share the "
        "text instead — or import the conversations.json file from your "
        "ChatGPT data export.",
    )


@router.post("/import/transcript", status_code=202)
async def import_transcript(
    request: Request,
    fallback_format: str = Query(
        "auto",
        alias="format",
        description=f"One of: {', '.join(SUPPORTED_FORMATS)}",
    ),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(_require_import_team),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Import an exported Claude Code or ChatGPT conversation into a team brain.

    Returns 202 with a per-conversation verdict. A conversation already
    imported into this team comes back as ``"duplicate"`` and nothing is
    written — re-running the same shortcut is a no-op that says so.
    """
    user = _require_user(principal)
    body = await _read_capped_body(request, settings.TRANSCRIPT_IMPORT_MAX_BYTES)
    if not body.strip():
        raise HTTPException(400, "The request body is empty — there is nothing to import.")

    _reject_bare_link(body)

    content_type = request.headers.get("content-type", "")
    try:
        payload = await asyncio.to_thread(
            _decode_request, body, content_type, fallback_format
        )
        source_format, conversations = await asyncio.to_thread(
            parse_transcript, payload["content"], payload["format"]
        )
    except TranscriptParseError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not conversations:
        raise HTTPException(
            400,
            "No conversation could be read from this file. Check that it is a "
            "ChatGPT conversations.json export or a Claude Code .jsonl session.",
        )

    max_convs = settings.TRANSCRIPT_IMPORT_MAX_CONVERSATIONS
    truncated_conversations = len(conversations) > max_convs
    if truncated_conversations:
        conversations = conversations[:max_convs]

    plan = await import_ingest.plan_import(
        session,
        team_scope=team_scope,
        source_format=source_format,
        conversations=conversations,
        user_id=user.id,
        max_turns=settings.TRANSCRIPT_IMPORT_MAX_TURNS,
        force=payload["force"],
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team_scope,
        action="import.transcript",
        target_id=None,
        payload={
            "source_format": source_format,
            "source": SOURCE_TAGS.get(source_format, ""),
            **plan.totals,
        },
    )
    await session.commit()

    if plan.pending:
        # Fire-and-forget, exactly like POST /v1/brain/ingest: the caller is a
        # phone on a share sheet and must not hold a connection open while a
        # few hundred turns are classified and embedded.
        asyncio.create_task(
            import_ingest.fan_out(
                team_scope=team_scope,
                source_format=source_format,
                pending=plan.pending,
                author_sub=user.source_user_id,
                project_scope=payload["project_scope"],
                concurrency=settings.TRANSCRIPT_IMPORT_CONCURRENCY,
            )
        )

    totals = plan.totals
    log.info(
        "import.transcript",
        team_scope=team_scope,
        source_format=source_format,
        **totals,
    )
    return {
        "status": "accepted" if plan.pending else "duplicate",
        "format": source_format,
        "team_scope": team_scope,
        "truth_level": "WORKING",
        "conversations": [o.as_dict() for o in plan.outcomes],
        "totals": totals,
        "truncated": truncated_conversations,
    }


# ── /v1/me/import-tokens — the credential the Shortcut carries ──────────────


class ImportTokenCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_scope: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="iOS Shortcut", min_length=1, max_length=128)


class ImportTokenCreated(BaseModel):
    id: str
    token: str  # plaintext — returned ONCE, never again
    team_scope: str
    name: str
    capability: str
    created_at: datetime


class ImportTokenInfo(BaseModel):
    id: str
    team_scope: str
    name: str
    capability: str
    created_at: datetime
    last_used_at: datetime | None


@router.post("/me/import-tokens", response_model=ImportTokenCreated, status_code=201)
async def create_import_token(
    body: ImportTokenCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Mint a token that can reach POST /v1/import/transcript and nothing else.

    The plaintext is returned ONCE. It is the credential an iOS Shortcut holds
    on the device, so it is deliberately narrower than the account: reaching
    /v1/me, the team chat or the brain with it fails with 403.
    """
    user = _require_user(principal)
    await _assert_member(session, user.id, body.team_scope)

    prefix = token_capabilities.TOKEN_PREFIX[token_capabilities.IMPORT]
    raw_token = prefix + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    row = (await session.execute(sa.text("""
        INSERT INTO user_api_tokens (user_id, token_hash, team_scope, name, capability)
        VALUES (:uid, :hash, :ts, :name, :cap)
        RETURNING id, team_scope, name, capability, created_at
    """), {
        "uid": str(user.id),
        "hash": token_hash,
        "ts": body.team_scope,
        "name": body.name,
        "cap": token_capabilities.IMPORT,
    })).mappings().fetchone()

    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=body.team_scope,
        action="me.import_token.created",
        target_id=str(row["id"]),
        payload={"name": body.name, "capability": token_capabilities.IMPORT},
    )
    await session.commit()
    log.info(
        "me.import_token.created",
        user_id=str(user.id),
        team_scope=body.team_scope,
        token_id=str(row["id"]),
    )

    return ImportTokenCreated(
        id=str(row["id"]),
        token=raw_token,
        team_scope=row["team_scope"],
        name=row["name"],
        capability=row["capability"],
        created_at=row["created_at"],
    )


@router.get("/me/import-tokens", response_model=list[ImportTokenInfo])
async def list_import_tokens(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """List the caller's live import tokens. Never returns a plaintext token."""
    user = _require_user(principal)
    rows = (await session.execute(sa.text("""
        SELECT id, team_scope, name, capability, created_at, last_used_at
        FROM user_api_tokens
        WHERE user_id = :uid AND capability = :cap AND revoked_at IS NULL
        ORDER BY created_at DESC
    """), {"uid": str(user.id), "cap": token_capabilities.IMPORT})).mappings().all()

    return [
        ImportTokenInfo(
            id=str(r["id"]),
            team_scope=r["team_scope"],
            name=r["name"],
            capability=r["capability"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
        )
        for r in rows
    ]


@router.delete("/me/import-tokens/{token_id}", status_code=204)
async def revoke_import_token(
    token_id: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Revoke an import token. It stops working everywhere, import included."""
    user = _require_user(principal)
    # A non-UUID path segment is a 404, not a 500 leaking a driver error — and
    # validating it here keeps the session out of a failed transaction state.
    try:
        parsed_id = str(UUID(token_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(404, "Token not found or already revoked") from exc

    row = (await session.execute(sa.text("""
        UPDATE user_api_tokens
        SET revoked_at = now()
        WHERE id = :tid AND user_id = :uid
          AND capability = :cap AND revoked_at IS NULL
        RETURNING team_scope
    """), {
        "tid": parsed_id,
        "uid": str(user.id),
        "cap": token_capabilities.IMPORT,
    })).mappings().fetchone()

    if row is None:
        raise HTTPException(404, "Token not found or already revoked")

    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=row["team_scope"],
        action="me.import_token.revoked",
        target_id=token_id,
        payload={"capability": token_capabilities.IMPORT},
    )
    await session.commit()
    log.info("me.import_token.revoked", user_id=str(user.id), token_id=token_id)
    return None

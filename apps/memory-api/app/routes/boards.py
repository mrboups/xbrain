"""/v1/teams/{team_id}/boards + /v1/boards/{board_id}/token + the internal doc endpoints.

Phase 26a (BOARD-01) — the memory-api half of the collaborative Excalidraw board.

  POST /v1/teams/{team_id}/boards          → create-or-get the team's default board and
                                             hand back an open URL carrying a fresh token
  GET  /v1/teams/{team_id}/boards          → list the team's boards (no doc bytes, no tokens)
  POST /v1/boards/{board_id}/token         → re-check membership, then mint a board token
  GET  /v1/internal/boards/{board_id}/doc  → the Hocuspocus database extension's `fetch`
  PUT  /v1/internal/boards/{board_id}/doc  → the Hocuspocus database extension's `store`

Auth model — the phase's load-bearing invariant is "a member of team B can never open
team A's board", and TWO of its three gates live in this module:

  gate 1  board create/list run `_resolve_team_and_check_membership` (imported from
          team_chat.py, not re-implemented) before touching anything.
  gate 2  the token mint RE-CHECKS membership against the BOARD's own team_id at mint
          time. It is never inherited from an earlier create call and never cached, so
          a revoked member loses access within one token TTL.
  gate 3  lives in the Hocuspocus server (plan 26-03): `claims.board_id === documentName`.

memory-api is the only component that knows team membership, so it is the only component
allowed to say who may open which board. The board server holds no database credentials
and derives no team scope of its own — team_scope arrives as a signed claim or not at all.

The two internal endpoints accept kind=bridge ONLY, mirroring team_chat.py's
/agent-context-bundle. They move the Y.Doc as raw application/octet-stream bytes: no JSON
envelope, no base64. The Hocuspocus database extension requires `fetch()` to return
exactly the bytes `store()` was handed.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_principal, get_session
from app.repos import boards as boards_repo
from app.routes.board_helpers import mint_board_token
from app.routes.team_chat import (
    _require_user_principal,
    _resolve_team_and_check_membership,
)

log = structlog.get_logger(__name__)
router = APIRouter()

# The mint endpoint's ONE answer for both "no such board" and "not your team" — a single
# constant so the two branches physically cannot drift apart. See issue_board_token below.
_BOARD_NOT_FOUND = "board not found"


def _require_bridge_principal(principal: dict[str, Any]) -> None:
    """Reject anything that is not a bridge service principal (T-26-08).

    Shared by BOTH internal doc endpoints so the gate cannot drift between them. A user
    token — even a valid one from a member of the board's own team — gets 403 here: the
    internal endpoints read and overwrite ANY team's board and are never proxied through
    the public board vhost.
    """
    if principal.get("kind") != "bridge":
        raise HTTPException(403, "bridge-only endpoint")


def _serialize_board(board) -> dict[str, Any]:
    return {
        "id": str(board.id),
        "team_id": str(board.team_id),
        "title": board.title,
        "is_default": board.is_default,
        "created_at": board.created_at.isoformat() if board.created_at else None,
    }


class CreateBoardBody(BaseModel):
    title: str | None = None


# ── POST /v1/teams/{team_id}/boards ──────────────────────────────────────────


@router.post("/teams/{team_id}/boards", status_code=201)
async def create_board(
    team_id: UUID,
    body: CreateBoardBody | None = None,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create-or-get the team's default board and return an open URL carrying a token.

    IDEMPOTENT: calling it twice returns the same board (26a ships one default board per
    team; the schema already allows more, so a board list is a later increment with
    nothing to migrate).

    HANDOFF RATIONALE — this is a deliberate security decision, do not "simplify" it
    later: the token rides in the URL **fragment** (`#t=`), never the query string. A
    fragment is never transmitted to any server, so it cannot appear in nginx access
    logs, in a Referer header, or in any proxy log — which is precisely what the "no
    token in the URL" rule is protecting. The SPA strips it with history.replaceState
    before its first network call, and the token is scoped to ONE board and ONE team
    with a 1-hour TTL. The WebSocket itself still carries the token in the Hocuspocus
    Auth MESSAGE via the provider's async token supplier — never as a WS query parameter.
    """
    user = _require_user_principal(principal)
    team = await _resolve_team_and_check_membership(session, user.id, team_id)  # gate 1

    title = (body.title if body else None) or "Team board"
    board = await boards_repo.get_or_create_default_board(
        session,
        team_id=team.id,
        created_by_user_id=user.id,
        title=title,
    )
    await session.commit()

    token = mint_board_token(
        board_id=str(board.id),
        team_scope=team.slug,
        team_id=str(team.id),
        sub=user.source_user_id,
        display_name=getattr(user, "display_name", "") or "",
    )
    expires_at = _token_expiry(token)

    log.info("board.opened", board_id=str(board.id), team_id=str(team.id))
    return {
        **_serialize_board(board),
        "open_url": f"{settings.BOARD_PUBLIC_BASE_URL}/?b={board.id}#t={token}",
        "expires_at": expires_at,
    }


# ── GET /v1/teams/{team_id}/boards ───────────────────────────────────────────


@router.get("/teams/{team_id}/boards")
async def list_team_boards(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List a team's boards. No doc bytes, no tokens — a token is minted only by the
    dedicated mint endpoint, which re-checks membership at that moment."""
    user = _require_user_principal(principal)
    team = await _resolve_team_and_check_membership(session, user.id, team_id)  # gate 1
    rows = await boards_repo.list_boards(session, team_id=team.id)
    return {"boards": [_serialize_board(b) for b in rows]}


# ── POST /v1/boards/{board_id}/token ─────────────────────────────────────────


@router.post("/boards/{board_id}/token")
async def issue_board_token(
    board_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Mint a board token for the caller — gate 2 of the team-scope invariant.

    Membership is re-checked HERE, against the BOARD's own team_id, every single time.
    It is never inherited from the caller's earlier create call and never cached, which
    is what makes a revoked member lose access within one token TTL.

    NO EXISTENCE ORACLE (T-26-07, the Phase-25 discipline): "board does not exist" and
    "board is not yours" return the IDENTICAL 404 with the IDENTICAL detail string
    (`_BOARD_NOT_FOUND`). A distinct 403 would tell an outsider that a given board id
    exists and belongs to someone else. The membership helper's own 404/403 is caught
    and collapsed into that one answer.
    """
    user = _require_user_principal(principal)
    board = await boards_repo.get_board(session, board_id=board_id)
    if board is None:
        raise HTTPException(404, _BOARD_NOT_FOUND)
    try:
        team = await _resolve_team_and_check_membership(session, user.id, board.team_id)
    except HTTPException as exc:
        # 403 "not a member" and 404 "team not found" both collapse to the SAME answer
        # a caller gets for a board id that does not exist at all.
        log.info(
            "board.token_denied",
            board_id=str(board_id),
            user_id=str(user.id),
            reason=exc.status_code,
        )
        raise HTTPException(404, _BOARD_NOT_FOUND) from exc

    token = mint_board_token(
        board_id=str(board.id),
        team_scope=team.slug,
        team_id=str(team.id),
        sub=user.source_user_id,
        display_name=getattr(user, "display_name", "") or "",
    )
    return {
        "token": token,
        "board_id": str(board.id),
        "ws_url": settings.BOARD_WS_URL_PUBLIC,
        "expires_at": _token_expiry(token),
    }


def _token_expiry(token: str) -> int:
    """Return the `exp` claim of a freshly minted token (unix seconds).

    Decoded rather than recomputed so the value the client caches can never drift from
    the value the socket will actually enforce.
    """
    from authlib.jose import jwt as authlib_jwt  # lazy — mirrors board_helpers

    claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    return int(claims["exp"])


# ── /v1/internal/boards/{board_id}/doc — bridge-only (Hocuspocus persistence) ─


@router.get("/internal/boards/{board_id}/doc")
async def fetch_board_doc(
    board_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return the stored Y.Doc update as raw bytes — the extension's `fetch`.

    404 means "new board, nothing stored yet"; the extension treats it as null and
    starts an empty document. The body is the bytea VERBATIM: no JSON envelope, no
    base64, byte-identical to what `store` was given (T-26-10).
    """
    _require_bridge_principal(principal)
    doc = await boards_repo.get_doc(session, board_id=board_id)
    if doc is None:
        raise HTTPException(404, "board document not found")
    return Response(content=doc.state, media_type="application/octet-stream")


@router.put("/internal/boards/{board_id}/doc", status_code=204)
async def store_board_doc(
    board_id: UUID,
    request: Request,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Overwrite the board's stored Y.Doc update — the extension's `store`.

    Overwriting the single row IS the compaction strategy: Hocuspocus hands `store()`
    already-compacted full state and Yjs garbage-collects internally. Never append.

    An unknown board is a 404 and NEVER an implicit insert (T-26-12): boards are created
    only through the membership-gated user endpoint, so a board can never be conjured
    from the socket side.
    """
    _require_bridge_principal(principal)
    body = await request.body()
    if len(body) > settings.BOARD_MAX_DOC_BYTES:
        # Reject loudly rather than let one pasted screenshot OOM the board container on
        # the next fetch (T-26-09). 26a keeps images as base64 inside the doc, so this
        # cap is the only thing standing between a big paste and the memory limit.
        log.warning(
            "board_doc_rejected_oversize",
            board_id=str(board_id),
            size=len(body),
            cap=settings.BOARD_MAX_DOC_BYTES,
        )
        raise HTTPException(413, "board document too large")
    if not body:
        raise HTTPException(400, "empty board document")

    board = await boards_repo.get_board(session, board_id=board_id)
    if board is None:
        raise HTTPException(404, "board not found")

    await boards_repo.upsert_doc(session, board_id=board_id, state=body)
    await session.commit()
    log.info("board_doc_stored", board_id=str(board_id), size=len(body))
    return Response(status_code=204)

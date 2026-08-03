"""GET /v1/internal/bridge-status/{user_sub} — is this user's socket live?

WHY THIS EXISTS.

memory-api decides, on every agent turn, whether to route through the user's
Claude subscription or fall back to an API key. It used to decide by reading
`user_external_sessions.last_seen_at` and allowing it 90 seconds of slack, with
its own docstring calling that provisional. A timestamp is a guess in both
directions: it calls a bridge dead 90 seconds after a heartbeat is missed, and
calls one alive for 90 seconds after the laptop lid closed. In production it
went thirty minutes stale, so every mention fell through to a key with no credit
on it.

This process holds the sockets. Asking it is not a better heuristic — it is the
answer, and it is a single dict lookup on an internal network hop memory-api
already makes to reach /v1/chat/completions.

AUTH. Bridge JWT only, and the `acting_user_sub` claim must equal the path. The
claim is what the chat route already uses to decide whose subscription runs a
request, so a token that cannot route for a user cannot ask about them either.
Without that equality check any service holding any bridge JWT could enumerate
which members of which team currently have a browser open — a presence oracle
nobody asked for.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import resolve_bridge_jwt_sub
from app.pool import pool

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/v1/internal/bridge-status/{user_sub}")
async def bridge_status(user_sub: str, request: Request):
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return JSONResponse(status_code=401, content={"code": "missing_auth"})
    token = auth.split(" ", 1)[1].strip()

    acting_sub = resolve_bridge_jwt_sub(token)
    if acting_sub is None:
        log.info("bridge_status.rejected", reason="not_a_bridge_jwt")
        return JSONResponse(status_code=401, content={"code": "invalid_token"})

    if acting_sub != user_sub:
        # Not 404: the caller authenticated fine, it is asking about somebody
        # it has no standing to ask about. Saying so plainly is safe — it
        # already knows which user its own token names.
        log.info("bridge_status.rejected", reason="sub_mismatch")
        return JSONResponse(status_code=403, content={"code": "sub_mismatch"})

    return {"user_sub": user_sub, "live": pool.has_socket(user_sub)}

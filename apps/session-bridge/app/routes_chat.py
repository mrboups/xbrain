"""POST /v1/chat/completions — OpenAI-compat endpoint with two auth modes.

Auth modes:
  A. `Bearer xbt_...` from LibreChat / user clients → validate_xbt_token →
     route to that user's own WS pool entry.
  B. `Bearer <bridge JWT>` from agent-runtime → verify_bridge_jwt → use
     `acting_user_sub` claim → route via THAT user's WS pool entry.
     This is how Claude responses in team chat route through the
     triggering user's Pro/Max subscription (quick task 260512-tcr
     decision #12).

After auth resolution both modes share the same Pool.push_request →
queue → StreamingResponse flow. Keepalive comments every 25s defeat
Cloudflare's 100s idle timeout.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import structlog
from authlib.jose import jwt as jose_jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth import validate_xbt_token
from app.config import settings
from app.pool import pool

log = structlog.get_logger(__name__)
router = APIRouter()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, "code": code})


def _looks_like_xbt(token: str) -> bool:
    return token.startswith("xbt_")


def _resolve_user_sub_from_bridge_jwt(token: str) -> str | None:
    """Decode a bridge JWT and return the acting_user_sub claim.

    Returns None when the token isn't a valid bridge JWT (wrong signature,
    wrong scope, missing claim, or simply not a JWT shape). Caller falls
    back to the xbt_ path.
    """
    if not settings.BRIDGE_SHARED_SECRET:
        return None
    # Cheap pre-check: a JWT has exactly two dots.
    if token.count(".") != 2:
        return None
    try:
        claims = jose_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
        claims.validate()
    except Exception:  # noqa: BLE001 — any decode/validate failure → not a bridge JWT
        return None
    if claims.get("scope") != "bridge":
        return None
    # Defense in depth: explicit exp check (validate() already does this but
    # belt-and-braces given the security-sensitive routing decision).
    exp = claims.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        return None
    acting_sub = claims.get("acting_user_sub")
    if not acting_sub or not isinstance(acting_sub, str):
        return None
    return acting_sub


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return _err(401, "missing_auth", "missing Bearer token")
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return _err(401, "missing_auth", "empty Bearer token")

    sub: str | None = None
    auth_mode: str = ""

    if _looks_like_xbt(token):
        try:
            me = await validate_xbt_token(token)
        except PermissionError as e:
            log.info("chat.auth.rejected", err=str(e), mode="xbt")
            return _err(401, "invalid_token", "invalid xbt_ token")
        # memory-api /v1/me returns `source_user_id`; the WS pool uses it
        # as the canonical key.
        sub = me.get("source_user_id") or me.get("sub")
        auth_mode = "xbt"
    else:
        # Treat as bridge JWT (agent-runtime acting on behalf of a user).
        sub = _resolve_user_sub_from_bridge_jwt(token)
        if sub is None:
            log.info("chat.auth.rejected", err="not_xbt_or_bridge_jwt", mode="unknown")
            return _err(401, "invalid_token", "invalid token")
        auth_mode = "bridge"

    if not sub:
        return _err(401, "invalid_token", "token did not resolve to a user sub")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "invalid_body", "request body is not valid JSON")

    request_id = str(uuid.uuid4())

    try:
        q = await pool.push_request(sub, request_id, body)
    except KeyError:
        log.info("chat.no_session", sub=sub, mode=auth_mode)
        return _err(
            503,
            "no_session",
            "install xbrain extension and login to claude.ai",
        )

    log.info("chat.routed", sub=sub, mode=auth_mode, request_id=request_id)

    async def event_stream():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                t = msg.get("type")
                if t == "chunk":
                    yield f"data: {json.dumps(msg.get('openai_chunk', {}))}\n\n"
                elif t == "end":
                    yield "data: [DONE]\n\n"
                    return
                elif t == "error":
                    detail = msg.get("detail", {})
                    yield f"data: {json.dumps({'error': detail})}\n\n"
                    return
                # Unknown frame types are ignored.
        finally:
            await pool.drop_request_queue(sub, request_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

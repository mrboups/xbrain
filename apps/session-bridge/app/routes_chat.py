"""POST /v1/chat/completions — OpenAI-compat endpoint called by LibreChat.

Flow:
  1. Parse `Authorization: Bearer xbt_...` → 401 if absent/malformed.
  2. validate_xbt_token → 401 on PermissionError.
  3. pool.push_request → 503 `no_session` if no WS registered.
  4. StreamingResponse that reads from the per-request asyncio.Queue, emits
     SSE chunks, and inserts `: keepalive\\n\\n` comments every 25s so
     Cloudflare's 100s idle timeout doesn't kill the connection.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth import validate_xbt_token
from app.pool import pool

log = structlog.get_logger(__name__)
router = APIRouter()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, "code": code})


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return _err(401, "missing_auth", "missing Bearer token")
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return _err(401, "missing_auth", "empty Bearer token")

    try:
        me = await validate_xbt_token(token)
    except PermissionError as e:
        log.info("chat.auth.rejected", err=str(e))
        return _err(401, "invalid_token", "invalid xbt_ token")

    sub = me["sub"]

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "invalid_body", "request body is not valid JSON")

    request_id = str(uuid.uuid4())

    try:
        q = await pool.push_request(sub, request_id, body)
    except KeyError:
        log.info("chat.no_session", sub=sub)
        return _err(
            503,
            "no_session",
            "install xbrain extension and login to claude.ai",
        )

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

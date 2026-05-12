"""WebSocket endpoint /ws/{user_sub}.

Validates the xbt_ token from the query string, enforces sub == path, registers
the socket in the in-memory pool (last-write-wins), then enters a receive loop
that:

- handles `register` frames by firing a non-blocking upsert into memory-api;
- ignores `ping` keepalives;
- routes `chunk`/`end`/`error` to the right per-request queue.

On disconnect, drains all in-flight queues for the user so the HTTP chat
handlers unblock with an `extension_disconnected` error frame.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.auth import validate_xbt_token
from app.envelope import IncomingFrame, RegisterFrame
from app.memory_api_client import upsert_external_session
from app.pool import pool

log = structlog.get_logger(__name__)
router = APIRouter()

_frame_adapter: TypeAdapter[IncomingFrame] = TypeAdapter(IncomingFrame)


def _token_fingerprint(token: str) -> str:
    """Return a 8-char prefix so logs can identify tokens without leaking them."""
    if not token:
        return "<empty>"
    return token[:8] + "..."


@router.websocket("/ws/{user_sub}")
async def ws_endpoint(
    websocket: WebSocket,
    user_sub: str,
    token: str = Query(...),
) -> None:
    """Persistent WS from the user's Chrome extension."""
    fingerprint = _token_fingerprint(token)

    # Validate token BEFORE accepting so we can use proper close codes.
    try:
        me = await validate_xbt_token(token)
    except PermissionError as e:
        log.info("ws.rejected", reason="invalid_token", token_fp=fingerprint, err=str(e))
        await websocket.close(code=4401, reason="invalid token")
        return
    except Exception as e:  # noqa: BLE001 — defensive: never crash without a close code
        log.warning("ws.rejected", reason="auth_error", token_fp=fingerprint, err=str(e))
        await websocket.close(code=4401, reason="auth error")
        return

    if me.get("sub") != user_sub:
        log.info(
            "ws.rejected",
            reason="sub_mismatch",
            path_sub=user_sub,
            token_sub=me.get("sub"),
            token_fp=fingerprint,
        )
        await websocket.close(code=4403, reason="sub mismatch")
        return

    await websocket.accept()
    log.info("ws.connected", sub=user_sub, token_fp=fingerprint)

    # Last-write-wins: register, then close any prior socket OUTSIDE the lock.
    prev = await pool.register(user_sub, websocket)
    if prev is not None:
        try:
            await prev.close(code=4000, reason="superseded")
        except Exception:  # noqa: BLE001 — the old socket may already be dead
            pass

    try:
        while True:
            raw = await websocket.receive_json()
            try:
                frame = _frame_adapter.validate_python(raw)
            except ValidationError as e:
                log.info(
                    "ws.frame.invalid",
                    sub=user_sub,
                    err=str(e)[:200],
                    raw_type=raw.get("type") if isinstance(raw, dict) else "?",
                )
                continue

            if isinstance(frame, RegisterFrame):
                # Fire-and-forget — never block the recv loop on memory-api.
                metadata = {
                    "email_logged": frame.email_logged,
                    "org_id": frame.org_id,
                }
                asyncio.create_task(
                    upsert_external_session(
                        user_sub=user_sub,
                        provider=frame.provider,
                        extension_id=frame.extension_id,
                        metadata=metadata,
                    )
                )
                await websocket.send_json(
                    {"type": "register_ack", "ok": True, "error": None}
                )
                continue

            if frame.type == "ping":
                continue  # keepalive

            # chunk / end / error all carry request_id and go to the pool.
            await pool.deliver_chunk(user_sub, frame.request_id, raw)

    except WebSocketDisconnect:
        log.info("ws.disconnected", sub=user_sub)
    except Exception as e:  # noqa: BLE001 — defensive
        log.warning("ws.loop.error", sub=user_sub, err=str(e))
    finally:
        await pool.unregister(user_sub, websocket)
        await pool.drain(user_sub, "extension_disconnected")

"""In-memory WS pool + per-request response queues.

All mutations of `_sockets` and `_queues` happen under a single asyncio.Lock so
the WS recv loop, the HTTP chat handler, and the WS disconnect cleanup never
race against each other.

`_sockets[user_sub]` holds at most one WebSocket per user (last-write-wins).
`_queues[user_sub][request_id]` is the queue the HTTP handler reads from.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class Pool:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sockets: dict[str, WebSocket] = {}
        # user_sub -> { request_id -> asyncio.Queue }
        self._queues: dict[str, dict[str, asyncio.Queue]] = {}

    async def register(self, user_sub: str, ws: WebSocket) -> WebSocket | None:
        """Insert `ws` as the active socket for `user_sub`.

        Returns the previously-registered socket (if any) so the caller can
        close it with code 4000 (last-write-wins). Caller MUST close it outside
        the lock to avoid awaiting on a remote peer while holding the pool lock.
        """
        async with self._lock:
            prev = self._sockets.get(user_sub)
            self._sockets[user_sub] = ws
            self._queues.setdefault(user_sub, {})
            return prev

    async def unregister(self, user_sub: str, ws: WebSocket) -> None:
        """Remove `ws` only if it is still the registered socket for `user_sub`.

        No-op if another socket has already replaced it (last-write-wins
        already evicted us). Does NOT drain queues — call `drain()` for that.
        """
        async with self._lock:
            if self._sockets.get(user_sub) is ws:
                self._sockets.pop(user_sub, None)

    async def push_request(
        self, user_sub: str, request_id: str, openai_body: dict
    ) -> asyncio.Queue:
        """Create a per-request queue and forward the chat request to the extension WS.

        Raises KeyError if no WS is currently registered for `user_sub`. The HTTP
        handler maps that to a 503 `no_session` response.
        """
        async with self._lock:
            ws = self._sockets.get(user_sub)
            if ws is None:
                raise KeyError(f"no_session:{user_sub}")
            q: asyncio.Queue = asyncio.Queue()
            self._queues.setdefault(user_sub, {})[request_id] = q
        # send_json OUTSIDE the lock — it awaits on the network and we don't
        # want to serialize all chat starts behind a single network round-trip.
        await ws.send_json(
            {"type": "chat_request", "request_id": request_id, "openai_body": openai_body}
        )
        return q

    async def deliver_chunk(self, user_sub: str, request_id: str, msg: dict[str, Any]) -> None:
        """Route a chunk/end/error frame from the extension to the right HTTP handler.

        Silently drops the frame if no queue exists (handler already timed out
        or the request_id is stale).
        """
        async with self._lock:
            q = self._queues.get(user_sub, {}).get(request_id)
        if q is not None:
            await q.put(msg)

    async def drop_request_queue(self, user_sub: str, request_id: str) -> None:
        """Remove a finished per-request queue (called from the HTTP handler's finally)."""
        async with self._lock:
            self._queues.get(user_sub, {}).pop(request_id, None)

    async def drain(self, user_sub: str, reason: str) -> None:
        """Push an error frame to every in-flight queue for the user, then drop them.

        Called when the user's WS disconnects so the HTTP handlers unblock
        instead of hanging until the SSE timeout.
        """
        async with self._lock:
            queues = self._queues.pop(user_sub, {})
        # Put errors OUTSIDE the lock (asyncio.Queue.put doesn't block on an
        # unbounded queue but make this future-proof).
        for q in queues.values():
            await q.put({"type": "error", "detail": {"reason": reason}})

    def active_count(self) -> int:
        """Number of currently-registered WS connections (for /healthz)."""
        return len(self._sockets)

    def _reset_for_tests(self) -> None:
        """Test helper — clears in-memory state between tests."""
        self._sockets.clear()
        self._queues.clear()


# Module singleton — every route imports this same instance.
pool = Pool()

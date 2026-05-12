"""Pool: register / last-write-wins / push_request / drain."""
from __future__ import annotations

import asyncio

import pytest

from app.pool import pool


@pytest.mark.asyncio
async def test_register_returns_none_first_time(fake_ws):
    prev = await pool.register("user-1", fake_ws)
    assert prev is None
    assert pool.active_count() == 1


@pytest.mark.asyncio
async def test_register_returns_prev_socket_on_second_connect(fake_ws):
    """Last-write-wins: registering a second socket for the same sub returns the first one."""

    class WS2:
        async def send_json(self, msg):
            pass

        async def close(self, code=1000, reason=""):
            pass

    ws2 = WS2()
    first = await pool.register("user-1", fake_ws)
    second = await pool.register("user-1", ws2)

    assert first is None
    assert second is fake_ws  # caller will close it with 4000
    assert pool.active_count() == 1  # only the new one is registered


@pytest.mark.asyncio
async def test_push_request_raises_keyerror_when_no_socket():
    """Chat handler depends on KeyError → 503 mapping."""
    with pytest.raises(KeyError):
        await pool.push_request("nobody", "req-1", {"messages": []})


@pytest.mark.asyncio
async def test_push_request_sends_chat_request_frame(fake_ws):
    await pool.register("user-1", fake_ws)
    q = await pool.push_request("user-1", "req-abc", {"model": "claude", "messages": [{"role": "user", "content": "hi"}]})

    assert isinstance(q, asyncio.Queue)
    assert len(fake_ws.sent) == 1
    sent = fake_ws.sent[0]
    assert sent["type"] == "chat_request"
    assert sent["request_id"] == "req-abc"
    assert sent["openai_body"]["model"] == "claude"


@pytest.mark.asyncio
async def test_drain_unblocks_inflight_queues(fake_ws):
    """drain() must push an error frame to every in-flight queue so HTTP handlers unblock."""
    await pool.register("user-1", fake_ws)
    q1 = await pool.push_request("user-1", "req-1", {})
    q2 = await pool.push_request("user-1", "req-2", {})

    await pool.drain("user-1", "extension_disconnected")

    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert msg1["type"] == "error"
    assert msg1["detail"]["reason"] == "extension_disconnected"
    assert msg2["type"] == "error"

    # Subsequent push must fail because socket still registered but a fresh
    # push after drain is fine — drain only clears queues, not sockets.
    q3 = await pool.push_request("user-1", "req-3", {})
    assert isinstance(q3, asyncio.Queue)


@pytest.mark.asyncio
async def test_deliver_chunk_routes_by_request_id(fake_ws):
    await pool.register("user-1", fake_ws)
    q = await pool.push_request("user-1", "req-X", {})

    await pool.deliver_chunk(
        "user-1", "req-X", {"type": "chunk", "request_id": "req-X", "openai_chunk": {"id": 1}}
    )
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    assert msg["openai_chunk"] == {"id": 1}


@pytest.mark.asyncio
async def test_deliver_chunk_silent_drop_for_unknown_request(fake_ws):
    """A chunk arriving after the per-request queue is gone must NOT crash the WS recv loop."""
    await pool.register("user-1", fake_ws)
    # No push_request → no queue for this request_id.
    await pool.deliver_chunk(
        "user-1", "ghost-req", {"type": "chunk", "request_id": "ghost-req", "openai_chunk": {}}
    )
    # No exception → pass.


@pytest.mark.asyncio
async def test_unregister_only_removes_current_socket(fake_ws):
    """If a newer WS replaced ours, our finally-block unregister must NOT evict the new one."""

    class WS2:
        async def send_json(self, msg):
            pass

        async def close(self, code=1000, reason=""):
            pass

    ws2 = WS2()
    await pool.register("user-1", fake_ws)
    await pool.register("user-1", ws2)  # replaces fake_ws

    # fake_ws's finally block calls unregister with itself
    await pool.unregister("user-1", fake_ws)

    # The new socket must still be there.
    assert pool.active_count() == 1

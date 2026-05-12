"""HTTP /v1/chat/completions + WS /ws/{user_sub} round-trips against the real ASGI app.

Uses fastapi.testclient. respx mocks memory-api so tests don't need network. Pool
is reset between tests via the autouse fixture in conftest.

For the SSE-streaming test, we open a real WS first (so the pool's queue + lock
bind to the same event loop the chat handler will use), then fire the chat
request in a background thread, then push chunks back over the WS.
"""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _mock_me(respx_mock, sub: str = "user-1") -> None:
    respx_mock.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
        return_value=httpx.Response(
            200, json={"sub": sub, "kind": "user_api_token", "email": "u@x.com"}
        )
    )


def test_healthz_returns_active_count(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok"
    assert j["active_sockets"] == 0


def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_401_missing_auth(client):
    r = client.post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 401
    assert r.json()["code"] == "missing_auth"


def test_chat_401_invalid_token(client):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
            return_value=httpx.Response(401, json={"detail": "bad"})
        )
        r = client.post(
            "/v1/chat/completions",
            json={"messages": []},
            headers={"Authorization": "Bearer xbt_bogus"},
        )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_token"


def test_chat_503_when_no_socket(client):
    with respx.mock(assert_all_called=False) as m:
        _mock_me(m, sub="user-1")
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer xbt_valid"},
        )
    assert r.status_code == 503
    j = r.json()
    assert j["code"] == "no_session"
    assert "extension" in j["error"].lower()


def test_chat_streams_chunks_from_ws(client):
    """End-to-end SSE: open a real WS, fire HTTP chat in a thread, push chunks back."""
    with respx.mock(assert_all_called=False) as m:
        _mock_me(m, sub="user-1")
        # Tolerate the optional register-upsert call.
        m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions").mock(
            return_value=httpx.Response(200, json={"id": "row-x"})
        )

        with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
            body = {
                "model": "claude-opus",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }
            chunks_collected: list[str] = []
            done_event = threading.Event()
            http_err: list[Exception] = []

            def _run_http():
                try:
                    with client.stream(
                        "POST",
                        "/v1/chat/completions",
                        json=body,
                        headers={"Authorization": "Bearer xbt_valid"},
                    ) as resp:
                        assert resp.status_code == 200, resp.text
                        assert "text/event-stream" in resp.headers["content-type"]
                        for line in resp.iter_lines():
                            if line:
                                chunks_collected.append(line)
                            if any("[DONE]" in c for c in chunks_collected):
                                break
                except Exception as e:  # noqa: BLE001
                    http_err.append(e)
                finally:
                    done_event.set()

            http_thread = threading.Thread(target=_run_http, daemon=True)
            http_thread.start()

            # The bridge pushes a `chat_request` frame to us over the WS once
            # push_request resolves on its side.
            chat_req = ws.receive_json()
            assert chat_req["type"] == "chat_request"
            request_id = chat_req["request_id"]
            assert chat_req["openai_body"]["model"] == "claude-opus"

            # Stream back a chunk + end frame.
            ws.send_json(
                {
                    "type": "chunk",
                    "request_id": request_id,
                    "openai_chunk": {"choices": [{"delta": {"content": "hello"}}]},
                }
            )
            ws.send_json({"type": "end", "request_id": request_id})

            done_event.wait(timeout=10.0)
            http_thread.join(timeout=2.0)

        assert not http_err, f"http thread failed: {http_err}"
        joined = "\n".join(chunks_collected)
        assert "choices" in joined, f"no chunk in response: {joined!r}"
        assert "hello" in joined
        assert "[DONE]" in joined


def test_ws_4401_on_invalid_token(client):
    """WS rejected when memory-api returns 401 for the xbt_ token."""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
            return_value=httpx.Response(401)
        )
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/user-1?token=xbt_bad"):
                pass


def test_ws_4403_on_sub_mismatch(client):
    """Path user_sub doesn't match token sub → close 4403."""
    with respx.mock(assert_all_called=False) as m:
        _mock_me(m, sub="user-actual")
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/user-wrong?token=xbt_valid"):
                pass


def test_ws_register_triggers_upsert(client):
    """WS register frame fires memory-api POST /v1/me/external-sessions AND returns register_ack."""
    upsert_called = {"count": 0, "body": None, "auth": None}

    def _capture(request: httpx.Request) -> httpx.Response:
        upsert_called["count"] += 1
        upsert_called["body"] = request.content
        upsert_called["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "row-1"})

    with respx.mock(assert_all_called=False) as m:
        _mock_me(m, sub="user-1")
        m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions").mock(side_effect=_capture)

        with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
            ws.send_json(
                {
                    "type": "register",
                    "provider": "claude",
                    "extension_id": "ext-9",
                    "email_logged": "u@x.com",
                    "org_id": "org-A",
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "register_ack"
            assert ack["ok"] is True

            # Wait up to ~2.5s for the fire-and-forget task to land.
            for _ in range(50):
                if upsert_called["count"] > 0:
                    break
                time.sleep(0.05)

        assert upsert_called["count"] >= 1, "memory-api upsert was not called"
        body = json.loads(upsert_called["body"])
        assert body["provider"] == "claude"
        assert body["extension_id"] == "ext-9"
        assert body["metadata"]["email_logged"] == "u@x.com"
        assert body["metadata"]["org_id"] == "org-A"
        assert upsert_called["auth"].startswith("Bearer ")


def test_ws_register_upsert_failure_does_not_close_ws(client):
    """If memory-api returns 500 on upsert, the WS must stay open + ack still True (fire-and-forget)."""
    with respx.mock(assert_all_called=False) as m:
        _mock_me(m, sub="user-1")
        m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions").mock(
            return_value=httpx.Response(500)
        )

        with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
            ws.send_json(
                {
                    "type": "register",
                    "provider": "claude",
                    "extension_id": None,
                    "email_logged": None,
                    "org_id": None,
                }
            )
            ack = ws.receive_json()
            # Ack is sent BEFORE the upsert outcome is known (fire-and-forget):
            # so ack.ok == True regardless of the eventual upsert result.
            assert ack["type"] == "register_ack"
            assert ack["ok"] is True
            # WS should still be open — send a ping
            ws.send_json({"type": "ping", "ts": 1})

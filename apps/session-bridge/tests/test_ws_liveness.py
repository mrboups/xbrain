"""The keepalive is a round trip.

The extension could not tell a quiet socket from a dead one.

  - `ping` is answered with `pong`. An idle bridge sends nothing for hours, so
    silence carried no information and a half-open socket read as healthy on the
    client until something was actually sent through it.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.pool import pool


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _mock_me(respx_mock, sub: str = "user-1") -> None:
    respx_mock.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "sub": sub,
                "source_user_id": sub,
                "kind": "user_api_token",
                "email": "u@x.com",
            },
        )
    )


class TestPingIsAnswered:
    def test_ping_gets_a_pong_back(self, client):
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
                ws.send_json({"type": "ping", "ts": 1234})
                reply = ws.receive_json()
        assert reply["type"] == "pong"

    def test_the_pong_echoes_the_ping_timestamp(self, client):
        """Echoed so the client can measure the round trip, not just observe one."""
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
                ws.send_json({"type": "ping", "ts": 987_654})
                reply = ws.receive_json()
        assert reply["ts"] == 987_654

    def test_pings_keep_being_answered(self, client):
        """The reply is not a one-off handshake artefact — it is the keepalive."""
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
                for ts in (1, 2, 3):
                    ws.send_json({"type": "ping", "ts": ts})
                    assert ws.receive_json() == {"type": "pong", "ts": ts}

    def test_a_ping_does_not_disturb_the_socket(self, client):
        """A keepalive must not knock the connection out of the pool."""
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid") as ws:
                ws.send_json({"type": "ping", "ts": 1})
                ws.receive_json()
                assert pool.active_count() == 1

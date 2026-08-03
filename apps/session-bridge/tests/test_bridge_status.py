"""GET /v1/internal/bridge-status/{user_sub} — synchronous truth, and its fence.

The route exists so memory-api can stop guessing from a timestamp. Its risk is
the mirror of its value: an endpoint that answers "does this person have a
browser open right now" is a presence oracle, so the fence around it — a bridge
JWT whose acting_user_sub equals the path — is tested as hard as the answer.
"""
from __future__ import annotations

import time

import httpx
import pytest
import respx
from authlib.jose import jwt as jose_jwt
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


def _bridge_jwt(acting_user_sub: str, *, ttl_s: int = 60, **overrides) -> str:
    now = int(time.time())
    payload = {
        "iss": "memory-api",
        "sub": "team-chat-agent",
        "scope": "bridge",
        "acting_user_sub": acting_user_sub,
        "iat": now,
        "exp": now + ttl_s,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    tok = jose_jwt.encode({"alg": "HS256"}, payload, settings.BRIDGE_SHARED_SECRET)
    return tok.decode() if isinstance(tok, bytes) else tok


def _get(client, sub: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/v1/internal/bridge-status/{sub}", headers=headers)


class TestTheAnswerIsTheSocket:
    def test_a_connected_user_is_live(self, client):
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid"):
                r = _get(client, "user-1", _bridge_jwt("user-1"))
        assert r.status_code == 200
        assert r.json()["live"] is True

    def test_a_user_with_no_socket_is_not_live(self, client):
        r = _get(client, "user-1", _bridge_jwt("user-1"))
        assert r.status_code == 200
        assert r.json()["live"] is False

    def test_the_answer_flips_the_instant_the_socket_closes(self, client):
        """The whole point: no window, no slack, no timestamp to go stale."""
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid"):
                assert _get(client, "user-1", _bridge_jwt("user-1")).json()["live"] is True
        assert _get(client, "user-1", _bridge_jwt("user-1")).json()["live"] is False

    def test_the_response_carries_nothing_but_the_answer(self, client):
        """No email, no extension id, no last-seen. A presence probe that also
        described the session would be a data leak with a routing excuse."""
        r = _get(client, "user-1", _bridge_jwt("user-1"))
        assert set(r.json()) == {"user_sub", "live"}


class TestOnlyAboutYourself:
    def test_a_token_for_one_user_cannot_ask_about_another(self, client):
        """Otherwise any service holding any bridge JWT could enumerate which
        members of which team currently have a browser open."""
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid"):
                r = _get(client, "user-1", _bridge_jwt("someone-else"))
        assert r.status_code == 403
        assert "live" not in r.text

    def test_no_token_is_refused(self, client):
        assert _get(client, "user-1", None).status_code == 401

    def test_an_xbt_token_is_not_a_bridge_jwt(self, client):
        assert _get(client, "user-1", "xbt_looks_like_a_user").status_code == 401

    def test_a_token_signed_with_the_wrong_secret_is_refused(self, client):
        forged = jose_jwt.encode(
            {"alg": "HS256"},
            {
                "iss": "memory-api",
                "scope": "bridge",
                "acting_user_sub": "user-1",
                "exp": int(time.time()) + 60,
            },
            "not-the-bridge-secret",
        )
        token = forged.decode() if isinstance(forged, bytes) else forged
        assert _get(client, "user-1", token).status_code == 401

    def test_an_expired_token_is_refused(self, client):
        assert _get(client, "user-1", _bridge_jwt("user-1", ttl_s=-10)).status_code == 401

    def test_a_token_with_no_expiry_is_refused(self, client):
        """An unbounded bridge credential is not a credential."""
        assert _get(client, "user-1", _bridge_jwt("user-1", exp=None)).status_code == 401

    def test_a_token_of_another_scope_is_refused(self, client):
        assert _get(client, "user-1", _bridge_jwt("user-1", scope="board")).status_code == 401

    def test_a_token_with_no_acting_user_is_refused(self, client):
        # A bridge JWT that names nobody must not resolve to the user in the
        # path — otherwise the path would be choosing its own authorisation.
        token = _bridge_jwt(None)
        assert _get(client, "user-1", token).status_code == 401


class TestThePoolKnowsWhoIsConnected:
    def test_a_connected_user_is_reported_live(self, client):
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid"):
                assert pool.has_socket("user-1") is True

    def test_a_disconnected_user_is_not(self, client):
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid"):
                pass
        assert pool.has_socket("user-1") is False

    def test_one_users_socket_is_not_anothers(self, client):
        """The routing decision this answers is per-user; a leak here would send
        one person's team chat through somebody else's subscription."""
        with respx.mock(assert_all_called=False) as m:
            _mock_me(m)
            with client.websocket_connect("/ws/user-1?token=xbt_valid"):
                assert pool.has_socket("user-1") is True
                assert pool.has_socket("someone-else") is False

    def test_an_unknown_user_is_not_live(self):
        assert pool.has_socket("nobody-has-ever-connected") is False

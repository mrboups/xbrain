"""The routing decision asks the bridge instead of reading a timestamp.

The bug this pins is a production one. `_user_has_live_bridge` read
`user_external_sessions.last_seen_at` and called the bridge alive if the row was
under 90 seconds old. The heartbeat stopped being written while the socket
stayed up; the row went thirty minutes stale; every mention routed to a fallback
key with no credit on it, and the team read a billing error.

The tempting repair — widen the window — is the one thing that must not happen,
and there is a test below that says so. A wider window does not find live
bridges. It makes dead ones look alive for longer, which turns a fast honest
failure into a slow one.
"""
from __future__ import annotations

import inspect

import httpx
import pytest
import respx
from authlib.jose import jwt as jose_jwt

from app.config import settings
from app.services import team_chat_agent
from app.services.team_chat_agent import BRIDGE_BASE_URL, _user_has_live_bridge

STATUS_URL = f"{BRIDGE_BASE_URL}/v1/internal/bridge-status/user-1"


class TestItAsksTheBridge:
    @pytest.mark.asyncio
    async def test_a_live_socket_routes_through_the_subscription(self):
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(
                return_value=httpx.Response(200, json={"user_sub": "user-1", "live": True})
            )
            assert await _user_has_live_bridge("user-1") is True

    @pytest.mark.asyncio
    async def test_no_socket_falls_back(self):
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(
                return_value=httpx.Response(200, json={"user_sub": "user-1", "live": False})
            )
            assert await _user_has_live_bridge("user-1") is False

    @pytest.mark.asyncio
    async def test_the_probe_authenticates_as_the_user_it_asks_about(self):
        """The bridge refuses a token naming anybody else, so a probe that signed
        for the wrong user would report every bridge dead."""
        captured: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"live": True})

        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(side_effect=_handler)
            await _user_has_live_bridge("user-1")

        assert captured["auth"].startswith("Bearer ")
        claims = jose_jwt.decode(
            captured["auth"].removeprefix("Bearer "), settings.BRIDGE_SHARED_SECRET
        )
        assert claims["acting_user_sub"] == "user-1"
        assert claims["scope"] == "bridge"
        assert claims["exp"] > claims["iat"]

    @pytest.mark.asyncio
    async def test_a_sub_with_a_slash_in_it_still_addresses_one_user(self):
        """source_user_id is "github:<login>" shaped and free-form besides. An
        unescaped one would walk out of the route and 404 every probe — which
        reads as "no bridge" and silently disables the subscription path."""
        weird = "github:some/one?x=1"
        with respx.mock(assert_all_called=False) as m:
            route = m.get(url__startswith=f"{BRIDGE_BASE_URL}/v1/internal/bridge-status/").mock(
                return_value=httpx.Response(200, json={"live": True})
            )
            assert await _user_has_live_bridge(weird) is True
        path = str(route.calls[0].request.url)
        assert path.endswith("github%3Asome%2Fone%3Fx%3D1")


class TestItFailsSafe:
    @pytest.mark.asyncio
    async def test_an_unreachable_bridge_is_not_a_live_bridge(self):
        """If this hop cannot be made, neither can the streaming one behind it."""
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(side_effect=httpx.ConnectError("no route to host"))
            assert await _user_has_live_bridge("user-1") is False

    @pytest.mark.asyncio
    async def test_a_timeout_is_not_a_live_bridge(self):
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
            assert await _user_has_live_bridge("user-1") is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 404, 500, 502])
    async def test_any_non_200_is_not_a_live_bridge(self, status: int):
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(return_value=httpx.Response(status, json={"live": True}))
            assert await _user_has_live_bridge("user-1") is False

    @pytest.mark.asyncio
    async def test_a_malformed_body_is_not_a_live_bridge(self):
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(return_value=httpx.Response(200, text="<html>nope"))
            assert await _user_has_live_bridge("user-1") is False

    @pytest.mark.asyncio
    async def test_a_missing_live_key_is_not_a_live_bridge(self):
        with respx.mock(assert_all_called=False) as m:
            m.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"user_sub": "user-1"}))
            assert await _user_has_live_bridge("user-1") is False


class TestTheGuessIsGone:
    def test_the_ninety_second_window_is_not_still_in_the_probe(self):
        # Asserted on the query, not on the prose: the docstring explains the
        # window at length precisely so nobody reintroduces it, and a test that
        # banned the words would forbid the explanation along with the bug.
        source = inspect.getsource(_user_has_live_bridge)
        assert "interval '90 seconds'" not in source, (
            "the timestamp heuristic is back — a socket the bridge does not have "
            "would read as live for another 90 seconds"
        )
        assert "FROM user_external_sessions" not in source, (
            "the probe is reading the session row again instead of asking the "
            "process that holds the socket"
        )

    def test_both_hops_go_to_the_same_bridge(self):
        """The routing decision and the request it authorises must reach one
        process. Two base URLs is a live bridge on one host and a chat request
        posted to another."""
        source = inspect.getsource(team_chat_agent)
        assert source.count("http://session-bridge:8105") == 1, (
            "the bridge host is written more than once — the liveness probe and "
            "the streaming call can drift onto different hosts"
        )

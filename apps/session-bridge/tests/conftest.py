"""Shared fixtures for session-bridge tests."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from app import auth as auth_mod
from app.config import settings
from app.pool import pool


@pytest.fixture(autouse=True)
def _clear_state_between_tests():
    """Clear the token cache + pool between tests so order doesn't matter."""
    auth_mod._reset_cache_for_tests()
    pool._reset_for_tests()
    yield
    auth_mod._reset_cache_for_tests()
    pool._reset_for_tests()


@pytest.fixture(autouse=True)
def _bridge_secret(monkeypatch):
    """Inject a non-empty BRIDGE_SHARED_SECRET so JWT-signing tests don't short-circuit.

    Individual tests that need the empty-secret path (e.g. test_upsert_returns_false_without_secret)
    can monkeypatch settings.BRIDGE_SHARED_SECRET back to "" inside the test body.
    """
    monkeypatch.setattr(settings, "BRIDGE_SHARED_SECRET", "test-secret-do-not-use-in-prod")
    monkeypatch.setattr(settings, "MEMORY_API_URL", "http://memory-api-test:8000")
    yield


class FakeWebSocket:
    """Minimal stand-in for fastapi.WebSocket used by Pool tests.

    Records every send_json call. Doesn't implement receive_json / accept /
    close — those are exercised in integration-style tests with the real
    TestClient (see test_chat.py for HTTP path).
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.fixture
def fake_ws() -> FakeWebSocket:
    return FakeWebSocket()


@pytest_asyncio.fixture
async def loop():
    """Convenience — pytest-asyncio in auto mode already provides one but some tests want it explicit."""
    return asyncio.get_event_loop()

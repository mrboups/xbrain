"""Tests for app.services.rate_limit (pure unit, no Docker) and app.services.api_tokens
(mint_xbt_for_user — @pytest.mark.integration, needs the real user_api_tokens table).

Covers every <behavior> bullet in 18-02-PLAN.md Task 2.
"""

import hashlib

import pytest
from fastapi import HTTPException

from app.services.rate_limit import check_rate, enforce_rate_limit


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


class _FakeRequest:
    """Duck-types the subset of fastapi.Request that enforce_rate_limit touches."""

    def __init__(self, host: str | None):
        self.client = _FakeClient(host) if host is not None else None


# === check_rate (pure unit) ===


def test_check_rate_allows_then_refuses_within_window():
    results = [check_rate("3/minute", "test-bucket-a", "ip-1.2.3.4") for _ in range(4)]
    assert results == [True, True, True, False]


def test_check_rate_independent_keys_have_independent_budgets():
    for _ in range(3):
        assert check_rate("3/minute", "test-bucket-b", "ip-a") is True
    # ip-a is now exhausted; ip-b (different key) starts fresh.
    assert check_rate("3/minute", "test-bucket-b", "ip-a") is False
    assert check_rate("3/minute", "test-bucket-b", "ip-b") is True


# === enforce_rate_limit (pure unit — FastAPI dependency shape) ===


@pytest.mark.asyncio
async def test_enforce_rate_limit_raises_429_once_exhausted():
    request = _FakeRequest("9.9.9.9")
    # First 2 calls under a 2/minute budget succeed silently.
    await enforce_rate_limit(request, "2/minute", "test-enforce")
    await enforce_rate_limit(request, "2/minute", "test-enforce")
    # 3rd call exceeds the budget -> 429.
    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(request, "2/minute", "test-enforce")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_enforce_rate_limit_no_client_falls_back_to_unknown():
    request = _FakeRequest(None)
    # Should not raise while under budget, even with no request.client.
    await enforce_rate_limit(request, "5/minute", "test-enforce-noclient")


# === mint_xbt_for_user (integration — real user_api_tokens table) ===


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mint_xbt_for_user_inserts_correct_row(session, seeded_two_teams):
    from app.services.api_tokens import mint_xbt_for_user

    alice = seeded_two_teams["alice"]
    raw = await mint_xbt_for_user(session, alice.id, name="local-register")
    await session.commit()

    assert raw.startswith("xbt_")
    expected_hash = hashlib.sha256(raw.encode()).hexdigest()

    import sqlalchemy as sa

    row = (
        await session.execute(
            sa.text("SELECT token_hash, team_scope, name FROM user_api_tokens WHERE user_id = :uid"),
            {"uid": str(alice.id)},
        )
    ).mappings().fetchone()

    assert row is not None
    assert row["token_hash"] == expected_hash
    assert row["team_scope"] == ""
    assert row["name"] == "local-register"

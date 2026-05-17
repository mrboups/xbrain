"""Tests for app.auth.check_github_org_membership using installation tokens.

Plan 12-04 Task 4 — covers the three membership states (MEMBER, NOT_MEMBER,
INSTALL_REQUIRED), the cache behaviour, the 401 retry path (M-4 fix), and the
B-2 regression: the LibreChat link-github flow (app/routes/me_github.py) still
works after the helper signature changed in Task 1.

Mocking strategy mirrors tests/test_phase12_installation_token.py — respx
intercepts every GitHub HTTPS call; the App private key is generated per-test
via cryptography so the real prod key is never loaded in unit tests.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import OrgMembershipResult, check_github_org_membership
from app.models.installation import Installation
from app.services import github_app_jwt, github_installation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# === Fixtures ================================================================


@pytest.fixture
def app_pem_b64() -> str:
    """Generate a fresh RSA key per test, return base64-encoded PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


@pytest.fixture(autouse=True)
def _configure(monkeypatch, app_pem_b64):
    """Configure GITHUB_APP_* settings + reset module caches each test."""
    monkeypatch.setattr(
        github_app_jwt.settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test",
    )
    monkeypatch.setattr(
        github_app_jwt.settings, "GITHUB_APP_PRIVATE_KEY_B64", app_pem_b64,
    )
    github_app_jwt._reset_private_key_cache_for_tests()
    github_installation._reset_caches_for_tests()
    # Clear auth.py membership cache too — module-global dict survives tests.
    from app import auth as auth_module
    auth_module._github_membership_cache.clear()
    yield
    github_installation._reset_caches_for_tests()
    github_app_jwt._reset_private_key_cache_for_tests()


# === Helpers =================================================================


def _expires_at_iso(hours: int = 1) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )


# === State 1: MEMBER =========================================================


@respx.mock
async def test_check_github_org_membership_returns_MEMBER_on_204(session):
    """Installed org + 204 from /orgs/{org}/members/{user} → MEMBER."""
    session.add(Installation(
        installation_id=100, github_org_login="dejavudev",
        github_account_type="Organization", permissions={}, raw_payload={},
    ))
    await session.commit()
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1001, "login": "alice", "email": "a@x.io", "name": "Alice"},
        ),
    )
    respx.post(
        "https://api.github.com/app/installations/100/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_test", "expires_at": _expires_at_iso()},
        ),
    )
    respx.get("https://api.github.com/orgs/dejavudev/members/alice").mock(
        return_value=httpx.Response(204),
    )

    res = await check_github_org_membership(session, "ghu_user_token", "dejavudev")

    assert res["is_org_member"] is True
    assert res["install_required"] is False
    assert res["result"] == OrgMembershipResult.MEMBER
    assert res["login"] == "alice"
    assert res["github_id"] == 1001
    assert res["email"] == "a@x.io"
    assert res["name"] == "Alice"


# === State 2: NOT_MEMBER =====================================================


@respx.mock
async def test_check_github_org_membership_returns_NOT_MEMBER_on_404(session):
    """Installed org + 404 from members endpoint → NOT_MEMBER."""
    session.add(Installation(
        installation_id=101, github_org_login="dejavudev",
        github_account_type="Organization", permissions={}, raw_payload={},
    ))
    await session.commit()
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1002, "login": "bob", "email": None, "name": "Bob"},
        ),
    )
    respx.post(
        "https://api.github.com/app/installations/101/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_test", "expires_at": _expires_at_iso()},
        ),
    )
    respx.get("https://api.github.com/orgs/dejavudev/members/bob").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}),
    )

    res = await check_github_org_membership(session, "ghu_user_token", "dejavudev")

    assert res["is_org_member"] is False
    assert res["install_required"] is False
    assert res["result"] == OrgMembershipResult.NOT_MEMBER
    assert res["login"] == "bob"
    assert res["github_id"] == 1002


# === State 3: INSTALL_REQUIRED ===============================================


@respx.mock
async def test_check_github_org_membership_returns_INSTALL_REQUIRED_when_no_installation(session):
    """App not installed on org → INSTALL_REQUIRED, no /members/... call."""
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1003, "login": "carol", "email": "c@x.io", "name": "Carol"},
        ),
    )
    # DB lookup returns nothing → fallback to GitHub returns 404 → app not installed.
    respx.get("https://api.github.com/orgs/randomorg/installation").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}),
    )

    res = await check_github_org_membership(session, "ghu_user_token", "randomorg")

    assert res["is_org_member"] is False
    assert res["install_required"] is True
    assert res["result"] == OrgMembershipResult.INSTALL_REQUIRED
    assert res["login"] == "carol"
    assert res["github_id"] == 1003


# === Cache behaviour =========================================================


@respx.mock
async def test_check_github_org_membership_cached_on_second_call(session):
    """Two calls within TTL reuse the cached result, no second /user call."""
    session.add(Installation(
        installation_id=102, github_org_login="dejavudev",
        github_account_type="Organization", permissions={}, raw_payload={},
    ))
    await session.commit()
    user_route = respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1004, "login": "dave", "email": None, "name": None},
        ),
    )
    respx.post(
        "https://api.github.com/app/installations/102/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_test", "expires_at": _expires_at_iso()},
        ),
    )
    respx.get("https://api.github.com/orgs/dejavudev/members/dave").mock(
        return_value=httpx.Response(204),
    )

    await check_github_org_membership(session, "ghu_user_token_xx", "dejavudev")
    await check_github_org_membership(session, "ghu_user_token_xx", "dejavudev")

    assert user_route.call_count == 1, (
        "cache hit on second call should skip /user"
    )


# === 401 retry path (M-4 fix) =================================================


@respx.mock
async def test_check_github_org_membership_retries_on_401_with_force_refresh(session):
    """REVISION 2 (M-4 fix) — 401 from /members/... triggers force-refresh retry.

    The first /members/... call returns 401 (installation token revoked between
    mint and use). check_github_org_membership force-refreshes the installation
    token and retries; the second call returns 204 → MEMBER.
    """
    session.add(Installation(
        installation_id=103, github_org_login="dejavudev",
        github_account_type="Organization", permissions={}, raw_payload={},
    ))
    await session.commit()
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1005, "login": "eve", "email": None, "name": None},
        ),
    )
    # Mint endpoint hit twice (first cache fill, then force-refresh on retry).
    mint_route = respx.post(
        "https://api.github.com/app/installations/103/access_tokens",
    ).mock(
        side_effect=[
            httpx.Response(
                201, json={"token": "ghs_first", "expires_at": _expires_at_iso()},
            ),
            httpx.Response(
                201, json={"token": "ghs_second", "expires_at": _expires_at_iso()},
            ),
        ],
    )
    # Members endpoint: first 401 → retry with fresh token → 204.
    members_route = respx.get(
        "https://api.github.com/orgs/dejavudev/members/eve",
    ).mock(
        side_effect=[
            httpx.Response(401, json={"message": "Bad credentials"}),
            httpx.Response(204),
        ],
    )

    res = await check_github_org_membership(session, "ghu_user_token_zz", "dejavudev")

    assert res["is_org_member"] is True, (
        "should succeed after force-refresh retry"
    )
    assert res["result"] == OrgMembershipResult.MEMBER
    assert mint_route.call_count == 2, (
        "expected 2 mint calls: initial + force-refresh after 401"
    )
    assert members_route.call_count == 2, (
        "expected 2 members calls: 401 + 204 retry"
    )


# === B-2 regression: link-github flow integration ============================


@respx.mock
async def test_link_github_route_with_gho_token_returns_200(session, client):
    """REVISION 2 (B-2 fix) — LibreChat link-github (gho_) flow still works.

    Simulates the Phase 5 LibreChat → POST /v1/me/link-github call after Phase 12.
    A Google-authenticated user POSTs their gho_ token; the route should:
      1. Call check_github_org_membership with the new (session, token, org) signature.
      2. Return the same shape as before, plus the new install_required field.
      3. Persist github_username + github_id on the users row.

    Without the B-2 fix in me_github.py, the call would raise TypeError on the
    old 3-arg call shape and the endpoint would 500.
    """
    # Setup: a Google-authenticated user (no github_id linked yet).
    from app.repos.users import get_or_create_user

    google_user = await get_or_create_user(
        session,
        source_user_id="google:54321",
        email="g@example.com",
        display_name="Google User",
    )
    await session.commit()

    # The xbrain App is installed on the configured org (GITHUB_ORG default).
    from app.config import settings
    target_org = settings.GITHUB_ORG
    session.add(Installation(
        installation_id=200, github_org_login=target_org,
        github_account_type="Organization", permissions={}, raw_payload={},
    ))
    await session.commit()

    # Mock the full GitHub call chain.
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 9999, "login": "ghuser", "email": "gh@x.io", "name": "GH User"},
        ),
    )
    respx.post(
        "https://api.github.com/app/installations/200/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_test", "expires_at": _expires_at_iso()},
        ),
    )
    respx.get(
        f"https://api.github.com/orgs/{target_org}/members/ghuser",
    ).mock(return_value=httpx.Response(204))

    # Forge a Google ID token for the call. We bypass real Google JWKS verify
    # by monkeypatching verify_google_id_token at the auth module — the route
    # itself doesn't care about the token type, only that get_current_principal
    # returns a "user"-kind principal with the right user attached.
    import app.deps as deps_module

    async def _fake_principal(authorization, session):
        return {
            "kind": "user",
            "user": google_user,
            "claims": {"sub": "google:54321"},
            "sub": "google:54321",
            "github_is_org_member": None,
        }

    from app.main import app
    app.dependency_overrides[deps_module.get_current_principal] = _fake_principal

    try:
        r = await client.post(
            "/v1/me/link-github",
            json={"github_token": "gho_librechat_token"},
            headers={"Authorization": "Bearer fake-google-jwt"},
        )
    finally:
        app.dependency_overrides.pop(deps_module.get_current_principal, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["github_username"] == "ghuser"
    assert body["github_id"] == 9999
    assert body["is_org_member"] is True
    # install_required field added by Plan 12-04 Task 2c.
    assert body.get("install_required") is False

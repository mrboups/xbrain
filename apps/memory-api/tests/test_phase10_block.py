"""Integration tests for Phase 10 block / org-block endpoints + 403 enforcement.

Strategy: bypass auth by overriding get_current_principal with a fake admin /
non-admin principal. Same pattern as test_team_isolation.py — set
`app.dependency_overrides[get_current_principal] = _override_principal(user)`
before each call. The `client` fixture in conftest already clears overrides
on teardown.

Phase 10 specific:
- test_xbt_token_blocked_user_gets_403 is the REVISION 1 B-3 regression test
  (multi-team xbt_ token whose owner was blocked AFTER minting). The deps.py
  fix must trip the 403 in both the user_api_token branch and the user branch.
"""

import types

import pytest

pytestmark = pytest.mark.integration


def _get_app_and_dep():
    """Lazy import — app.main pulls in qdrant_client + other heavy deps that
    aren't required at collection time. Importing here keeps test collection
    green on dev hosts without the full integration env (Docker, qdrant)."""
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_override(user, *, kind="user_api_token", api_token_team_scope=None):
    """Install a principal override on the FastAPI app — wraps the lazy import
    and the dependency_overrides assignment in one call."""
    app, get_current_principal = _get_app_and_dep()
    app.dependency_overrides[get_current_principal] = _override_principal(
        user, kind=kind, api_token_team_scope=api_token_team_scope
    )


def _override_principal(user, *, kind="user_api_token", api_token_team_scope=None):
    """Override get_current_principal to return a fake principal backed by the
    given user row. Defaults mirror deps.py's xbt_ branch shape; pass
    kind='user' to test the gho_/Google branch.

    api_token_team_scope:
      - None → multi-team token (matches the '' sentinel meaning in deps.py
        after the empty-string→None normalisation, i.e. falls through to
        the user-branch membership check).
      - "team-a" → single-team scoped token (early-return path in deps.py
        after Phase 10 B-3 added the blocked_at check).
    """
    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=user.source_user_id,
        email=user.email,
        display_name=user.display_name,
        github_username=getattr(user, "github_username", None),
        github_id=getattr(user, "github_id", None),
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": user.source_user_id,
            "api_token_team_scope": api_token_team_scope,
            "github_is_org_member": None,
        }

    return _override


async def test_block_then_403_on_team_scope(client, session, seeded_two_teams):
    """Admin blocks Bob → Bob's request with X-Team-Scope returns 403."""
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=bob.id, role="member"
    )
    await session.commit()

    # Admin (alice) blocks bob.
    _install_override(alice, kind="user")
    r = await client.post(f"/v1/teams/{team_a.id}/members/{bob.id}/block")
    assert r.status_code == 200, r.text
    assert r.json()["blocked_at"] is not None

    # Bob now tries to use team-scoped route — the gho_/user branch.
    _install_override(bob, kind="user")
    r = await client.get(
        "/v1/memory/search?q=x", headers={"X-Team-Scope": "team-a"}
    )
    assert r.status_code == 403, r.text
    assert "blocked" in r.text.lower()


async def test_block_refuses_non_admin(client, session, seeded_two_teams):
    """Non-admin caller cannot block."""
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=bob.id, role="member"
    )
    await session.commit()

    _install_override(bob, kind="user")
    r = await client.post(f"/v1/teams/{team_a.id}/members/{alice.id}/block")
    assert r.status_code == 403


async def test_unblock_restores_access(client, session, seeded_two_teams):
    """After unblock, blocked_at is null + scoped calls succeed again."""
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=bob.id, role="member"
    )
    await teams_repo.block_member(
        session, team_id=team_a.id, user_id=bob.id, blocked_by=alice.id
    )
    await session.commit()

    _install_override(alice, kind="user")
    r = await client.post(f"/v1/teams/{team_a.id}/members/{bob.id}/unblock")
    assert r.status_code == 200, r.text
    assert r.json()["blocked_at"] is None


async def test_block_last_admin_refused(client, session, seeded_two_teams):
    """Cannot block the team's last admin — would orphan the team."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    _install_override(alice, kind="user")
    # alice is the only admin of team-a (creator).
    r = await client.post(f"/v1/teams/{team_a.id}/members/{alice.id}/block")
    assert r.status_code == 409, r.text


async def test_org_block_crud(client, session, seeded_two_teams):
    """Create + list + delete pre-block by github_login."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await session.commit()

    _install_override(alice, kind="user")
    # Create.
    r = await client.post(
        f"/v1/teams/{team_a.id}/org-blocks", json={"github_login": "evilbot"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["github_login"] == "evilbot"
    assert r.json()["blocked_at"]
    # List.
    r = await client.get(f"/v1/teams/{team_a.id}/org-blocks")
    assert r.status_code == 200
    payload = r.json()
    assert any(b["github_login"] == "evilbot" for b in payload)
    # Delete.
    r = await client.delete(f"/v1/teams/{team_a.id}/org-blocks/evilbot")
    assert r.status_code == 204
    # Confirm gone.
    r = await client.get(f"/v1/teams/{team_a.id}/org-blocks")
    assert all(b["github_login"] != "evilbot" for b in r.json())


async def test_org_block_create_refuses_non_admin(client, session, seeded_two_teams):
    """Non-admin can't create org-blocks."""
    team_a = seeded_two_teams["team_a"]
    bob = seeded_two_teams["bob"]  # not in team-a

    _install_override(bob, kind="user")
    r = await client.post(
        f"/v1/teams/{team_a.id}/org-blocks", json={"github_login": "x"}
    )
    assert r.status_code == 403


async def test_xbt_token_blocked_user_gets_403(client, session, seeded_two_teams):
    """REVISION 1 B-3 regression test — MULTI-TEAM xbt_ branch.

    A user mints a multi-team xbt_ token (api_token_team_scope=None — the
    '' sentinel in user_api_tokens). An admin then blocks the user's
    team_members row. The user-branch fall-through path of get_team_scope
    MUST consult team_members.blocked_at and return 403 — otherwise the
    pre-minted token bypasses block enforcement entirely.
    """
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=bob.id, role="member"
    )
    await teams_repo.block_member(
        session, team_id=team_a.id, user_id=bob.id, blocked_by=alice.id
    )
    await session.commit()

    # Bob presents a multi-team xbt_ token (api_token_team_scope=None) targeting team-a.
    _install_override(bob, kind="user_api_token", api_token_team_scope=None)
    r = await client.get(
        "/v1/memory/search?q=x", headers={"X-Team-Scope": "team-a"}
    )
    assert r.status_code == 403, (
        f"B-3 regression: multi-team xbt_ token bypassed blocked_at check "
        f"(got {r.status_code}, expected 403). Body: {r.text}"
    )
    assert "blocked" in r.text.lower()


async def test_xbt_token_scoped_to_team_blocked_user_gets_403(
    client, session, seeded_two_teams
):
    """B-3 sibling — SCOPED xbt_ branch (single-team).

    Same scenario as above but with api_token_team_scope='team-a' (the
    single-team scoped-token early-return path in deps.py). Block must
    still fire — the scoped-token branch now also queries blocked_at.
    """
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=bob.id, role="member"
    )
    await teams_repo.block_member(
        session, team_id=team_a.id, user_id=bob.id, blocked_by=alice.id
    )
    await session.commit()

    _install_override(bob, kind="user_api_token", api_token_team_scope="team-a")
    r = await client.get(
        "/v1/memory/search?q=x", headers={"X-Team-Scope": "team-a"}
    )
    assert r.status_code == 403, r.text
    assert "blocked" in r.text.lower()

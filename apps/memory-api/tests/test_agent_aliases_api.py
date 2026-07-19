"""Integration tests for GET/PATCH /v1/teams/{id}/agent-aliases (Phase 21 / 21-02).

Real Postgres (testcontainer) via the `client` + `seeded_two_teams` fixtures.
These prove the two endpoints that make the extension client and the memory-api
server share ONE source of truth for a team's mention aliases:

  1. Any team MEMBER can GET the effective alias list (admin AND a plain member).
  2. A team ADMIN PATCH round-trips and takes effect with NO restart — the very
     next GET reflects the new alias (detection reads the DB per request).
  3. A non-admin member (carol) and a non-member admin (bob) both get 403 on PATCH.
  4. Cross-team isolation: team-a's custom alias never appears in team-b's list.
  5. Validation → 422 for bad charset / >32 chars / empty / 'claude' / >8 items;
     a leading '@' is stripped and the alias is stored without it.

Auth is faked by overriding get_current_principal (same pattern as
test_admin_brain.py) so a specific seeded user drives each call.
"""
from __future__ import annotations

import types

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (mirror test_admin_brain.py) ──────────────────


def _install_principal(user, *, kind: str = "user") -> None:
    from app.deps import get_current_principal
    from app.main import app

    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=getattr(user, "source_user_id", None),
        email=getattr(user, "email", None),
        display_name=getattr(user, "display_name", None),
        github_username=getattr(user, "github_username", None),
        github_id=getattr(user, "github_id", None),
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": fake_user.source_user_id,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


async def _add_member(session, *, team_id, source_user_id, email, role):
    """Create a user and add them to a team with the given role; commit + return."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    u = await users_repo.get_or_create_user(
        session, source_user_id=source_user_id, email=email, display_name=source_user_id
    )
    await teams_repo.add_member(session, team_id=team_id, user_id=u.id, role=role)
    await session.commit()
    return u


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_member_can_get_effective_aliases(client, seeded_two_teams, session):
    """Case 1 — admin and a plain member can both GET the effective list."""
    alice = seeded_two_teams["alice"]  # admin of team-a (also a member)
    team_a = seeded_two_teams["team_a"]

    _install_principal(alice)
    try:
        r = await client.get(f"/v1/teams/{team_a.id}/agent-aliases")
    finally:
        _clear_principal()
    assert r.status_code == 200, r.text
    assert "agent" in r.json()["aliases"]  # env default always present

    # A plain member (role='member') can GET too.
    dave = await _add_member(
        session, team_id=team_a.id, source_user_id="dave-sub",
        email="dave@test.local", role="member",
    )
    _install_principal(dave)
    try:
        r2 = await client.get(f"/v1/teams/{team_a.id}/agent-aliases")
    finally:
        _clear_principal()
    assert r2.status_code == 200, r2.text
    assert "agent" in r2.json()["aliases"]


async def test_admin_patch_round_trip_no_restart(client, seeded_two_teams):
    """Case 2 — admin PATCH takes effect immediately (no restart)."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_principal(alice)
    try:
        patch = await client.patch(
            f"/v1/teams/{team_a.id}/agent-aliases", json={"aliases": ["wizard"]}
        )
        assert patch.status_code == 200, patch.text
        patched = patch.json()["aliases"]
        assert "wizard" in patched and "agent" in patched

        # No restart step — a fresh GET reflects the change (read from the DB).
        get = await client.get(f"/v1/teams/{team_a.id}/agent-aliases")
    finally:
        _clear_principal()
    assert get.status_code == 200, get.text
    aliases = get.json()["aliases"]
    assert "agent" in aliases and "wizard" in aliases


async def test_non_admin_patch_forbidden(client, seeded_two_teams, session):
    """Case 3 — a non-admin member and a non-member admin both get 403."""
    team_a = seeded_two_teams["team_a"]
    bob = seeded_two_teams["bob"]  # admin of team-b, NOT a member of team-a

    # carol is a plain member of team-a → 403.
    carol = await _add_member(
        session, team_id=team_a.id, source_user_id="carol-sub",
        email="carol@test.local", role="member",
    )
    _install_principal(carol)
    try:
        r = await client.patch(
            f"/v1/teams/{team_a.id}/agent-aliases", json={"aliases": ["wizard"]}
        )
    finally:
        _clear_principal()
    assert r.status_code == 403, r.text

    # bob is an admin — but of team-b, not team-a → still 403 on team-a.
    _install_principal(bob)
    try:
        r2 = await client.patch(
            f"/v1/teams/{team_a.id}/agent-aliases", json={"aliases": ["wizard"]}
        )
    finally:
        _clear_principal()
    assert r2.status_code == 403, r2.text


async def test_cross_team_isolation(client, seeded_two_teams):
    """Case 4 — team-a's custom alias never appears in team-b's effective list."""
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    _install_principal(alice)
    try:
        p = await client.patch(
            f"/v1/teams/{team_a.id}/agent-aliases", json={"aliases": ["wizard"]}
        )
        assert p.status_code == 200, p.text
    finally:
        _clear_principal()

    _install_principal(bob)
    try:
        g = await client.get(f"/v1/teams/{team_b.id}/agent-aliases")
    finally:
        _clear_principal()
    assert g.status_code == 200, g.text
    team_b_aliases = g.json()["aliases"]
    assert "wizard" not in team_b_aliases  # isolation
    assert "agent" in team_b_aliases  # default still present


async def test_validation_422(client, seeded_two_teams):
    """Case 5 — bad aliases are rejected 422; a leading '@' is stripped on accept."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    bad_bodies = [
        {"aliases": ["a*b"]},                                    # regex metachar
        {"aliases": ["x" * 33]},                                 # too long (>32)
        {"aliases": [""]},                                       # empty after trim
        {"aliases": ["claude"]},                                 # reserved word
        {"aliases": ["a", "b", "c", "d", "e", "f", "g", "h", "i"]},  # 9 items (>8)
    ]

    _install_principal(alice)
    try:
        for body in bad_bodies:
            r = await client.patch(
                f"/v1/teams/{team_a.id}/agent-aliases", json=body
            )
            assert r.status_code == 422, f"{body} -> {r.status_code} {r.text}"

        # A leading '@' is stripped; the alias is stored/returned without it.
        ok = await client.patch(
            f"/v1/teams/{team_a.id}/agent-aliases", json={"aliases": ["@wizard"]}
        )
        assert ok.status_code == 200, ok.text
        assert "wizard" in ok.json()["aliases"]

        get = await client.get(f"/v1/teams/{team_a.id}/agent-aliases")
    finally:
        _clear_principal()
    assert get.status_code == 200, get.text
    got = get.json()["aliases"]
    assert "wizard" in got
    assert "@wizard" not in got  # stored without the '@'

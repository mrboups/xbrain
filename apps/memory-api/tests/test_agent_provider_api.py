"""GET/PATCH /v1/teams/{id}/agent-provider against a real Postgres.

The endpoints a team's settings UI drives to choose which provider the agent
falls back to. Real container via the `client` + `seeded_two_teams` fixtures, so
the CHECK constraint added by migration 0033 is in force under every write here —
a route that validated nothing would still be caught by the database, and a route
that validated wrongly would be caught by the 422 assertions.

What each case is defending:

  1. Any active MEMBER may read the selection. It is not a secret; the team chose
     it, and a member who cannot see it cannot understand why the agent answered
     the way it did.
  2. A team ADMIN's PATCH round-trips with no restart. The agent reads the column
     per turn.
  3. A non-admin member and a non-member admin are both refused.
  4. A BLOCKED admin is refused. `blocked_at` was enforced on every team-scoped
     data path and not on the admin gate, so being blocked took away a person's
     access to the team's content while leaving them able to administer it —
     including, now, choosing whose money the agent spends.
  5. Anything outside the closed set is 422 and nothing is stored.
  6. `available` tells the client whether a selection has a key behind it, without
     revealing the key or which tier it came from.
  7. One team's selection never leaks into another's.
"""
from __future__ import annotations

import types

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (mirror test_agent_aliases_api.py) ─────────────


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
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    u = await users_repo.get_or_create_user(
        session, source_user_id=source_user_id, email=email, display_name=source_user_id
    )
    await teams_repo.add_member(session, team_id=team_id, user_id=u.id, role=role)
    await session.commit()
    return u


@pytest.fixture(autouse=True)
def _clean_key_cache():
    from app.services import team_keys

    team_keys._reset_cache_for_tests()
    yield
    team_keys._reset_cache_for_tests()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_a_team_that_never_chose_reads_as_anthropic(client, seeded_two_teams):
    """Case 1 — and the default is today's behaviour, not a new one."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_principal(alice)
    try:
        r = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "anthropic"
    assert set(body["supported"]) == {"anthropic", "openai", "xai"}
    assert body["labels"]["xai"] == "Grok"


async def test_a_plain_member_can_read_it_too(client, seeded_two_teams, session):
    dave = await _add_member(
        session, team_id=seeded_two_teams["team_a"].id, source_user_id="prov-dave",
        email="prov-dave@test.local", role="member",
    )
    _install_principal(dave)
    try:
        r = await client.get(
            f"/v1/teams/{seeded_two_teams['team_a'].id}/agent-provider"
        )
    finally:
        _clear_principal()
    assert r.status_code == 200, r.text


async def test_admin_patch_round_trips_with_no_restart(client, seeded_two_teams):
    """Case 2 — the very next GET reflects it; the agent reads the column per turn."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_principal(alice)
    try:
        patch = await client.patch(
            f"/v1/teams/{team_a.id}/agent-provider", json={"provider": "openai"}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["provider"] == "openai"
        get = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()
    assert get.json()["provider"] == "openai"


async def test_case_and_whitespace_are_forgiven_but_stored_canonically(
    client, seeded_two_teams
):
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_principal(alice)
    try:
        r = await client.patch(
            f"/v1/teams/{team_a.id}/agent-provider", json={"provider": "  XAI "}
        )
    finally:
        _clear_principal()
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "xai", "a canonical column, not what was typed"


async def test_non_admins_cannot_choose_who_gets_paid(
    client, seeded_two_teams, session
):
    """Case 3 — a plain member and an admin of ANOTHER team are both refused."""
    team_a = seeded_two_teams["team_a"]
    bob = seeded_two_teams["bob"]  # admin of team-b only

    carol = await _add_member(
        session, team_id=team_a.id, source_user_id="prov-carol",
        email="prov-carol@test.local", role="member",
    )
    for user in (carol, bob):
        _install_principal(user)
        try:
            r = await client.patch(
                f"/v1/teams/{team_a.id}/agent-provider", json={"provider": "openai"}
            )
        finally:
            _clear_principal()
        assert r.status_code == 403, f"{user.source_user_id}: {r.text}"


async def test_a_blocked_admin_can_neither_read_nor_choose(
    client, seeded_two_teams, session
):
    """Case 4 — the bypass this project has shipped once already.

    Being blocked already removed this person's access to the team's messages and
    its brain. Leaving the admin gate open meant they could still invite, remove,
    unblock themselves, and now point the team's agent at a provider. A block the
    blocked person can undo is not a block.
    """
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    erin = await _add_member(
        session, team_id=team_a.id, source_user_id="prov-erin",
        email="prov-erin@test.local", role="admin",
    )
    # Sanity: an ACTIVE admin can do it, so the refusal below is about the block.
    _install_principal(erin)
    try:
        ok = await client.patch(
            f"/v1/teams/{team_a.id}/agent-provider", json={"provider": "xai"}
        )
    finally:
        _clear_principal()
    assert ok.status_code == 200, ok.text

    await teams_repo.block_member(
        session, team_id=team_a.id, user_id=erin.id, blocked_by=alice.id
    )
    await session.commit()

    _install_principal(erin)
    try:
        patched = await client.patch(
            f"/v1/teams/{team_a.id}/agent-provider", json={"provider": "openai"}
        )
        read = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()
    assert patched.status_code == 403, patched.text
    assert read.status_code == 403, read.text

    # And the refusal actually held — the selection is still what it was.
    _install_principal(alice)
    try:
        after = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()
    assert after.json()["provider"] == "xai"


async def test_an_unsupported_provider_is_refused_and_nothing_is_stored(
    client, seeded_two_teams
):
    """Case 5 — free text must never reach a column read on the billing path."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_principal(alice)
    try:
        for bad in ["gemini", "ANTHROPIC; DROP TABLE teams", "", "x" * 64, "claude"]:
            r = await client.patch(
                f"/v1/teams/{team_a.id}/agent-provider", json={"provider": bad}
            )
            assert r.status_code == 422, f"{bad!r} -> {r.status_code} {r.text}"
        # The rejected value is not echoed by OUR refusal. (Pydantic's own
        # length/type errors do echo `input`, uniformly across this API — that is
        # the framework's contract, not a decision this endpoint gets to make.)
        for bad in ["gemini", "ANTHROPIC; DROP TABLE teams", "claude"]:
            r = await client.patch(
                f"/v1/teams/{team_a.id}/agent-provider", json={"provider": bad}
            )
            assert bad not in r.text, r.text
            assert "anthropic, openai, xai" in r.text, "say what IS accepted"
        # An unknown field is refused too (extra="forbid").
        extra = await client.patch(
            f"/v1/teams/{team_a.id}/agent-provider",
            json={"provider": "openai", "api_key": "sk-should-not-be-here"},
        )
        assert extra.status_code == 422, extra.text
        still = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()
    assert still.json()["provider"] == "anthropic", "a refusal wrote something anyway"


async def test_available_reflects_a_stored_key_without_revealing_it(
    client, seeded_two_teams, session, monkeypatch
):
    """Case 6 — what the settings UI renders its "no key for this" warning from."""
    from cryptography.fernet import Fernet

    from app.config import settings as app_settings

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    # PLUMBING, not a logic stub. `resolve_fallback_key` opens its OWN pooled
    # connection, which under the rollback-transaction fixture cannot see rows the
    # request session "committed" (a savepoint release inside the still-open outer
    # transaction). Repointing the factory at THIS test's session makes the real
    # lookup run against the real table through the same transaction — the query,
    # the decrypt and the tier ordering are all still the production ones.
    class _SameSession:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "app.services.team_keys.async_session_factory", lambda: _SameSession()
    )
    monkeypatch.setattr(app_settings, "FERNET_KEY", Fernet.generate_key().decode())
    # No deployment-wide key for any provider, so `available` can only come from
    # what the team itself stored.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
        monkeypatch.setattr(app_settings, var, "")

    _install_principal(alice)
    try:
        before = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
        assert before.json()["available"] == []

        put = await client.put(
            f"/v1/teams/{team_a.id}/api-keys",
            json={"keys": [{"provider": "openai", "api_key": "sk-openai-secret-1234"}]},
        )
        assert put.status_code == 204, put.text

        after = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()

    body = after.json()
    assert body["available"] == ["openai"]
    assert "sk-openai-secret-1234" not in after.text, "the key crossed the boundary"


async def test_the_deployment_key_counts_as_available_for_that_provider_only(
    client, seeded_two_teams, monkeypatch
):
    """A team with no key of its own still answers if the operator configured one
    — but only for THAT provider. Reporting anthropic as available for an OpenAI
    selection is how a team ends up billed by a vendor they did not choose."""
    from app.config import settings as app_settings

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    monkeypatch.setattr(app_settings, "ANTHROPIC_API_KEY", "sk-ant-platform-0000")
    monkeypatch.setattr(app_settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(app_settings, "XAI_API_KEY", "")

    _install_principal(alice)
    try:
        r = await client.get(f"/v1/teams/{team_a.id}/agent-provider")
    finally:
        _clear_principal()
    assert r.json()["available"] == ["anthropic"]


async def test_one_teams_choice_never_reaches_another(client, seeded_two_teams):
    """Case 7 — team_scope isolation, on the newest column."""
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]

    _install_principal(alice)
    try:
        p = await client.patch(
            f"/v1/teams/{seeded_two_teams['team_a'].id}/agent-provider",
            json={"provider": "xai"},
        )
        assert p.status_code == 200, p.text
    finally:
        _clear_principal()

    _install_principal(bob)
    try:
        g = await client.get(
            f"/v1/teams/{seeded_two_teams['team_b'].id}/agent-provider"
        )
    finally:
        _clear_principal()
    assert g.json()["provider"] == "anthropic"


async def test_an_unknown_team_is_a_404_before_any_authorisation_leak(
    client, seeded_two_teams
):
    from uuid import uuid4

    _install_principal(seeded_two_teams["alice"])
    try:
        r = await client.get(f"/v1/teams/{uuid4()}/agent-provider")
    finally:
        _clear_principal()
    assert r.status_code == 404, r.text

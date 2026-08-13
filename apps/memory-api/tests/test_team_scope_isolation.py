"""team_scope isolation — a member of team A can neither read nor write team B.

`test_team_isolation.py` covers ONE principal shape (a bridge JWT whose
team_scope claim disagrees with X-Team-Scope) on ONE surface (conversations +
messages). Everything else was untested, and the 2026-08-06 audit found nine
memory-api routes that read a team_scope out of client input — a body field, a
query param, a header, a path id — and used it with no membership check at all.
Registration is open on this deployment, so authentication stops nobody; these
are the routes where AUTHORIZATION was missing.

This file locks the fix for each of them, per principal shape:

  * `kind='user'` — a signed-in person naming another team in a request body.
  * `kind='user_api_token'` — the xbt_ path, INCLUDING a token whose row is
    already pinned to a team its owner does not belong to. That row is what a
    token minted before the fix looks like, so the check that makes it inert
    has to live in `deps.get_team_scope`, not only on the mint route.
  * `kind='bridge'` — a service JWT presented against a team it was not issued
    for, and a service JWT carrying no team_scope claim at all. A missing claim
    is a mismatch: an unnamed team cannot be the team you were authorised for.

Marked integration: real Postgres, real schema, real membership rows. The point
is to fail when the guard is removed, and a mocked membership lookup cannot do
that.
"""
from __future__ import annotations

import hashlib
import secrets
import types
import uuid

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── principal plumbing ───────────────────────────────────────────────────────
#
# Two ways in, used deliberately:
#   _install_* overrides get_current_principal, isolating ONE route's guard.
#   _mint_token_row + a real Authorization header exercises the whole chain,
#   which is the only way to prove the deps.py half.


def _app_and_dep():
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_user(user, *, kind: str = "user", api_token_team_scope=None) -> None:
    """Install a user-shaped principal (mirrors deps.py's returned dicts)."""
    app, get_current_principal = _app_and_dep()
    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=user.source_user_id,
        email=user.email,
        display_name=user.display_name,
        github_username=None,
        github_id=None,
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": user.source_user_id,
            "claims": {"sub": user.source_user_id},
            "api_token_team_scope": api_token_team_scope,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _install_bridge(team_scope: str | None, sub: str = "some-service") -> None:
    """Install a bridge principal. `team_scope=None` = a JWT with no claim."""
    app, get_current_principal = _app_and_dep()

    async def _override():
        return {
            "kind": "bridge",
            "sub": sub,
            "team_scope": team_scope,
            "claims": {"sub": sub, "iss": "test-bridge"},
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    """Drop ONLY the principal override — the client fixture's get_session stays."""
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture(autouse=True)
def _no_leaked_principal():
    yield
    _clear_principal()


async def _mint_token_row(session, *, user_id, team_scope: str) -> str:
    """Insert a real user_api_tokens row and return the plaintext token.

    Bypasses POST /v1/me/api-token on purpose: this is how a token minted
    BEFORE the mint route was fixed sits in a production database today.
    """
    raw = "xbt_" + secrets.token_urlsafe(32)
    await session.execute(
        sa.text(
            """
            INSERT INTO user_api_tokens (id, user_id, token_hash, team_scope, name)
            VALUES (gen_random_uuid(), :uid, :hash, :ts, 'isolation-test')
            """
        ),
        {
            "uid": str(user_id),
            "hash": hashlib.sha256(raw.encode()).hexdigest(),
            "ts": team_scope,
        },
    )
    await session.commit()
    return raw


# ═══════════════════════════════════════════════════════════════════════════
# 1. POST /v1/me/api-token — the worst finding: a self-service durable grant
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_user_cannot_mint_a_token_for_a_team_she_is_not_in(
    client, session, seeded_two_teams
):
    """Alice is in team-a only. A token pinned to team-b must be refused.

    This is the finding ranked worst in the audit: the row it would have
    written is read back by deps.get_team_scope on every later request, so one
    unchecked POST bought permanent, silent access to another team's brain.
    """
    alice = seeded_two_teams["alice"]
    team_b = seeded_two_teams["team_b"]

    _install_user(alice)
    r = await client.post(
        "/v1/me/api-token", json={"team_scope": team_b.slug, "name": "intrusion"}
    )
    assert r.status_code == 403, r.text

    # And nothing was written — a 403 that still leaves the row is not a fix.
    count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM user_api_tokens "
                "WHERE user_id = :uid AND team_scope = :ts"
            ),
            {"uid": str(alice.id), "ts": team_b.slug},
        )
    ).scalar_one()
    assert count == 0


async def test_a_member_can_still_mint_a_token_for_her_own_team(
    client, seeded_two_teams
):
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_user(alice)
    r = await client.post(
        "/v1/me/api-token", json={"team_scope": team_a.slug, "name": "legit"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["team_scope"] == team_a.slug


async def test_the_multi_team_sentinel_needs_no_membership(client, seeded_two_teams):
    """team_scope='' is the sign-in default and pins nothing — it must not 403.

    Every web and extension sign-in mints this shape. It grants no team by
    itself: deps.get_team_scope falls through to the team_members lookup on
    each request, which is exactly why no check belongs on the mint side.
    """
    bob = seeded_two_teams["bob"]

    _install_user(bob)
    r = await client.post("/v1/me/api-token", json={"name": "session"})
    assert r.status_code == 201, r.text
    assert r.json()["team_scope"] == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. deps.get_team_scope — the pin is re-validated, so old tokens go inert
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_token_pinned_to_a_foreign_team_is_inert(
    client, session, seeded_two_teams
):
    """A pre-fix token row pinned to team-a, owned by a non-member, gets 403.

    Fixing only the mint route would leave every token already issued working
    forever. The real header, the real token, the real deps.py chain.
    """
    bob = seeded_two_teams["bob"]  # member of team-b, never team-a
    team_a = seeded_two_teams["team_a"]

    raw = await _mint_token_row(session, user_id=bob.id, team_scope=team_a.slug)

    r = await client.get(
        "/v1/memory/search?q=anything",
        headers={"Authorization": f"Bearer {raw}", "X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 403, r.text


async def test_a_pinned_token_still_works_for_its_own_team(
    client, session, seeded_two_teams
):
    """The legitimate case must survive the fix: alice IS in team-a."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    raw = await _mint_token_row(session, user_id=alice.id, team_scope=team_a.slug)

    r = await client.get(
        "/v1/memory/search?q=anything",
        headers={"Authorization": f"Bearer {raw}", "X-Team-Scope": team_a.slug},
    )
    assert r.status_code != 403, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 3. POST /v1/agents/{id}/invoke — a memory_item written into any team
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_user_cannot_invoke_an_agent_into_another_team(
    client, seeded_two_teams
):
    """The cross-team guard used to run only for kind='bridge'.

    The handler ends in an INSERT into memory_items under body.team_scope, so a
    plain user naming team-a put agent output in team-a's brain.
    """
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]

    _install_user(bob)
    r = await client.post(
        f"/v1/agents/{uuid.uuid4()}/invoke",
        json={"team_scope": team_a.slug, "content": "summarise this"},
    )
    assert r.status_code == 403, r.text


async def test_a_member_passes_the_invoke_guard(client, seeded_two_teams):
    """A member reaches the agent lookup — 404 (no such agent), never 403.

    Pins down that the guard rejects on membership rather than on everything.
    """
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_user(alice)
    r = await client.post(
        f"/v1/agents/{uuid.uuid4()}/invoke",
        json={"team_scope": team_a.slug, "content": "summarise this"},
    )
    assert r.status_code == 404, r.text


async def test_a_pinned_token_cannot_invoke_outside_its_pin(client, seeded_two_teams):
    """xbt_ shape: the token's own scope beats the body's."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    _install_user(alice, kind="user_api_token", api_token_team_scope=team_a.slug)
    r = await client.post(
        f"/v1/agents/{uuid.uuid4()}/invoke",
        json={"team_scope": team_b.slug, "content": "summarise this"},
    )
    assert r.status_code == 403, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 4. POST /v1/me/granola-key — a recurring write primitive into another team
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_user_cannot_point_a_granola_key_at_another_team(
    client, session, seeded_two_teams
):
    """granola-sync polls this row and ingests notes into `team_scope`.

    An unchecked scope here is not one write, it is a write on a timer.
    """
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]

    _install_user(bob)
    r = await client.post(
        "/v1/me/granola-key",
        json={"api_key": "gr_secret_value", "team_scope": team_a.slug},
    )
    assert r.status_code == 403, r.text

    count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM granola_user_connections WHERE user_id = :uid"
            ),
            {"uid": str(bob.id)},
        )
    ).scalar_one()
    assert count == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. POST /v1/integrations/granola/ingest — the guard that never fired
# ═══════════════════════════════════════════════════════════════════════════


def _granola_body(team_scope: str) -> dict:
    return {
        "team_scope": team_scope,
        "note": {"id": "note-isolation-1", "title": "Board sync", "summary_text": "x"},
        "extracted": {},
    }


async def test_granola_ingest_refuses_a_bridge_jwt_with_no_team_claim(
    client, seeded_two_teams
):
    """The old guard read `if bridge_team and bridge_team != body.team_scope`.

    Every JWT reaching it carried no team_scope, so the `and` short-circuited
    and the mitigation had never once fired. A missing claim means the caller
    never proved which team it may write to.
    """
    team_a = seeded_two_teams["team_a"]

    _install_bridge(None, sub="granola-sync")
    r = await client.post(
        "/v1/integrations/granola/ingest", json=_granola_body(team_a.slug)
    )
    assert r.status_code == 403, r.text


async def test_granola_ingest_refuses_a_mismatched_team_claim(
    client, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    _install_bridge(team_b.slug, sub="granola-sync")
    r = await client.post(
        "/v1/integrations/granola/ingest", json=_granola_body(team_a.slug)
    )
    assert r.status_code == 403, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 6/7. /v1/internal/github/{sync,catalog}
# ═══════════════════════════════════════════════════════════════════════════


async def test_github_sync_refuses_a_non_member(client, seeded_two_teams):
    """Writing a whole repository into another team's brain."""
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]

    _install_user(bob)
    r = await client.post(
        "/v1/internal/github/sync",
        json={"repo": "someorg/somerepo", "team_scope": team_a.slug},
    )
    assert r.status_code == 403, r.text


async def test_github_sync_refuses_a_bridge_jwt_scoped_elsewhere(
    client, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    _install_bridge(team_b.slug, sub="mcp-github")
    r = await client.post(
        "/v1/internal/github/sync",
        json={"repo": "someorg/somerepo", "team_scope": team_a.slug},
    )
    assert r.status_code == 403, r.text


async def test_github_sync_refuses_a_claimless_bridge_jwt(client, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]

    _install_bridge(None, sub="mcp-github")
    r = await client.post(
        "/v1/internal/github/sync",
        json={"repo": "someorg/somerepo", "team_scope": team_a.slug},
    )
    assert r.status_code == 403, r.text


async def test_github_catalog_refuses_a_non_member_header(client, seeded_two_teams):
    """The header is 'what mcp-github sends' — it says nothing about who sent it."""
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]

    _install_user(bob)
    r = await client.get(
        "/v1/internal/github/catalog", headers={"X-Team-Scope": team_a.slug}
    )
    assert r.status_code == 403, r.text


async def test_github_catalog_refuses_a_non_member_query_param(
    client, seeded_two_teams
):
    """Same route, the other input. Closing one and not the other closes nothing."""
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]

    _install_user(bob)
    r = await client.get(f"/v1/internal/github/catalog?team_scope={team_a.slug}")
    assert r.status_code == 403, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 8. GET /v1/admin/projects — a team's deploy registry
# ═══════════════════════════════════════════════════════════════════════════


async def test_projects_listing_refuses_a_non_member(client, seeded_two_teams):
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]

    _install_user(bob)
    r = await client.get(f"/v1/admin/projects?team_scope={team_a.slug}")
    assert r.status_code == 403, r.text


async def test_projects_listing_serves_a_member(client, seeded_two_teams):
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_user(alice)
    r = await client.get(f"/v1/admin/projects?team_scope={team_a.slug}")
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 9. GET /v1/internal/resolve-team-scope — a directory of everyone's team
# ═══════════════════════════════════════════════════════════════════════════


async def test_resolve_team_scope_refuses_a_user_principal(client, seeded_two_teams):
    """`principal` was declared and never read: any account could walk subs.

    The answer includes another person's team slug AND their internal user
    UUID, which is the map an attacker needs before any of the routes above.
    """
    bob = seeded_two_teams["bob"]
    alice = seeded_two_teams["alice"]

    _install_user(bob)
    r = await client.get(
        "/v1/internal/resolve-team-scope", params={"sub": alice.source_user_id}
    )
    assert r.status_code == 403, r.text


async def test_resolve_team_scope_still_serves_a_bridge(client, seeded_two_teams):
    """librechat-bridge, mcp-brain and mcp-github all call this with a bridge JWT."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_bridge("whatever", sub="librechat-bridge")
    r = await client.get(
        "/v1/internal/resolve-team-scope", params={"sub": alice.source_user_id}
    )
    assert r.status_code == 200, r.text
    assert r.json()["team_scope"] == team_a.slug


# ═══════════════════════════════════════════════════════════════════════════
# 3(bundle). GET /v1/teams/{id}/agent-context-bundle
# ═══════════════════════════════════════════════════════════════════════════


async def test_bundle_refuses_a_bridge_jwt_issued_for_another_team(
    client, seeded_two_teams
):
    """Bridge-kind alone was the whole gate, so the path id was a free parameter.

    One service token — and they are all signed with the same shared secret —
    dumped every team's memory bundle and last twenty messages, one id at a time.
    """
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    _install_bridge(team_b.slug, sub="agent-runtime")
    r = await client.get(f"/v1/teams/{team_a.id}/agent-context-bundle")
    assert r.status_code == 403, r.text


async def test_bundle_refuses_a_claimless_bridge_jwt(client, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]

    _install_bridge(None, sub="agent-runtime")
    r = await client.get(f"/v1/teams/{team_a.id}/agent-context-bundle")
    assert r.status_code == 403, r.text


async def test_bundle_serves_the_team_its_claim_names(client, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]

    _install_bridge(team_a.slug, sub="agent-runtime")
    r = await client.get(f"/v1/teams/{team_a.id}/agent-context-bundle")
    assert r.status_code == 200, r.text
    assert r.json()["team"]["slug"] == team_a.slug


async def test_bundle_refuses_a_user_token_outright(client, seeded_two_teams):
    """Even team-a's own member: this endpoint is service-to-service, not a read API."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    _install_user(alice)
    r = await client.get(f"/v1/teams/{team_a.id}/agent-context-bundle")
    assert r.status_code == 403, r.text

"""Integration tests: the CRM/Tasks plan gate is gone (Phase 15 Plan 06).

`require_paid_tier` used to 403 every `/v1/crm/*` and `/v1/tasks/*` call
for teams on the schema-default `starter` plan — i.e. every self-hosted
install, since no code path anywhere upgrades a team. Locked decision Q6
("nothing in the product is paywalled — monetize the hosted service
only") means that gate should never have survived to Phase 15.

This file locks two contracts as a pair. Neither alone is sufficient:

1. `test_starter_team_can_use_crm_and_tasks` — proves the widening
   happened: a team on the schema DEFAULT plan is served, not 403'd.
2. `test_non_member_still_blocked_on_crm_and_tasks` — proves the
   widening did not go too far: team_scope membership isolation, the
   one invariant this product cannot break, still holds. A caller who
   is not a member of the target team is still rejected.
"""
from __future__ import annotations

import types

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal-shape helper (mirrors tests/test_brain_events_list.py) ──


def _get_app_and_dep():
    """Lazy import so test collection stays light when integration deps
    (Docker, qdrant_client) aren't present."""
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_principal_override(user) -> None:
    """Bypass `get_current_principal` with a fake authenticated 'user'
    principal — same pattern as `tests/test_brain_events_list.py`. Avoids
    signing real JWTs / hitting the GitHub / Google verification paths.
    """
    app, get_current_principal = _get_app_and_dep()

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
            "kind": "user",
            "user": fake_user,
            "sub": fake_user.source_user_id,
            # None == Google-style principal (D7 — always allowed by the
            # org-membership check); this test is about team_scope
            # membership, not GitHub org membership.
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


# ── 1. A starter-plan team is served (the widening) ────────────────────


async def test_starter_team_can_use_crm_and_tasks(client, session, seeded_two_teams):
    """The schema DEFAULT plan ('starter') must not paywall CRM or Tasks.

    Reads the team's `plan` column directly instead of setting it — the
    whole point of this test is to prove the DEFAULT is served, not a
    value the test chose itself.
    """
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]  # admin of team_a per seeded_two_teams

    row = (
        await session.execute(
            sa.text("SELECT plan FROM teams WHERE slug = :slug"),
            {"slug": team_a.slug},
        )
    ).fetchone()
    assert row is not None, "team_a row vanished before the plan check"
    assert row.plan == "starter", (
        f"expected the schema DEFAULT 'starter', got {row.plan!r} — "
        "this test must prove the default, not a value it set"
    )

    _install_principal_override(alice)

    r_crm = await client.get(
        "/v1/crm/contacts?limit=5",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r_crm.status_code != 403, (
        f"starter team got 403 from /v1/crm/contacts — paywall gate still active: {r_crm.text}"
    )
    assert r_crm.status_code == 200, r_crm.text
    assert "plan" not in r_crm.text.lower(), (
        f"response body mentions 'plan' — looks like a paywall rejection leaked through: {r_crm.text}"
    )

    r_tasks = await client.get(
        "/v1/tasks?limit=5",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r_tasks.status_code != 403, (
        f"starter team got 403 from /v1/tasks — paywall gate still active: {r_tasks.text}"
    )
    assert r_tasks.status_code == 200, r_tasks.text
    assert "plan" not in r_tasks.text.lower(), (
        f"response body mentions 'plan' — looks like a paywall rejection leaked through: {r_tasks.text}"
    )


# ── 2. A non-member is still blocked (team-scope isolation intact) ─────


async def test_non_member_still_blocked_on_crm_and_tasks(client, seeded_two_teams):
    """Regression guard for the widening.

    Bob is admin of team-b, NOT a member of team-a. Removing the plan
    check must not also remove the membership check — `get_team_scope`
    must still reject him with 403, exactly as it does today. This is
    the test that fails loudly if someone "simplifies" the dependency
    away entirely and accidentally opens cross-team reads — team_scope
    isolation is the one invariant this product cannot break.
    """
    team_a = seeded_two_teams["team_a"]
    bob = seeded_two_teams["bob"]

    _install_principal_override(bob)

    r_crm = await client.get(
        "/v1/crm/contacts?limit=5",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r_crm.status_code == 403, (
        f"non-member of team_a was NOT rejected by /v1/crm/contacts — "
        f"team_scope isolation broken: {r_crm.status_code} {r_crm.text}"
    )

    r_tasks = await client.get(
        "/v1/tasks?limit=5",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r_tasks.status_code == 403, (
        f"non-member of team_a was NOT rejected by /v1/tasks — "
        f"team_scope isolation broken: {r_tasks.status_code} {r_tasks.text}"
    )

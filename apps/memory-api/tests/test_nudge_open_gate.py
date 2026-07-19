"""Phase 22 — THE definitive push-a-link nudge gate (NUDGE-01, D-22-01/03/04).

The "gate lesson" made executable: a "nudge works" claim proves nothing until a
real POST traverses the REAL `/v1/teams/{id}/nudge-open` path against a REAL
Postgres and the publish is CAPTURED (not mocked away). The ONLY thing stubbed is
the final fire-and-forget network call (`centrifugo_client.publish`), replaced by a
RECORDER so we can assert exactly WHICH channel/payload it was handed. Membership
resolution, target lookup, URL validation and the per-sender rate limit all run for
real — that is precisely what this gate exists to prove.

Four failure surfaces, each varying exactly ONE condition (the endpoint's ordering
is: sender-membership 403 -> target-membership 403 -> URL 422 -> rate-limit 429 ->
publish), so a rejection provably schedules NO publish:

  1. HAPPY (SC#1): same-team sender->target, https url -> 202 AND exactly one publish
     to `user:<target_source_user_id>` carrying {type:open_url, url, from, team_id,
     team_slug}, sender identity intact, url un-expanded/literal.
  2. CROSS-TEAM / NON-MEMBER (SC#1, T-22-02): target is a different team's member (or
     no member at all) -> 403 AND recorder length unchanged.
  3. BAD SCHEME (SC#2, T-22-03): javascript:/file: url -> 422 AND no publish.
  4. RATE LIMIT (T-22-05): per-sender bucket exhausted -> 429 AND no publish.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this file.
A clean SKIP is legitimate ONLY when Docker is genuinely absent (conftest gate);
under Docker this file MUST run green.
"""
from __future__ import annotations

import asyncio
import types
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (copied from test_agent_aliases_gate.py) ────────


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


# ── The gate ──────────────────────────────────────────────────────────────────


async def test_nudge_open_gate(client, seeded_two_teams, session, monkeypatch):
    """Real Postgres, captured publish: same-team target publishes; cross-team/bad
    url/rate-limited requests provably publish nothing."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services import rate_limit

    alice = seeded_two_teams["alice"]   # admin+member of team-a
    bob = seeded_two_teams["bob"]       # admin+member of team-b (NOT team-a)
    team_a = seeded_two_teams["team_a"]

    # 1. Seed carol as a SECOND member of team-a (the legitimate nudge target).
    carol = await users_repo.get_or_create_user(
        session,
        source_user_id="carol-sub",
        email="carol@test.local",
        display_name="Carol",
    )
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=carol.id, role="member"
    )
    await session.commit()

    # 2. RECORDER — capture (channel, data). team_chat calls the module attribute
    #    `centrifugo_client.publish`, so patching the attribute intercepts it. We do
    #    NOT mock membership/validation — only the terminal network publish.
    published: list[tuple[str, dict]] = []

    async def _recorder(channel, data):
        published.append((channel, data))
        return True

    monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)

    # 3. POST as `user`, then flush the loop so the fire-and-forget create_task
    #    (the recorder) runs before we assert. Returns the httpx response.
    async def _post_as(user, body: dict):
        _install_principal(user)
        try:
            r = await client.post(f"/v1/teams/{team_a.id}/nudge-open", json=body)
        finally:
            _clear_principal()
        for _ in range(5):
            await asyncio.sleep(0)
        return r

    # 4. HAPPY (SC#1): alice -> carol, https -> 202 AND exactly one publish to carol's
    #    user channel carrying the full open_url event with sender identity + literal url.
    r = await _post_as(
        alice, {"target_user_id": str(carol.id), "url": "https://example.com/x"}
    )
    assert r.status_code == 202, r.text
    assert len(published) == 1, f"expected exactly one publish, got {published!r}"
    channel, data = published[0]
    assert channel == f"user:{carol.source_user_id}", channel
    assert data["type"] == "open_url"
    assert data["url"] == "https://example.com/x"       # literal, un-expanded (D-22-03)
    assert data["from"]["sub"] == "alice-sub"           # sender identity (T-22-06)
    assert data["from"]["display_name"] == "Alice"
    assert data["team_id"] == str(team_a.id)
    assert data["team_slug"] == team_a.slug

    # 5. CROSS-TEAM / NON-MEMBER (SC#1, T-22-02): alice -> bob (team-b only) -> 403, no publish.
    r = await _post_as(
        alice, {"target_user_id": str(bob.id), "url": "https://example.com/x"}
    )
    assert r.status_code == 403, r.text
    assert len(published) == 1, "cross-team target must NOT publish"

    # 5b. A totally unknown target user id -> 403, no publish (same single check).
    r = await _post_as(
        alice, {"target_user_id": str(uuid4()), "url": "https://example.com/x"}
    )
    assert r.status_code == 403, r.text
    assert len(published) == 1, "unknown target must NOT publish"

    # 6. BAD SCHEME (SC#2, T-22-03): javascript:/file: -> 422, no publish.
    r = await _post_as(
        alice, {"target_user_id": str(carol.id), "url": "javascript:alert(1)"}
    )
    assert r.status_code == 422, r.text
    assert len(published) == 1, "javascript: url must NOT publish"

    r = await _post_as(
        alice, {"target_user_id": str(carol.id), "url": "file:///etc/passwd"}
    )
    assert r.status_code == 422, r.text
    assert len(published) == 1, "file: url must NOT publish"

    # 7. RATE LIMIT (T-22-05): clean bucket + 1/minute -> first ok, second 429, no publish.
    rate_limit._storage.reset()
    monkeypatch.setattr("app.config.settings.NUDGE_RATE_LIMIT", "1/minute")

    r = await _post_as(
        alice, {"target_user_id": str(carol.id), "url": "https://example.com/y"}
    )
    assert r.status_code == 202, r.text
    assert len(published) == 2, "first nudge under the fresh bucket must publish"

    r = await _post_as(
        alice, {"target_user_id": str(carol.id), "url": "https://example.com/z"}
    )
    assert r.status_code == 429, r.text
    assert len(published) == 2, "the rate-limited (429) nudge must add NO publish"

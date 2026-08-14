"""The brain tag — a teammate must not receive it, on the wire or on read.

This is the gate the feature does not ship without. The unit tests in
`test_private_lane_predicate.py` prove the predicate is shaped right; this
proves the product behaves right, with two real members of one real team.

WHY THE WIRE ASSERTION COMES FIRST. Centrifugo's `team` namespace keeps 100
frames for seven days with `force_recovery` (infrastructure/centrifugo/
config.json). A private note published on `team:` is therefore not a momentary
slip that the next deploy fixes — it is replayed to every member on their next
reconnect, for a week, and nothing takes it back. Display-side filtering cannot
save a wrong channel, which is why the channel is the access control.

WHAT IS DELIBERATELY NOT ISOLATED. The note lands in the team's brain at full
length and every member can find it — the owner's decision of 2026-08-05,
reaffirmed on 2026-08-14 when the Brain Monitor filter was removed. The UI copy
says so in as many words. That is asserted here too, as a POSITIVE test: if
someone later "fixes" it into real privacy, this file goes red and tells them it
was a decision, not an oversight.
"""
from __future__ import annotations

import asyncio
import types
import uuid

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── plumbing ─────────────────────────────────────────────────────────────────


def _app_and_dep():
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_user(user) -> None:
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
            "kind": "user",
            "user": fake_user,
            "sub": user.source_user_id,
            "claims": {"sub": user.source_user_id},
            "api_token_team_scope": None,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


@pytest.fixture(autouse=True)
def _no_leaked_principal():
    yield
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture
async def one_team(session, seeded_two_teams):
    """Alice and Bob in ONE team — Bob an admin, so admin paths are exercised."""
    from app.repos import teams as teams_repo

    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    team = seeded_two_teams["team_a"]
    await teams_repo.add_member(
        session, team_id=team.id, user_id=bob.id, role="admin"
    )
    await session.commit()
    return {"alice": alice, "bob": bob, "team": team}


@pytest.fixture
def frames(monkeypatch):
    """Record every (channel, data) the app publishes, and send none."""
    recorded: list[tuple[str, dict]] = []

    async def _fake_publish(channel, data):
        recorded.append((channel, data))
        return True

    from app.services import centrifugo_client

    monkeypatch.setattr(centrifugo_client, "publish", _fake_publish)
    # The routes import the module, not the function, so patching the attribute
    # on the module is what actually intercepts them — asserted by the control
    # test at the bottom, which would otherwise record nothing and pass hollow.
    return recorded


RARE = "zzq-marker-" + uuid.uuid4().hex[:8]


async def _drain():
    """Run the fire-and-forget work the route scheduled, to completion.

    The publish and the brain ingest are background tasks, so on the line after
    the POST returns they have not necessarily run. Awaiting the registry is
    deterministic where a sleep is a guess — and a test that sleeps just long
    enough today is a test that goes flaky on a loaded machine.
    """
    from app.services import background

    for _ in range(10):
        pending = list(background._TASKS)
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def _post(client, team, *, content, private):
    r = await client.post(
        f"/v1/teams/{team.id}/messages",
        json={"content": content, "private": private},
        headers={"X-Team-Scope": team.slug},
    )
    assert r.status_code == 201, r.text
    await _drain()
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE WIRE — the assertion that matters most
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_tagged_message_never_touches_the_team_channel(
    client, one_team, frames
):
    """`team:` keeps 100 frames for 7 days with recovery. One publish is forever."""
    alice, team = one_team["alice"], one_team["team"]
    _install_user(alice)

    await _post(client, team, content=f"a note {RARE}", private=True)

    channels = [c for c, _ in frames]
    assert channels, "nothing was published — the fake never intercepted"
    assert f"team:{team.id}" not in channels, (
        f"a tagged message reached the team channel: {channels}"
    )
    assert channels == [f"user:{alice.source_user_id}"], channels


async def test_the_frame_carries_its_team_because_that_channel_is_cross_team(
    client, one_team, frames
):
    """One socket serves every team a person belongs to.

    Without team_id the client cannot tell which thread a private frame belongs
    in, and a note written in team A would paint into team B's open thread.
    """
    alice, team = one_team["alice"], one_team["team"]
    _install_user(alice)

    await _post(client, team, content=f"a note {RARE}", private=True)

    _, data = frames[0]
    assert data.get("team_id") == str(team.id), data


# ═══════════════════════════════════════════════════════════════════════════
# 2. EVERY READ PATH — passing one is not passing another
# ═══════════════════════════════════════════════════════════════════════════


async def test_history_hides_it_from_the_teammate_and_keeps_it_for_the_author(
    client, one_team, frames
):
    alice, bob, team = one_team["alice"], one_team["bob"], one_team["team"]
    _install_user(alice)
    sent = await _post(client, team, content=f"a note {RARE}", private=True)

    _install_user(bob)
    r = await client.get(
        f"/v1/teams/{team.id}/messages", headers={"X-Team-Scope": team.slug}
    )
    assert r.status_code == 200, r.text
    ids = {m["id"] for m in r.json()["messages"]}
    assert sent["id"] not in ids, "Bob can read Alice's tagged message"

    _install_user(alice)
    r = await client.get(
        f"/v1/teams/{team.id}/messages", headers={"X-Team-Scope": team.slug}
    )
    mine = {m["id"]: m for m in r.json()["messages"]}
    assert sent["id"] in mine, "Alice cannot read her own tagged message"
    assert mine[sent["id"]]["private"] is True, "the flag must reach the client"


async def test_the_unread_count_does_not_move_for_the_teammate(
    client, one_team, frames
):
    """A count is a disclosure too — a badge that ticks announces the note."""
    alice, bob, team = one_team["alice"], one_team["bob"], one_team["team"]

    _install_user(bob)
    before = (
        await client.get(
            f"/v1/teams/{team.id}/unread-summary",
            headers={"X-Team-Scope": team.slug},
        )
    ).json()

    _install_user(alice)
    await _post(client, team, content=f"a note {RARE}", private=True)

    _install_user(bob)
    after = (
        await client.get(
            f"/v1/teams/{team.id}/unread-summary",
            headers={"X-Team-Scope": team.slug},
        )
    ).json()
    assert after == before, (before, after)


async def test_a_teammate_admin_cannot_star_or_delete_it(client, one_team, frames):
    """404, not 403 — a distinct code would confirm the id exists."""
    alice, bob, team = one_team["alice"], one_team["bob"], one_team["team"]
    _install_user(alice)
    sent = await _post(client, team, content=f"a note {RARE}", private=True)

    _install_user(bob)  # Bob is an ADMIN of this team
    frames.clear()
    r = await client.put(
        f"/v1/teams/{team.id}/messages/{sent['id']}/star",
        json={"starred": True},
        headers={"X-Team-Scope": team.slug},
    )
    assert r.status_code == 404, r.text
    r = await client.delete(
        f"/v1/teams/{team.id}/messages/{sent['id']}",
        headers={"X-Team-Scope": team.slug},
    )
    assert r.status_code == 404, r.text
    assert frames == [], f"a refused action still published: {frames}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE LOCKED DECISION — asserted so a later "fix" breaks loudly
# ═══════════════════════════════════════════════════════════════════════════


async def test_the_tag_does_not_stop_the_note_reaching_the_teams_brain(
    client, one_team, frames, monkeypatch
):
    """NOT a leak. The tag governs the chat surface only.

    Asserted at the point where it is decided — whether the route still hands
    the note to brain ingest — rather than by reading the row back. The ingest
    runs on its own connection and commits outside this test's transaction, so
    a SELECT here proves nothing either way; what must never change is that the
    route makes the call, with the full text, for a tagged message exactly as
    for an ordinary one.

    The owner decided this on 2026-08-05 and reaffirmed it on 2026-08-14 when
    the Brain Monitor filter came out, and the UI copy tells the author in as
    many words that teammates can still find it. If someone later gates this on
    `private`, the product starts lying to the person who wrote the note — so
    the decision is pinned here rather than left to be re-argued.
    """
    alice, team = one_team["alice"], one_team["team"]
    seen: list[dict] = []

    from app.services import brain_ingest

    async def _spy(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(brain_ingest, "ingest_team_message", _spy)

    _install_user(alice)
    await _post(client, team, content=f"a note {RARE}", private=True)

    assert len(seen) == 1, f"the tagged note was not sent to the brain: {seen}"
    assert RARE in seen[0]["content"], "the brain got something other than the note"
    assert seen[0]["team_scope"] == team.slug, "it landed outside the team's brain"


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE CONTROL — without it, every assertion above passes on a build that
#    silently drops the message
# ═══════════════════════════════════════════════════════════════════════════


async def test_the_same_message_untagged_behaves_exactly_as_before(
    client, one_team, frames
):
    alice, bob, team = one_team["alice"], one_team["bob"], one_team["team"]
    _install_user(alice)
    sent = await _post(client, team, content=f"an ordinary note {RARE}", private=False)

    channels = [c for c, _ in frames]
    assert channels == [f"team:{team.id}"], channels

    _install_user(bob)
    r = await client.get(
        f"/v1/teams/{team.id}/messages", headers={"X-Team-Scope": team.slug}
    )
    ids = {m["id"] for m in r.json()["messages"]}
    assert sent["id"] in ids, "the control message is invisible — the test proves nothing"


async def test_private_defaults_to_false_when_the_client_omits_it(
    client, one_team, frames
):
    """An older client that never heard of the tag must keep posting to the team."""
    alice, team = one_team["alice"], one_team["team"]
    _install_user(alice)
    r = await client.post(
        f"/v1/teams/{team.id}/messages",
        json={"content": f"legacy client {RARE}"},
        headers={"X-Team-Scope": team.slug},
    )
    assert r.status_code == 201, r.text
    await _drain()
    assert r.json()["private"] is False
    assert [c for c, _ in frames] == [f"team:{team.id}"]

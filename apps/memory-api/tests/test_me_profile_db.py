"""The profile against a REAL Postgres: two accounts, real rows, real SQL.

tests/test_me_profile_isolation.py proves the route surface cannot express
"someone else's profile" and proves the behaviour against a recording stub. This
file proves the same property where it finally matters — with two rows in a real
table, where a wrong WHERE clause would actually cross between them — and adds
the end-to-end nobody can fake: a person renames themselves and the name changes
in CHAT, because both surfaces resolve through the one ladder.

SKIP=FAIL discipline: a clean SKIP here is legitimate only when Docker is absent.
"""
from __future__ import annotations

import types

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _install_principal(user, *, kind: str = "user") -> None:
    from app.deps import get_current_principal
    from app.main import app

    # A SimpleNamespace on purpose: the routes must re-read the row from the
    # database rather than trust the principal's snapshot of it. Nothing here
    # carries preferred_name, bio or the avatar columns, so a route that read
    # them off the principal would return None for every one of them.
    identity = types.SimpleNamespace(
        id=user.id,
        source_user_id=user.source_user_id,
        email=user.email,
        display_name=user.display_name,
    )

    async def _override():
        return {
            "kind": kind,
            "user": identity,
            "sub": identity.source_user_id,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


async def _row(session, user_id):
    return (
        await session.execute(
            sa.text(
                "SELECT preferred_name, bio, avatar_media_id, avatar_media_team_scope, "
                "display_name, email FROM users WHERE id = :id"
            ),
            {"id": str(user_id)},
        )
    ).mappings().fetchone()


async def test_one_person_cannot_read_or_write_another_persons_profile(
    client, seeded_two_teams, session
):
    """The gate, against real rows. Alice edits; Bob's row is untouched, and vice versa."""
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]

    _install_principal(alice)
    try:
        r = await client.patch(
            "/v1/me/profile",
            json={"preferred_name": "Alice Renamed", "bio": "Alice's private bio"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "alice@test.local"

        # A query parameter naming Bob is not a way into Bob's row.
        r = await client.get(f"/v1/me/profile?user_id={bob.id}")
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == str(alice.id)
        assert r.json()["email"] == "alice@test.local"
        assert "bob" not in r.text.lower()
    finally:
        _clear_principal()

    bob_row = await _row(session, bob.id)
    assert bob_row["preferred_name"] is None, "Alice's write reached Bob's row"
    assert bob_row["bio"] is None

    # Bob now reads his own, and sees nothing of Alice's.
    _install_principal(bob)
    try:
        r = await client.get("/v1/me/profile")
    finally:
        _clear_principal()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(bob.id)
    assert body["email"] == "bob@test.local"
    assert body["preferred_name"] is None
    assert body["bio"] is None
    assert "Alice's private bio" not in r.text

    alice_row = await _row(session, alice.id)
    assert alice_row["preferred_name"] == "Alice Renamed", "Alice's own write must persist"
    assert alice_row["bio"] == "Alice's private bio"


async def test_a_rename_reaches_chat_because_both_read_the_same_ladder(
    client, seeded_two_teams, session
):
    """The end-to-end: set a preferred name, and the chat bubble says it.

    This is what a profile is FOR. A rename that persists but leaves messages
    reading the provider's name would be a profile screen talking to itself.
    """
    from app.repos import team_messages as tm_repo

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    await tm_repo.insert_user_message(
        session, team_id=team_a.id, author_user_id=alice.id, content="before the rename"
    )
    await session.commit()

    _install_principal(alice)
    try:
        r = await client.patch("/v1/me/profile", json={"preferred_name": "Nico"})
        assert r.status_code == 200, r.text
        assert r.json()["label"] == "Nico"

        history = await client.get(f"/v1/teams/{team_a.id}/messages")
        assert history.status_code == 200, history.text
        msg = next(
            m for m in history.json()["messages"] if m["content"] == "before the rename"
        )
        assert msg["author_label"] == "Nico", (
            "a message written before the rename must render the CURRENT name — "
            "the label is resolved at read time from the users row, not copied "
            "onto the message"
        )

        # And the undo: clearing hands the label back to the provider's name,
        # everywhere at once.
        assert (
            await client.patch("/v1/me/profile", json={"preferred_name": ""})
        ).json()["label"] == "Alice"
        history = await client.get(f"/v1/teams/{team_a.id}/messages")
        msg = next(
            m for m in history.json()["messages"] if m["content"] == "before the rename"
        )
        assert msg["author_label"] == "Alice"
    finally:
        _clear_principal()


async def test_clearing_writes_sql_null_not_an_empty_string(
    client, seeded_two_teams, session
):
    """`""` must reach the column as NULL — an empty string is a value, not an absence."""
    alice = seeded_two_teams["alice"]

    _install_principal(alice)
    try:
        await client.patch("/v1/me/profile", json={"preferred_name": "Temp", "bio": "Temp"})
        await client.patch("/v1/me/profile", json={"preferred_name": "", "bio": ""})
    finally:
        _clear_principal()

    is_null = (
        await session.execute(
            sa.text(
                "SELECT preferred_name IS NULL AS pn_null, bio IS NULL AS bio_null "
                "FROM users WHERE id = :id"
            ),
            {"id": str(alice.id)},
        )
    ).mappings().fetchone()
    assert is_null["pn_null"] and is_null["bio_null"]


async def test_the_longest_accepted_values_fit_the_columns(
    client, seeded_two_teams, session
):
    """A value the API accepts but the column rejects is a 500 for the first
    person who writes a long one. Written to real Postgres, read back verbatim."""
    from app.services.user_profile import MAX_BIO_LENGTH, MAX_PREFERRED_NAME_LENGTH

    alice = seeded_two_teams["alice"]
    longest_name = "n" * MAX_PREFERRED_NAME_LENGTH
    longest_bio = "b" * MAX_BIO_LENGTH

    _install_principal(alice)
    try:
        r = await client.patch(
            "/v1/me/profile", json={"preferred_name": longest_name, "bio": longest_bio}
        )
        assert r.status_code == 200, r.text
    finally:
        _clear_principal()

    row = await _row(session, alice.id)
    assert row["preferred_name"] == longest_name
    assert row["bio"] == longest_bio
    assert r.json()["label"] == longest_name, "no ellipsis on a name we accepted"


async def test_a_multibyte_name_survives_the_round_trip(
    client, seeded_two_teams, session
):
    alice = seeded_two_teams["alice"]
    name = "Nicolás 日本語 \U0001f600"

    _install_principal(alice)
    try:
        r = await client.patch("/v1/me/profile", json={"preferred_name": name})
        assert r.status_code == 200, r.text
    finally:
        _clear_principal()

    assert (await _row(session, alice.id))["preferred_name"] == name
    assert r.json()["label"] == name


async def test_the_provider_display_name_is_never_overwritten_by_an_edit(
    client, seeded_two_teams, session
):
    """The column that makes a rename undoable must stay the provider's.

    If a PATCH ever wrote display_name, the next Google sign-in would either
    clobber the person's chosen name or freeze a stale copy of the provider's,
    and clearing preferred_name would restore nothing.
    """
    alice = seeded_two_teams["alice"]

    _install_principal(alice)
    try:
        await client.patch("/v1/me/profile", json={"preferred_name": "Chosen"})
    finally:
        _clear_principal()

    assert (await _row(session, alice.id))["display_name"] == "Alice"

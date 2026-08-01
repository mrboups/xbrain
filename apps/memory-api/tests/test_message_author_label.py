"""A chat message carries its own name — the "Teammate" defect, made a gate.

The defect: `_serialize_message` emitted only `author_user_id`, so every client
resolved names out of band from GET /v1/teams/{id}/members into a local cache and
rendered `cache[author_user_id] || "Teammate"`. That fails in two ways no client
can fix — the cache is filled after an await (history that renders first renders
nameless), and an author who has LEFT the team is not in /members at all, so their
past messages read "Teammate" forever.

These tests run the REAL routes against a REAL Postgres. Nothing about label
resolution is mocked; the only stub is the terminal fire-and-forget network call
(`centrifugo_client.publish`), replaced by a recorder so the live frame can be
inspected — that frame is half the contract (a message that arrives live must not
be labelled differently from the same message after a reload).

Covered:
  1. history carries `author_label`, resolved through the one helper;
  2. the live Centrifugo frame carries the IDENTICAL label;
  3. an author who has LEFT the team keeps their label — the case the cache can
     never handle, and the proof this fix is real;
  4. a blocked member's history keeps its label (blocking hides the roster row,
     it does not rewrite history);
  5. an account with a provider display name NEVER renders the last resort;
  6. the payload gained a label and NOT the author's email or bio;
  7. agent frames carry a null label (the client labels those from agent_name).

SKIP=FAIL discipline: a clean SKIP is legitimate only when Docker is absent.
"""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _install_principal(user, *, kind: str = "user") -> None:
    from app.deps import get_current_principal
    from app.main import app

    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=getattr(user, "source_user_id", None),
        email=getattr(user, "email", None),
        display_name=getattr(user, "display_name", None),
        preferred_name=getattr(user, "preferred_name", None),
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


async def test_message_author_label_gate(client, seeded_two_teams, session, monkeypatch):
    """History and the live frame both name the author, including after they leave."""
    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services.user_label import LAST_RESORT_LABEL

    alice = seeded_two_teams["alice"]          # admin+member of team-a
    team_a = seeded_two_teams["team_a"]

    # The account from the bug report: a provider display name AND an email that
    # would otherwise be the label. It must read "Excalibur Team".
    excalibur = await users_repo.get_or_create_user(
        session,
        source_user_id="excalibur-sub",
        email="team@excalibur.game",
        display_name="Excalibur Team",
    )
    # The leaver: authors a message, then leaves the team. A name cache built from
    # GET /members loses them permanently.
    leaver = await users_repo.get_or_create_user(
        session, source_user_id="leaver-sub", email="leaver@test.local", display_name="Leaver Lou"
    )
    # The blocked member: hidden from the roster, history intact.
    blocked = await users_repo.get_or_create_user(
        session, source_user_id="blocked-sub", email="blocked@test.local", display_name="Blocked Bob"
    )
    for u in (excalibur, leaver, blocked):
        await teams_repo.add_member(session, team_id=team_a.id, user_id=u.id, role="member")
    await session.commit()

    # Seed one message from each of the three, plus an agent frame.
    for author, text in (
        (excalibur, "from the provider-named account"),
        (leaver, "posted before leaving"),
        (blocked, "posted before being blocked"),
    ):
        await tm_repo.insert_user_message(
            session, team_id=team_a.id, author_user_id=author.id, content=text
        )
    await tm_repo.insert_agent_message(
        session, team_id=team_a.id, agent_name="agent", content="agent frame", routed_via="team_api"
    )
    await session.commit()

    # The leaver leaves; the blocked member is blocked. Neither appears in the
    # roster any more — which is the whole point.
    await teams_repo.remove_member(session, team_id=team_a.id, user_id=leaver.id)
    await teams_repo.block_member(
        session, team_id=team_a.id, user_id=blocked.id, blocked_by=alice.id
    )
    await session.commit()

    # ── 1. History carries a label for every message ────────────────────────────
    _install_principal(alice)
    try:
        r = await client.get(f"/v1/teams/{team_a.id}/messages")
    finally:
        _clear_principal()
    assert r.status_code == 200, r.text
    by_content = {m["content"]: m for m in r.json()["messages"]}

    # 5. The reported account: provider name wins, last resort never appears.
    provider_named = by_content["from the provider-named account"]
    assert provider_named["author_label"] == "Excalibur Team"
    assert provider_named["author_label"] != LAST_RESORT_LABEL

    # 3. THE case a client-side cache can never handle.
    assert by_content["posted before leaving"]["author_label"] == "Leaver Lou", (
        "an author who left the team must keep their label — this is exactly the "
        "message that read 'Teammate' forever under the /members name cache"
    )

    # 4. Blocking hides the roster row; it does not rewrite history.
    assert by_content["posted before being blocked"]["author_label"] == "Blocked Bob"

    # 7. Agent frames are labelled client-side from agent_name.
    assert by_content["agent frame"]["author_label"] is None

    # 6. The payload gained a LABEL and nothing else about the author.
    leaked = {"email", "bio", "author_email", "preferred_name", "source_user_id"}
    assert not (leaked & set(provider_named)), (
        f"message payload must expose only the label: found {leaked & set(provider_named)}"
    )

    # ── 2. The live frame agrees with the history ───────────────────────────────
    published: list[tuple[str, dict]] = []

    async def _recorder(channel, data):
        published.append((channel, data))
        return True

    monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)

    _install_principal(excalibur)
    try:
        r = await client.post(
            f"/v1/teams/{team_a.id}/messages", json={"content": "live frame check"}
        )
    finally:
        _clear_principal()
    for _ in range(5):
        await asyncio.sleep(0)

    assert r.status_code == 201, r.text
    assert r.json()["author_label"] == "Excalibur Team"

    frames = [d for _c, d in published if d.get("type") == "message"]
    assert frames, "the send must publish a realtime frame"
    assert frames[0]["message"]["author_label"] == "Excalibur Team", (
        "a message that arrives live must carry the same label as the same "
        "message after a reload"
    )


async def test_history_label_matches_the_helper_exactly(client, seeded_two_teams, session):
    """The route must not re-derive a name — it must agree with the one helper.

    Both former call sites (the mention push at team_chat.py and the nudge push)
    now call resolve_user_label, and so does the serializer; this asserts the
    serializer's output IS the helper's output for a user whose tiers differ at
    every level, so a future edit that inlines a fallback chain again fails here.
    """
    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services.user_label import resolve_user_label

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    # No provider display_name at all → the ladder must reach the email local part.
    nameless = await users_repo.get_or_create_user(
        session, source_user_id="nameless-sub", email="nicoboups@gmail.com", display_name=None
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=nameless.id, role="member")
    await tm_repo.insert_user_message(
        session, team_id=team_a.id, author_user_id=nameless.id, content="no provider name"
    )
    await session.commit()

    _install_principal(alice)
    try:
        r = await client.get(f"/v1/teams/{team_a.id}/messages")
    finally:
        _clear_principal()

    msg = next(m for m in r.json()["messages"] if m["content"] == "no provider name")
    assert msg["author_label"] == resolve_user_label(nameless) == "nicoboups"


async def test_push_labels_use_the_same_helper(seeded_two_teams, session):
    """The two notification call sites resolve through the helper, not their own chain.

    Unit-level on the label itself: the previous mention-push chain was
    `display_name or email or "A teammate"`, which would render the RAW EMAIL
    `team@excalibur.game` on a lock screen for an account with no provider name.
    The ladder never puts a full email on a notification.
    """
    from app.services.user_label import resolve_user_label

    no_provider_name = types.SimpleNamespace(
        preferred_name=None,
        display_name=None,
        email="team@excalibur.game",
        source_user_id="excalibur-sub",
    )
    label = resolve_user_label(no_provider_name)
    assert label == "team"
    assert "@" not in label, "a notification label must never be a full email address"


async def test_a_nameless_user_message_is_unrepresentable(seeded_two_teams, session):
    """The last resort cannot surface in chat history, because the schema forbids it.

    `users.id` is a FK from `team_messages.author_user_id` (so the author row
    always exists and always has an email and a sub), and the CHECK constraint
    `ck_team_messages_author_required` additionally forbids a `kind='user'` row
    with a NULL author. Both halves together make "a user message with no name"
    an unrepresentable state — which is the strongest possible version of "'A
    teammate' never reaches the screen".

    NOTE for whoever implements account deletion: the FK is ON DELETE SET NULL,
    and this constraint means that SET NULL will RAISE rather than orphan the
    message. Deleting an account that has ever posted needs a decision (reassign
    to a tombstone user, or soft-delete the account) — it is not a no-op today.
    """
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    team_a = seeded_two_teams["team_a"]

    ghost = await users_repo.get_or_create_user(
        session, source_user_id="ghost-sub", email="ghost@test.local", display_name="Ghost"
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=ghost.id, role="member")
    msg = await tm_repo.insert_user_message(
        session, team_id=team_a.id, author_user_id=ghost.id, content="authored by a real account"
    )
    await session.commit()

    # Savepoint: the violation must not tear down the fixture's outer transaction.
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                sa.text("UPDATE team_messages SET author_user_id = NULL WHERE id = :id"),
                {"id": str(msg.id)},
            )


async def test_label_resolution_is_one_query_not_one_per_message(
    client, seeded_two_teams, session
):
    """N messages from N authors must cost ONE author query, not N.

    Counts SELECTs against `users` during a history read. A lazy per-row load
    would also raise MissingGreenlet on a detached instance in async SQLAlchemy —
    this asserts the batch shape that avoids both.
    """
    from sqlalchemy import event

    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    for i in range(6):
        u = await users_repo.get_or_create_user(
            session,
            source_user_id=f"many-{i}-sub",
            email=f"many{i}@test.local",
            display_name=f"Member {i}",
        )
        await teams_repo.add_member(session, team_id=team_a.id, user_id=u.id, role="member")
        await tm_repo.insert_user_message(
            session, team_id=team_a.id, author_user_id=u.id, content=f"message {i}"
        )
    await session.commit()

    user_selects: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        normalised = " ".join(statement.split()).lower()
        if normalised.startswith("select") and "from users" in normalised:
            user_selects.append(normalised)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        _install_principal(alice)
        try:
            r = await client.get(f"/v1/teams/{team_a.id}/messages")
        finally:
            _clear_principal()
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)

    assert r.status_code == 200, r.text
    labels = {m["author_label"] for m in r.json()["messages"] if m["author_label"]}
    assert {f"Member {i}" for i in range(6)} <= labels

    assert len(user_selects) == 1, (
        f"expected ONE batched author query, got {len(user_selects)} — a per-message "
        f"lookup is an N+1 and, on a detached instance, a MissingGreenlet waiting to "
        f"happen. Statements: {user_selects}"
    )


async def test_serializer_is_pure_and_needs_no_session(session, seeded_two_teams):
    """_serialize_message takes a prepared label map — it never loads anything.

    Keeping the serializer free of I/O is what makes the single batch query
    possible and what keeps a lazy attribute out of the render path.
    """
    from app.routes.team_chat import _serialize_message

    now = datetime.now(timezone.utc)
    row = types.SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        team_id=seeded_two_teams["team_a"].id,
        author_user_id="22222222-2222-4222-8222-222222222222",
        agent_name=None,
        kind="user",
        content="hello",
        created_at=now,
        routed_via=None,
        metadata_={},
        parent_message_id=None,
        edited_at=None,
    )
    out = _serialize_message(row, "team-a", {str(row.author_user_id): "Nico"})
    assert out["author_label"] == "Nico"

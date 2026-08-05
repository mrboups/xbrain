"""THE gate for deleting a team-chat message — real route, real Postgres.

A "delete works" claim proves nothing until a real DELETE traverses the REAL
`/v1/teams/{id}/messages/{message_id}` path against a REAL Postgres, with the
realtime publish CAPTURED rather than mocked away. Membership resolution, the
block check, the author/admin rule, the brain-link expansion and the audit write
all run for real — that is precisely what this file exists to prove.

What is locked here, one condition varied at a time:

  1. AUTHOR, scope=message — 200, the bubble leaves history, and the memory item
     the message seeded is UNTOUCHED. "Message only" has to actually leave the
     brain able to answer, or the two options are the same option.
  2. AUTHOR, scope=message_and_brain — 200, and the seeded item AND its linked
     CHILDREN (a document's body chunks, an image's vision description, both
     keyed off `metadata.parent_item_id`) are all soft-deleted. Deleting a parent
     and leaving its chunks recallable is the failure this test exists for.
  3. A NON-AUTHOR PLAIN MEMBER — 403, the message survives, NO audit row. The
     server is the authority; a client that drew the control anyway changes
     nothing.
  4. A TEAM ADMIN deleting someone else's message — 200. The rule is stated in
     both directions or it is not stated.
  5. A BLOCKED member — 403, even when they are the author of the message. This
     project has shipped a bypass on `blocked_at` once.
  6. CROSS-TEAM — a member of team B naming team A's message id gets 404, and the
     message survives. A bare uuid in a URL is not an authorisation.
  7. THE DELETION REACHES OTHER CLIENTS — exactly one publish, on the ACTIVE
     team's channel and no other, carrying `{type: message_deleted, message_id}`.
  8. THE AUDIT TRAIL — both scopes write exactly one row, under two DIFFERENT
     actions, naming the actor and (for the brain scope) what went with the
     message. A refused deletion writes none.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this
file. A clean SKIP is legitimate ONLY when Docker is genuinely absent (conftest
gate); under Docker this file MUST run green.
"""
from __future__ import annotations

import types
import uuid
from typing import Any

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (same shape as test_nudge_open_gate.py) ────────


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


class _QdrantRecorder:
    """Stands in for the memory provider's vector half.

    Only `mark_deleted` is exercised by this path. Recording it (rather than
    letting the real provider reach for a Qdrant that may not be running) keeps
    the test fast AND turns "vector search still returns the removed item" into an
    assertion instead of a hope.
    """

    def __init__(self) -> None:
        self.marked: list[str] = []

    async def mark_deleted(self, item_id: str, deleted_at) -> None:
        self.marked.append(str(item_id))


async def _insert_memory_item(
    session,
    *,
    team_scope: str,
    content: str,
    metadata: dict[str, Any],
    source: str = "team-chat:alice-sub",
) -> str:
    """Insert one live memory_items row and return its id.

    Written with raw SQL through the REQUEST's session on purpose: the provider
    owns a separate asyncpg pool, and a row written there would not be visible
    inside this test's transaction — the delete path reads through the session,
    so the fixture has to as well.
    """
    item_id = str(uuid.uuid4())
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
                (id, team_scope, content, metadata, visibility, truth_level,
                 validation_status, confidence, source)
            VALUES
                (:id, :ts, :content, CAST(:md AS jsonb), 'team', 'WORKING',
                 'pending', 0.7, :source)
            """
        ),
        {
            "id": item_id,
            "ts": team_scope,
            "content": content,
            "md": __import__("json").dumps(metadata),
            "source": source,
        },
    )
    return item_id


async def _item_is_live(session, item_id: str) -> bool:
    row = (
        await session.execute(
            sa.text("SELECT deleted_at FROM memory_items WHERE id = :id"),
            {"id": item_id},
        )
    ).fetchone()
    assert row is not None, f"memory item {item_id} vanished entirely"
    return row[0] is None


async def _audit_rows(session, message_id) -> list[Any]:
    return (
        await session.execute(
            sa.text(
                "SELECT action, actor_user_id, team_scope, payload "
                "FROM audit_log WHERE target_id = :t ORDER BY id"
            ),
            {"t": str(message_id)},
        )
    ).fetchall()


# ── The gate ─────────────────────────────────────────────────────────────────


async def test_message_delete_gate(client, seeded_two_teams, session, monkeypatch):
    from app.deps import get_memory_provider
    from app.main import app
    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    alice = seeded_two_teams["alice"]  # admin of team-a
    bob = seeded_two_teams["bob"]      # admin of team-b, NOT a member of team-a
    team_a = seeded_two_teams["team_a"]

    # carol and dave are PLAIN members of team-a. carol is the author under test;
    # dave is the non-author whose refusal is the point of case 3.
    carol = await users_repo.get_or_create_user(
        session, source_user_id="carol-sub", email="carol@test.local", display_name="Carol"
    )
    dave = await users_repo.get_or_create_user(
        session, source_user_id="dave-sub", email="dave@test.local", display_name="Dave"
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=carol.id, role="member")
    await teams_repo.add_member(session, team_id=team_a.id, user_id=dave.id, role="member")
    await session.commit()

    # Capture the realtime publish instead of firing it at a Centrifugo that is
    # not running. This is the ONLY network call stubbed.
    published: list[tuple[str, dict]] = []

    async def _recorder(channel, data):
        published.append((channel, data))
        return True

    monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)

    qdrant = _QdrantRecorder()
    app.dependency_overrides[get_memory_provider] = lambda: qdrant

    async def _delete_as(user, message_id, *, scope=None, team_id=None):
        _install_principal(user)
        try:
            url = f"/v1/teams/{team_id or team_a.id}/messages/{message_id}"
            if scope:
                url += f"?scope={scope}"
            return await client.delete(url)
        finally:
            _clear_principal()

    async def _new_message(author, content, *, metadata=None):
        msg = await tm_repo.insert_user_message(
            session,
            team_id=team_a.id,
            author_user_id=author.id,
            content=content,
            metadata=metadata,
        )
        await session.commit()
        return msg

    async def _history_ids(user):
        _install_principal(user)
        try:
            r = await client.get(f"/v1/teams/{team_a.id}/messages?limit=200")
        finally:
            _clear_principal()
        assert r.status_code == 200, r.text
        return r.json(), [m["id"] for m in r.json()["messages"]]

    try:
        # ── 1. AUTHOR, scope=message — the brain keeps what was said ─────────
        m1 = await _new_message(carol, "The Q3 budget was signed off on Tuesday.")
        kept_item = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="The Q3 budget was signed off on Tuesday.",
            metadata={
                "origin": "team-chat",
                "author_sub": "carol-sub",
                "message_id": str(m1.id),
            },
        )
        await session.commit()

        before = len(published)
        r = await _delete_as(carol, m1.id)  # no ?scope → defaults to message-only
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scope"] == "message"
        assert body["brain_items_removed"] == 0

        payload, ids = await _history_ids(carol)
        assert str(m1.id) not in ids, "a deleted message must leave the history"
        assert await _item_is_live(session, kept_item), (
            "scope=message must leave the memory item intact — otherwise the two "
            "options are the same option and nobody can choose between them"
        )
        assert qdrant.marked == [], "message-only must not touch the vector store"

        # ── 7. the deletion reached other clients, on the RIGHT channel ──────
        assert len(published) == before + 1, "exactly one publish per deletion"
        channel, frame = published[-1]
        assert channel == f"team:{team_a.id}", (
            "the frame must go to the ACTIVE team's channel and nothing else — the "
            "realtime token grants every team the caller belongs to"
        )
        assert frame["type"] == "message_deleted"
        assert frame["message_id"] == str(m1.id)
        assert frame["scope"] == "message"
        # Nothing about the message's CONTENT rides on a delete frame.
        assert "content" not in frame

        # ── 8a. one audit row, naming the actor and the authority ───────────
        rows = await _audit_rows(session, m1.id)
        assert len(rows) == 1, f"expected exactly one audit row, got {rows}"
        assert rows[0][0] == "team_message.delete"
        assert rows[0][1] == carol.id, "actor_user_id must be the real human id"
        assert rows[0][2] == team_a.slug
        assert rows[0][3]["actor_role"] == "author"
        assert rows[0][3]["scope"] == "message"
        assert "brain" not in rows[0][3], (
            "a message-only deletion must not claim a brain outcome it did not have"
        )

        # ── 2. AUTHOR, scope=message_and_brain — children go too ─────────────
        upload_id = str(uuid.uuid4())
        m2 = await _new_message(
            carol,
            "Here is the signed contract.",
            metadata={"media": {"item_id": upload_id, "mime": "application/pdf"}},
        )
        text_item = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="Here is the signed contract.",
            metadata={
                "origin": "team-chat",
                "author_sub": "carol-sub",
                "message_id": str(m2.id),
            },
        )
        # The upload's card item — the message points at it by id.
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items
                    (id, team_scope, content, metadata, visibility, truth_level,
                     validation_status, confidence, source)
                VALUES
                    (:id, :ts, 'contract.pdf', CAST(:md AS jsonb), 'team', 'WORKING',
                     'pending', 1.0, 'upload:card')
                """
            ),
            {
                "id": upload_id,
                "ts": team_a.slug,
                "md": __import__("json").dumps({"media": {"filename": "contract.pdf"}}),
            },
        )
        # Its CHILDREN: two body chunks and one vision description. Both kinds key
        # off `metadata.parent_item_id`, which is what the expansion walks.
        chunk_a = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="Clause 4: the term renews annually.",
            metadata={"parent_item_id": upload_id, "chunk_index": 0, "chunk_total": 2},
            source="upload:body",
        )
        chunk_b = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="Clause 9: notice period is 60 days.",
            metadata={"parent_item_id": upload_id, "chunk_index": 1, "chunk_total": 2},
            source="upload:body",
        )
        vision = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="A scanned signature page.",
            metadata={"parent_item_id": upload_id, "kind": "image_description"},
            source="vision:test-model",
        )
        # A bystander in the same team that this message does NOT own. Nothing
        # about it links to the message, so a delete that removed it would be
        # removing someone else's memory.
        bystander = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="Unrelated: the office wifi password changed.",
            metadata={"origin": "team-chat", "author_sub": "dave-sub"},
        )
        await session.commit()

        r = await _delete_as(carol, m2.id, scope="message_and_brain")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scope"] == "message_and_brain"
        assert body["brain_items_removed"] == 5, (
            "text item + upload card + 2 body chunks + 1 vision description; got "
            f"{body['brain_items_removed']}"
        )

        for item_id, what in (
            (text_item, "the message's own indexed text"),
            (upload_id, "the upload's card item"),
            (chunk_a, "a document body chunk"),
            (chunk_b, "a document body chunk"),
            (vision, "an image's vision description"),
        ):
            assert not await _item_is_live(session, item_id), (
                f"{what} is still live — the content stays findable after the "
                "person believed they removed it"
            )
        assert await _item_is_live(session, bystander), (
            "a deletion must not reach memory items the message does not own"
        )
        # The vector store was told about every one of them, not just the parent.
        assert sorted(qdrant.marked) == sorted(
            [text_item, upload_id, chunk_a, chunk_b, vision]
        )

        # ── 8b. the two scopes are DIFFERENT actions, and the row says what went
        rows = await _audit_rows(session, m2.id)
        assert len(rows) == 1
        assert rows[0][0] == "team_message.delete_with_brain", (
            "the two outcomes must be greppable apart; a flag in the payload is "
            "not an action"
        )
        assert rows[0][0] != "team_message.delete"
        brain = rows[0][3]["brain"]
        assert brain["items_removed"] == 5
        assert brain["child_count"] == 3, "the children must be reported, not implied"
        assert brain["root_count"] == 2
        assert sorted(brain["item_ids"]) == sorted(
            [text_item, upload_id, chunk_a, chunk_b, vision]
        )

        # ── 3. a non-author plain member is refused, and writes NO trail ─────
        m3 = await _new_message(carol, "A message dave has no business deleting.")
        await session.commit()
        before_pub = len(published)
        before_marked = len(qdrant.marked)

        r = await _delete_as(dave, m3.id, scope="message_and_brain")
        assert r.status_code == 403, r.text

        _, ids = await _history_ids(carol)
        assert str(m3.id) in ids, "a refused deletion must change nothing"
        assert len(published) == before_pub, "a refused deletion publishes nothing"
        assert len(qdrant.marked) == before_marked
        assert await _audit_rows(session, m3.id) == [], (
            "a refused deletion must write NO audit row — a trail that records "
            "attempts as if they were actions is worse than none"
        )

        # ── 4. a team admin may delete someone else's message ────────────────
        r = await _delete_as(alice, m3.id)  # alice is team-a's admin
        assert r.status_code == 200, r.text
        _, ids = await _history_ids(carol)
        assert str(m3.id) not in ids
        rows = await _audit_rows(session, m3.id)
        assert len(rows) == 1
        assert rows[0][1] == alice.id
        assert rows[0][3]["actor_role"] == "team_admin"

        # ── 6. cross-team: team-b's admin cannot touch team-a's message ──────
        m4 = await _new_message(carol, "A message bob must never reach.")
        await session.commit()
        r = await _delete_as(bob, m4.id, team_id=team_a.id)
        assert r.status_code == 403, r.text  # not a member of team-a at all
        # ...and naming it through his OWN team is a 404, not a leak.
        _install_principal(bob)
        try:
            r = await client.delete(
                f"/v1/teams/{seeded_two_teams['team_b'].id}/messages/{m4.id}"
            )
        finally:
            _clear_principal()
        assert r.status_code == 404, r.text
        _, ids = await _history_ids(carol)
        assert str(m4.id) in ids, "the message survived both attempts"

        # ── 5. a BLOCKED member is refused even on their OWN message ────────
        await teams_repo.block_member(
            session, team_id=team_a.id, user_id=carol.id, blocked_by=alice.id
        )
        await session.commit()
        before_pub = len(published)
        r = await _delete_as(carol, m4.id)
        assert r.status_code == 403, r.text
        _, ids = await _history_ids(alice)
        assert str(m4.id) in ids, "a blocked author's deletion must not land"
        assert len(published) == before_pub
        assert await _audit_rows(session, m4.id) == []
    finally:
        app.dependency_overrides.pop(get_memory_provider, None)


async def test_delete_is_idempotent_and_refuses_an_unknown_scope(
    client, seeded_two_teams, session, monkeypatch
):
    """A second DELETE is a 404, and an invented scope is a 422 that writes nothing.

    Both matter for the same reason: the client must not be able to talk the
    server into a third behaviour. `scope=brain_only` would be a plausible guess
    and there is no such outcome — a message whose bubble stays while its memory
    goes is not one of the two things the person was offered.
    """
    from app.deps import get_memory_provider
    from app.main import app
    from app.repos import team_messages as tm_repo

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    published: list[tuple[str, dict]] = []

    async def _recorder(channel, data):
        published.append((channel, data))
        return True

    monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)
    app.dependency_overrides[get_memory_provider] = lambda: _QdrantRecorder()

    try:
        msg = await tm_repo.insert_user_message(
            session, team_id=team_a.id, author_user_id=alice.id, content="Once only."
        )
        await session.commit()

        _install_principal(alice)
        try:
            bad = await client.delete(
                f"/v1/teams/{team_a.id}/messages/{msg.id}?scope=brain_only"
            )
            assert bad.status_code == 422, bad.text
            assert published == [], "a rejected scope must publish nothing"
            assert await _audit_rows(session, msg.id) == []

            first = await client.delete(f"/v1/teams/{team_a.id}/messages/{msg.id}")
            assert first.status_code == 200, first.text
            second = await client.delete(f"/v1/teams/{team_a.id}/messages/{msg.id}")
            assert second.status_code == 404, second.text
        finally:
            _clear_principal()

        assert len(published) == 1, "the second DELETE must not re-announce anything"
        assert len(await _audit_rows(session, msg.id)) == 1, (
            "the second DELETE must not write a second row — it removed nothing"
        )
    finally:
        app.dependency_overrides.pop(get_memory_provider, None)


async def test_a_message_ingested_before_the_backlink_can_still_be_removed(
    client, seeded_two_teams, session, monkeypatch
):
    """The legacy fallback: no `metadata.message_id`, matched on content instead.

    Every message already in a running deployment was ingested without the
    back-link. If the brain option quietly removed nothing for those, the person
    would be told it worked — the exact failure the feature exists to prevent.

    The match is narrow, and this proves the narrowness: same team, chat origin,
    same author, same text. A different author's identical sentence stays.
    """
    from app.deps import get_memory_provider
    from app.main import app
    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    erin = await users_repo.get_or_create_user(
        session, source_user_id="erin-sub", email="erin@test.local", display_name="Erin"
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=erin.id, role="member")
    await session.commit()

    async def _recorder(channel, data):
        return True

    monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)
    app.dependency_overrides[get_memory_provider] = lambda: _QdrantRecorder()

    SENTENCE = "The staging database is rebuilt every Sunday at 03:00."
    try:
        msg = await tm_repo.insert_user_message(
            session, team_id=team_a.id, author_user_id=erin.id, content=SENTENCE
        )
        await session.commit()

        legacy = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content=SENTENCE,
            metadata={"origin": "team-chat", "author_sub": "erin-sub"},
        )
        # Same words, different author — not erin's to remove.
        someone_else = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content=SENTENCE,
            metadata={"origin": "team-chat", "author_sub": "alice-sub"},
        )
        # Same words, same author, ANOTHER TEAM. The scope filter is the boundary.
        other_team = await _insert_memory_item(
            session,
            team_scope=team_b.slug,
            content=SENTENCE,
            metadata={"origin": "team-chat", "author_sub": "erin-sub"},
        )
        await session.commit()

        _install_principal(erin)
        try:
            r = await client.delete(
                f"/v1/teams/{team_a.id}/messages/{msg.id}?scope=message_and_brain"
            )
        finally:
            _clear_principal()
        assert r.status_code == 200, r.text
        assert r.json()["brain_items_removed"] == 1

        assert not await _item_is_live(session, legacy)
        assert await _item_is_live(session, someone_else), (
            "the fallback must not reach another author's identical sentence"
        )
        assert await _item_is_live(session, other_team), (
            "the fallback must never cross a team scope"
        )

        rows = await _audit_rows(session, msg.id)
        assert len(rows) == 1
        assert rows[0][3]["brain"]["matched_legacy_text"] is True, (
            "the row has to say the link was a content match, not an exact id — "
            "six months from now that is the difference between a certain removal "
            "and a probable one"
        )
    finally:
        app.dependency_overrides.pop(get_memory_provider, None)

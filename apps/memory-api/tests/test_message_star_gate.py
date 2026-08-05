"""THE gate for starring a message — real route, real Postgres, real audit rows.

Starred is the ONE truth level a person sets. That is the whole of its value, so
what this file locks is who may set it and who may not, one condition varied at a
time:

  1. A PLAIN MEMBER may star — starring is wider than deleting on purpose. It
     says "this one matters" about a message the whole team already shares.
  2. THE MEMORY ITEMS MOVE WITH IT. A star that only decorated the bubble would
     leave the agent ranking the item as ordinary, which is the entire point of
     the feature.
  3. UN-STARRING RESTORES, IT DOES NOT FLATTEN. An item the AI had judged final
     before a person starred it goes back to VALIDATED, not to WORKING.
  4. A BLOCKED MEMBER gets 403 — this project has shipped a bypass on
     `blocked_at` once.
  5. A NON-MEMBER naming another team's message id gets 404, and nothing moves.
  6. A BRIDGE PRINCIPAL gets 403. A token starring things would make this the
     level a machine can set, which is exactly what it must not be.
  7. THE AUDIT TRAIL — one row per real change, under two DIFFERENT actions,
     naming the human. A refused star and a repeated star both write none.
  8. THE STAR REACHES OTHER CLIENTS and shows up in history.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this
file. A clean SKIP is legitimate ONLY when Docker is genuinely absent (conftest
gate); under Docker this file MUST run green.
"""
from __future__ import annotations

import json
import types
import uuid
from typing import Any

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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


def _install_bridge_principal(member_user) -> None:
    """A bridge principal carrying a REAL member's user object.

    Deliberately adversarial. A bridge token with no user is refused by the
    membership check, so a test using one would pass no matter what
    `_require_user_principal` did — it would prove the wrong guard. Attaching a
    user who genuinely belongs to the team removes that second net, leaving
    `kind` as the only thing standing between a machine and the human level.
    """
    from app.deps import get_current_principal
    from app.main import app

    fake_user = types.SimpleNamespace(
        id=member_user.id,
        source_user_id=getattr(member_user, "source_user_id", None),
        email=None,
        display_name=None,
        github_username=None,
        github_id=None,
    )

    async def _override():
        return {
            "kind": "bridge",
            "sub": "bridge-service",
            "team_scope": "team-a",
            "user": fake_user,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


async def _insert_memory_item(
    session,
    *,
    team_scope: str,
    content: str,
    metadata: dict[str, Any],
    truth_level: str = "WORKING",
) -> str:
    item_id = str(uuid.uuid4())
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
                (id, team_scope, content, metadata, visibility, truth_level,
                 validation_status, confidence, source)
            VALUES
                (:id, :ts, :content, CAST(:md AS jsonb), 'team', :level,
                 'pending', 0.7, 'team-chat:carol-sub')
            """
        ),
        {
            "id": item_id,
            "ts": team_scope,
            "content": content,
            "md": json.dumps(metadata),
            "level": truth_level,
        },
    )
    return item_id


async def _item_level(session, item_id: str) -> str:
    return (
        await session.execute(
            sa.text("SELECT truth_level FROM memory_items WHERE id = CAST(:id AS uuid)"),
            {"id": item_id},
        )
    ).scalar_one()


async def _message_level(session, message_id) -> str:
    return (
        await session.execute(
            sa.text("SELECT truth_level FROM team_messages WHERE id = :id"),
            {"id": str(message_id)},
        )
    ).scalar_one()


async def _audit_rows(session, target_id) -> list[Any]:
    return (
        await session.execute(
            sa.text(
                "SELECT action, actor_user_id, team_scope, payload "
                "FROM audit_log WHERE target_id = :t ORDER BY id"
            ),
            {"t": str(target_id)},
        )
    ).fetchall()


async def test_message_star_gate(client, seeded_two_teams, session, monkeypatch):
    from app.main import app
    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    bob = seeded_two_teams["bob"]      # admin of team-b, NOT a member of team-a
    team_a = seeded_two_teams["team_a"]

    carol = await users_repo.get_or_create_user(
        session, source_user_id="carol-sub", email="carol@test.local", display_name="Carol"
    )
    dave = await users_repo.get_or_create_user(
        session, source_user_id="dave-sub", email="dave@test.local", display_name="Dave"
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=carol.id, role="member")
    await teams_repo.add_member(session, team_id=team_a.id, user_id=dave.id, role="member")
    await session.commit()

    published: list[tuple[str, dict]] = []

    async def _recorder(channel, data):
        published.append((channel, data))
        return True

    monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)

    async def _star_as(user, message_id, starred, *, team_id=None, kind="user"):
        if kind == "bridge":
            _install_bridge_principal(user)
        else:
            _install_principal(user, kind=kind)
        try:
            return await client.put(
                f"/v1/teams/{team_id or team_a.id}/messages/{message_id}/star",
                json={"starred": starred},
            )
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

    try:
        # ── 1 + 2. A PLAIN MEMBER stars SOMEONE ELSE'S message, and the memory
        #           items it seeded move with it ────────────────────────────
        m1 = await _new_message(carol, "Q3 budget signed off by finance: 240k.")
        plain_item = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="Q3 budget signed off by finance: 240k.",
            metadata={
                "origin": "team-chat",
                "author_sub": "carol-sub",
                "message_id": str(m1.id),
            },
        )
        await session.commit()

        before = len(published)
        r = await _star_as(dave, m1.id, True)  # dave is NOT the author
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["starred"] is True
        assert body["changed"] is True
        assert body["brain_items"] == 1

        assert await _message_level(session, m1.id) == "CANONICAL"
        assert await _item_level(session, plain_item) == "CANONICAL", (
            "a star that only decorates the bubble leaves the agent ranking the "
            "item as ordinary — which is the whole point of the feature"
        )

        # ── 8. it reached the other screens, on the RIGHT channel ───────────
        assert len(published) == before + 1, "exactly one publish per real change"
        channel, frame = published[-1]
        assert channel == f"team:{team_a.id}"
        assert frame["type"] == "message_starred"
        assert frame["message_id"] == str(m1.id)
        assert frame["starred"] is True
        assert "content" not in frame

        # …and it shows in history, as a boolean rather than the raw enum.
        _install_principal(dave)
        try:
            hist = await client.get(f"/v1/teams/{team_a.id}/messages?limit=200")
        finally:
            _clear_principal()
        assert hist.status_code == 200, hist.text
        rendered = {m["id"]: m for m in hist.json()["messages"]}
        assert rendered[str(m1.id)]["starred"] is True
        assert "CANONICAL" not in json.dumps(rendered[str(m1.id)]), (
            "the client is told 'starred', not the internal level it must not write"
        )

        # ── 7a. one audit row, naming the human ────────────────────────────
        rows = await _audit_rows(session, m1.id)
        assert len(rows) == 1, f"expected exactly one audit row, got {rows}"
        action, actor_user_id, audited_scope, payload = rows[0]
        assert action == "team_message.star"
        assert actor_user_id == dave.id, "the actor is the real human id, never NULL"
        assert audited_scope == team_a.slug
        assert payload["actor_kind"] == "user"
        assert payload["starred"] is True
        assert payload["brain"]["items_moved"] == 1
        assert payload["brain"]["item_ids"] == [plain_item]

        # ── 7b. a REPEATED star is not a second decision ───────────────────
        before = len(published)
        r = await _star_as(carol, m1.id, True)
        assert r.status_code == 200, r.text
        assert r.json()["changed"] is False
        assert r.json()["starred"] is True
        assert len(await _audit_rows(session, m1.id)) == 1, (
            "a second tap on an already-starred message must add no row"
        )
        assert len(published) == before, "and must publish nothing"

        # ── 3. UN-STARRING RESTORES what the AI thought, it does not flatten ─
        m2 = await _new_message(carol, "Migration 0034 is merged and applied.")
        ai_flagged = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="Migration 0034 is merged and applied.",
            metadata={
                "origin": "team-chat",
                "author_sub": "carol-sub",
                "message_id": str(m2.id),
                "ai_important": True,
            },
            truth_level="VALIDATED",
        )
        ordinary = await _insert_memory_item(
            session,
            team_scope=team_a.slug,
            content="a chunk of the same message's attachment",
            metadata={"origin": "team-chat", "parent_item_id": ai_flagged},
        )
        await session.commit()

        r = await _star_as(carol, m2.id, True)
        assert r.status_code == 200, r.text
        assert r.json()["brain_items"] == 2
        assert await _item_level(session, ai_flagged) == "CANONICAL"
        assert await _item_level(session, ordinary) == "CANONICAL", (
            "a document's chunks travel with the message, same as on delete"
        )

        r = await _star_as(carol, m2.id, False)
        assert r.status_code == 200, r.text
        assert r.json()["starred"] is False
        assert r.json()["changed"] is True
        assert await _message_level(session, m2.id) == "WORKING"
        assert await _item_level(session, ai_flagged) == "VALIDATED", (
            "the star covered the AI's opinion, it did not erase it — un-starring "
            "must return the item to VALIDATED, not flatten it to WORKING"
        )
        assert await _item_level(session, ordinary) == "WORKING", (
            "and an item nothing had judged goes back to plain WORKING"
        )

        rows = await _audit_rows(session, m2.id)
        assert [r_[0] for r_ in rows] == ["team_message.star", "team_message.unstar"], (
            "set and clear must be DIFFERENT actions — the question later is "
            "which one happened"
        )
        assert rows[1][1] == carol.id

        # ── 7c. un-starring something that was never starred changes nothing ─
        m3 = await _new_message(carol, "An ordinary line nobody starred yet.")
        r = await _star_as(carol, m3.id, False)
        assert r.status_code == 200, r.text
        assert r.json()["changed"] is False
        assert await _message_level(session, m3.id) == "WORKING"
        assert await _audit_rows(session, m3.id) == []

        # ── 4. A BLOCKED MEMBER — 403, even on their own message ───────────
        m4 = await _new_message(dave, "Dave's own message, and he is about to be blocked.")
        await session.execute(
            sa.text(
                "UPDATE team_members SET blocked_at = NOW() "
                "WHERE team_id = :t AND user_id = :u"
            ),
            {"t": str(team_a.id), "u": str(dave.id)},
        )
        await session.commit()

        r = await _star_as(dave, m4.id, True)
        assert r.status_code == 403, r.text
        assert await _message_level(session, m4.id) == "WORKING"
        assert await _audit_rows(session, m4.id) == [], (
            "a refused star writes NOTHING — a trail that records attempts as if "
            "they were actions is worse than no trail"
        )

        # ── 5. CROSS-TEAM — a bare uuid in a URL is not an authorisation ────
        r = await _star_as(bob, m1.id, False, team_id=team_a.id)
        assert r.status_code == 403, r.text
        assert await _message_level(session, m1.id) == "CANONICAL", (
            "the star set in case 1 must survive an outsider's attempt to clear it"
        )
        assert len(await _audit_rows(session, m1.id)) == 1

        # ── 6. A BRIDGE PRINCIPAL — 403. Starring stays human-only ─────────
        #
        # carol is a genuine, non-blocked member of team-a, so the membership
        # check would WAVE THIS THROUGH. The only thing refusing it is `kind`,
        # which is why the detail string is asserted: two different guards both
        # answer 403 here, and the test has to name which one fired or it proves
        # the wrong mechanism.
        r = await _star_as(carol, m1.id, False, kind="bridge")
        assert r.status_code == 403, r.text
        assert "user-only" in r.json()["detail"], (
            f"refused by the wrong guard: {r.json()['detail']!r}"
        )
        assert await _message_level(session, m1.id) == "CANONICAL", (
            "a machine that can set this level makes it a level a machine sets, "
            "which is exactly what it must not be"
        )
        assert len(await _audit_rows(session, m1.id)) == 1
    finally:
        app.dependency_overrides.pop("_", None)
        _clear_principal()

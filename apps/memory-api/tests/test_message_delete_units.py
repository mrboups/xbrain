"""Per-layer contracts behind message deletion — each one on its own.

WHY THIS FILE EXISTS. Mutation-testing the end-to-end gate
(`test_message_delete_gate.py`) found two breaks it could not see: dropping the
team filter from `get_live_message`, and dropping it from the legacy content
fallback in `collect_linked_items`. Neither changed the endpoint's behaviour,
because a SECOND filter downstream (`soft_delete_message` / `soft_delete_items`)
still refused the foreign row.

That redundancy is deliberate and worth keeping — but redundancy that no test
sees is redundancy nobody will notice losing. So each layer's own promise is
pinned here, and a single removed WHERE clause goes red even though the product
would still behave.

Real Postgres (the queries are raw SQL and JSONB operators — a fake would prove
nothing about either).
"""
from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _insert_item(session, *, team_scope, content, metadata, source="test") -> str:
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
            "md": json.dumps(metadata),
            "source": source,
        },
    )
    return item_id


async def _deleted_at(session, item_id):
    row = (
        await session.execute(
            sa.text("SELECT deleted_at FROM memory_items WHERE id = :id"),
            {"id": item_id},
        )
    ).fetchone()
    return None if row is None else row[0]


# ── repos/team_messages ──────────────────────────────────────────────────────


async def test_get_live_message_is_bounded_to_its_team(session, seeded_two_teams):
    """A bare message uuid in a URL is not an authorisation.

    The team predicate here is the first of two that stop a member of team B
    reaching team A's row. Removing it must be visible somewhere, and this is
    where.
    """
    from app.repos import team_messages as tm_repo

    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]
    alice = seeded_two_teams["alice"]

    msg = await tm_repo.insert_user_message(
        session, team_id=team_a.id, author_user_id=alice.id, content="Team A only."
    )
    await session.commit()

    assert await tm_repo.get_live_message(
        session, team_id=team_a.id, message_id=msg.id
    ) is not None
    assert await tm_repo.get_live_message(
        session, team_id=team_b.id, message_id=msg.id
    ) is None, "a message must not be reachable through another team's id"


async def test_soft_delete_message_refuses_another_team_and_repeats(
    session, seeded_two_teams
):
    """The WRITE half carries the same team predicate, and will not delete twice.

    Returning None rather than a timestamp is what keeps the route's answer
    honest: it reports a removal only when one happened.
    """
    from app.repos import team_messages as tm_repo

    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]

    msg = await tm_repo.insert_user_message(
        session, team_id=team_a.id, author_user_id=alice.id, content="Team A only."
    )
    await session.commit()

    assert await tm_repo.soft_delete_message(
        session, team_id=team_b.id, message_id=msg.id, deleted_by=bob.id
    ) is None
    still = await tm_repo.get_live_message(
        session, team_id=team_a.id, message_id=msg.id
    )
    assert still is not None, "the wrong team's DELETE must not have flipped the row"

    first = await tm_repo.soft_delete_message(
        session, team_id=team_a.id, message_id=msg.id, deleted_by=alice.id
    )
    assert first is not None
    second = await tm_repo.soft_delete_message(
        session, team_id=team_a.id, message_id=msg.id, deleted_by=bob.id
    )
    assert second is None, (
        "a repeat must change nothing — otherwise it overwrites the first "
        "deleter's identity and timestamp with the second's"
    )


# ── services/message_brain_links ─────────────────────────────────────────────


async def test_collect_never_crosses_a_team_scope(session, seeded_two_teams):
    """Every branch of the collector is bounded to one team — including the
    content fallback, which is the one that matches on text rather than on an id
    and would otherwise reach an identical sentence in another team's brain."""
    from app.services import message_brain_links

    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]
    message_id = uuid.uuid4()
    SENTENCE = "Deployments freeze during the last week of the quarter."

    mine = await _insert_item(
        session,
        team_scope=team_a.slug,
        content=SENTENCE,
        metadata={"origin": "team-chat", "author_sub": "alice-sub"},
    )
    theirs = await _insert_item(
        session,
        team_scope=team_b.slug,
        content=SENTENCE,
        metadata={"origin": "team-chat", "author_sub": "alice-sub"},
    )
    # An exact back-link, but in the other team. Even the precise branch must not
    # follow it across the boundary.
    theirs_linked = await _insert_item(
        session,
        team_scope=team_b.slug,
        content="Something else entirely.",
        metadata={"origin": "team-chat", "message_id": str(message_id)},
    )
    await session.commit()

    linked = await message_brain_links.collect_linked_items(
        session,
        team_scope=team_a.slug,
        message_id=message_id,
        content=SENTENCE,
        author_sub="alice-sub",
        message_metadata=None,
    )
    assert linked.all_ids == [mine]
    assert theirs not in linked.all_ids
    assert theirs_linked not in linked.all_ids
    assert linked.matched_legacy_text is True


async def test_collect_prefers_the_backlink_over_the_text_match(
    session, seeded_two_teams
):
    """With a `message_id` present the content fallback never runs.

    Which is the whole reason the back-link was added: an exact link removes what
    the person pointed at, a text match removes what merely reads the same.
    """
    from app.services import message_brain_links

    team_a = seeded_two_teams["team_a"]
    message_id = uuid.uuid4()
    SENTENCE = "The retro moved to Thursday."

    linked_item = await _insert_item(
        session,
        team_scope=team_a.slug,
        content=SENTENCE,
        metadata={
            "origin": "team-chat",
            "author_sub": "alice-sub",
            "message_id": str(message_id),
        },
    )
    twin = await _insert_item(
        session,
        team_scope=team_a.slug,
        content=SENTENCE,
        metadata={"origin": "team-chat", "author_sub": "alice-sub"},
    )
    await session.commit()

    linked = await message_brain_links.collect_linked_items(
        session,
        team_scope=team_a.slug,
        message_id=message_id,
        content=SENTENCE,
        author_sub="alice-sub",
        message_metadata=None,
    )
    assert linked.all_ids == [linked_item]
    assert twin not in linked.all_ids
    assert linked.matched_legacy_text is False


async def test_collect_walks_past_the_first_generation(session, seeded_two_teams):
    """The expansion is a walk, not one join.

    Today's tree is two deep (upload -> chunks). A chunk that ever gains a child
    of its own must not be the thing left behind, so the loop is proven to reach
    a grandchild rather than assumed to.
    """
    from app.services import message_brain_links

    team_a = seeded_two_teams["team_a"]
    parent = await _insert_item(
        session, team_scope=team_a.slug, content="report.pdf", metadata={}
    )
    child = await _insert_item(
        session,
        team_scope=team_a.slug,
        content="page one",
        metadata={"parent_item_id": parent},
    )
    grandchild = await _insert_item(
        session,
        team_scope=team_a.slug,
        content="a figure on page one",
        metadata={"parent_item_id": child},
    )
    await session.commit()

    linked = await message_brain_links.collect_linked_items(
        session,
        team_scope=team_a.slug,
        message_id=uuid.uuid4(),
        content="",
        author_sub=None,
        message_metadata={"media": {"item_id": parent}},
    )
    assert linked.roots == [parent]
    assert sorted(linked.children) == sorted([child, grandchild])


async def test_soft_delete_items_refuses_ids_outside_its_scope(
    session, seeded_two_teams
):
    """The WRITE half re-applies the scope filter the collector already applied.

    Two independent gates, on purpose: a mis-scoped write is the only cross-team
    leak vector this feature has, and a defence that exists only in the read half
    is one refactor away from not existing.
    """
    from app.services import message_brain_links

    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]
    alice = seeded_two_teams["alice"]

    mine = await _insert_item(
        session, team_scope=team_a.slug, content="ours", metadata={}
    )
    theirs = await _insert_item(
        session, team_scope=team_b.slug, content="theirs", metadata={}
    )
    await session.commit()

    from datetime import datetime, timezone

    removed = await message_brain_links.soft_delete_items(
        session,
        team_scope=team_a.slug,
        item_ids=[mine, theirs],
        deleted_by=alice.id,
        deleted_at=datetime.now(tz=timezone.utc),
    )
    await session.commit()

    assert removed == [mine]
    assert await _deleted_at(session, mine) is not None
    assert await _deleted_at(session, theirs) is None, (
        "an id from another team must not be flipped even when it is handed in"
    )

    again = await message_brain_links.soft_delete_items(
        session,
        team_scope=team_a.slug,
        item_ids=[mine],
        deleted_by=alice.id,
        deleted_at=datetime.now(tz=timezone.utc),
    )
    assert again == [], "a repeat removes nothing and must report nothing"

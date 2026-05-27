"""conv_enricher tests — idempotency + skip cases + happy path.

Uses mongomock for the Mongo side and an inline fake MemoryApiClient
to control /v1/system-prompt responses without spinning up the real API.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


class FakeMemClient:
    """Stand-in for MemoryApiClient — records calls and returns canned responses."""

    def __init__(self, addendum: str = "", fact_count: int = 0) -> None:
        self.addendum = addendum
        self.fact_count = fact_count
        self.calls: list[dict[str, Any]] = []

    async def get_system_prompt(self, *, sub: str, team_scope: str, query: str, **kwargs) -> dict:
        self.calls.append({"sub": sub, "team_scope": team_scope, "query": query, **kwargs})
        return {"system_addendum": self.addendum, "fact_count": self.fact_count}


async def test_enrich_skips_already_enriched_conv(mongomock_db):
    from app.conv_enricher import enrich_new_conversation

    db, _alice, _bob = mongomock_db
    await db["conversations"].update_one(
        {"conversationId": "conv-1"},
        {"$set": {"title": "Auth question", "metadata": {"xbrain_enriched": True}}},
    )
    conv_doc = await db["conversations"].find_one({"conversationId": "conv-1"})

    mem = FakeMemClient(addendum="something", fact_count=3)
    result = await enrich_new_conversation(
        conv_doc, db, mem, sub="alice-google-sub", team_scope="team-a"
    )

    assert result is False
    assert mem.calls == []  # never even called memory-api
    # No system message inserted
    sys_msgs = await db["messages"].count_documents({"user": "system"})
    assert sys_msgs == 0


async def test_enrich_skips_conv_without_title(mongomock_db):
    """Empty title → skip without marking enriched (will retry on next event with title)."""
    from app.conv_enricher import enrich_new_conversation

    db, _alice, _bob = mongomock_db
    conv_doc = await db["conversations"].find_one({"conversationId": "conv-1"})

    mem = FakeMemClient(addendum="x", fact_count=1)
    result = await enrich_new_conversation(
        conv_doc, db, mem, sub="alice-google-sub", team_scope="team-a"
    )

    assert result is False
    assert mem.calls == []
    # NOT marked enriched — must retry when title arrives
    refreshed = await db["conversations"].find_one({"conversationId": "conv-1"})
    assert (refreshed.get("metadata") or {}).get("xbrain_enriched") is not True


async def test_enrich_skips_when_no_canonical_facts_but_marks_enriched(mongomock_db):
    from app.conv_enricher import enrich_new_conversation

    db, _alice, _bob = mongomock_db
    await db["conversations"].update_one(
        {"conversationId": "conv-1"}, {"$set": {"title": "Some new question"}}
    )
    conv_doc = await db["conversations"].find_one({"conversationId": "conv-1"})

    mem = FakeMemClient(addendum="", fact_count=0)
    result = await enrich_new_conversation(
        conv_doc, db, mem, sub="alice-google-sub", team_scope="team-a"
    )

    assert result is False
    assert len(mem.calls) == 1
    sys_msgs = await db["messages"].count_documents({"user": "system"})
    assert sys_msgs == 0
    refreshed = await db["conversations"].find_one({"conversationId": "conv-1"})
    assert refreshed["metadata"]["xbrain_enriched"] is True
    assert refreshed["metadata"]["xbrain_facts_injected"] == 0


async def test_enrich_inserts_system_message_with_addendum(mongomock_db):
    from app.conv_enricher import enrich_new_conversation

    db, _alice, _bob = mongomock_db
    await db["conversations"].update_one(
        {"conversationId": "conv-1"}, {"$set": {"title": "How does our auth flow?"}}
    )
    conv_doc = await db["conversations"].find_one({"conversationId": "conv-1"})

    addendum = "## Team facts\n- (deadbeef, conf=0.95): Auth uses Google OAuth"
    mem = FakeMemClient(addendum=addendum, fact_count=1)
    result = await enrich_new_conversation(
        conv_doc, db, mem, sub="alice-google-sub", team_scope="team-a"
    )

    assert result is True
    assert len(mem.calls) == 1
    assert mem.calls[0]["query"] == "How does our auth flow?"
    assert mem.calls[0]["team_scope"] == "team-a"

    # Exactly one system message inserted
    sys_msgs = await db["messages"].find({"user": "system"}).to_list(length=10)
    assert len(sys_msgs) == 1
    assert sys_msgs[0]["text"] == addendum
    assert sys_msgs[0]["conversationId"] == "conv-1"
    assert sys_msgs[0]["isCreatedByUser"] is False
    assert sys_msgs[0]["metadata"]["xbrain_injected"] is True
    assert sys_msgs[0]["metadata"]["fact_count"] == 1

    refreshed = await db["conversations"].find_one({"conversationId": "conv-1"})
    assert refreshed["metadata"]["xbrain_enriched"] is True
    assert refreshed["metadata"]["xbrain_facts_injected"] == 1


async def test_enrich_is_idempotent_on_second_call(mongomock_db):
    """Two consecutive change-stream events for the same conv must NOT inject 2x."""
    from app.conv_enricher import enrich_new_conversation

    db, _alice, _bob = mongomock_db
    await db["conversations"].update_one(
        {"conversationId": "conv-1"}, {"$set": {"title": "topic X"}}
    )

    mem = FakeMemClient(addendum="some addendum", fact_count=1)

    # First call enriches
    conv_doc_1 = await db["conversations"].find_one({"conversationId": "conv-1"})
    r1 = await enrich_new_conversation(
        conv_doc_1, db, mem, sub="alice-google-sub", team_scope="team-a"
    )
    assert r1 is True

    # Second call sees the enriched flag and bails — no second insert
    conv_doc_2 = await db["conversations"].find_one({"conversationId": "conv-1"})
    r2 = await enrich_new_conversation(
        conv_doc_2, db, mem, sub="alice-google-sub", team_scope="team-a"
    )
    assert r2 is False
    assert len(mem.calls) == 1  # mem-api only called once

    sys_msgs = await db["messages"].count_documents({"user": "system"})
    assert sys_msgs == 1


async def test_enrich_handles_memapi_failure_without_marking_enriched(mongomock_db):
    """If memory-api call raises, leave conv unmarked so a retry will happen."""
    from app.conv_enricher import enrich_new_conversation

    db, _alice, _bob = mongomock_db
    await db["conversations"].update_one(
        {"conversationId": "conv-1"}, {"$set": {"title": "topic Y"}}
    )
    conv_doc = await db["conversations"].find_one({"conversationId": "conv-1"})

    class FailingMem:
        async def get_system_prompt(self, **_):
            raise RuntimeError("memory-api unreachable")

    result = await enrich_new_conversation(
        conv_doc, db, FailingMem(), sub="alice-google-sub", team_scope="team-a"
    )

    assert result is False
    refreshed = await db["conversations"].find_one({"conversationId": "conv-1"})
    # NOT marked enriched — next change-stream event will retry
    assert (refreshed.get("metadata") or {}).get("xbrain_enriched") is not True

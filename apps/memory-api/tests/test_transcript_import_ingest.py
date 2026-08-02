"""The fan-out — provenance, truth level, and the per-turn idempotency key.

Unit tests: ``brain_ingest.ingest_external_message`` is mocked, so what is
asserted is the CONTRACT this module hands to the ingest path, not the ingest
path itself (which has its own tests in test_brain_ingest_endpoint.py).

The assertion worth reading twice is
``test_the_idempotency_key_is_team_scoped``. Without ``team_scope`` in that
key, importing the same conversation into team B computes the same uuid5
``MemoryItem.id`` as team A's copy and OVERWRITES it — a cross-team write
straight through the isolation boundary that is this product's whole point.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.transcript_import import ingest as import_ingest
from app.services.transcript_import.types import ParsedConversation, Turn

pytestmark = pytest.mark.asyncio


def _conversation(source_id: str = "conv-1") -> ParsedConversation:
    return ParsedConversation(
        turns=[
            Turn("user", "Which VM size are we on?",
                 datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)),
            Turn("assistant", "An e2-standard-2: 2 vCPU, 8 GB."),
        ],
        source_id=source_id,
        title="Infra sizing",
    )


async def _run(conv, *, team_scope="team-a", source_format="chatgpt", project_scope=None):
    captured = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    import_id = uuid4()
    with patch(
        "app.services.brain_ingest.ingest_external_message",
        new=AsyncMock(side_effect=_capture),
    ):
        await import_ingest.fan_out(
            team_scope=team_scope,
            source_format=source_format,
            pending=[(import_id, conv)],
            author_sub="alice-sub",
            project_scope=project_scope,
            concurrency=4,
        )
    return import_id, captured


async def test_every_turn_reaches_the_ingest_path():
    _id, captured = await _run(_conversation())
    assert len(captured) == 2
    assert [c["content"] for c in captured] == [
        "Which VM size are we on?",
        "An e2-standard-2: 2 vCPU, 8 GB.",
    ]


@pytest.mark.parametrize(
    ("source_format", "expected"),
    [("chatgpt", "import:chatgpt"), ("claude-code", "import:claude-code")],
)
async def test_provenance_names_the_source_product(source_format, expected):
    _id, captured = await _run(_conversation(), source_format=source_format)
    assert {c["source"] for c in captured} == {expected}


async def test_the_tagging_contract_travels_with_every_turn():
    _id, captured = await _run(_conversation(), team_scope="team-a", project_scope="proj-x")
    for call in captured:
        assert call["team_scope"] == "team-a"
        assert call["project_scope"] == "proj-x"
        assert call["author_sub"] == "alice-sub"
        md = call["metadata"]
        assert md["origin"] == "transcript-import"
        assert md["source_format"] == "chatgpt"
        assert md["source_conversation_id"] == "conv-1"
        assert md["conversation_title"] == "Infra sizing"
        assert md["turn_role"] in ("user", "assistant")


async def test_turn_order_is_preserved_in_the_metadata():
    _id, captured = await _run(_conversation())
    assert [c["metadata"]["turn_index"] for c in captured] == [0, 1]


async def test_a_missing_timestamp_is_an_empty_string_not_a_crash():
    _id, captured = await _run(_conversation())
    assert captured[0]["metadata"]["turn_timestamp"].startswith("2026-08-01T09:00:00")
    assert captured[1]["metadata"]["turn_timestamp"] == ""


async def test_the_idempotency_key_makes_a_re_import_an_upsert():
    """Same conversation twice → same keys → same uuid5 ids → no second copy."""
    conv = _conversation()
    _a, first = await _run(conv)
    _b, second = await _run(conv)
    keys_first = [c["metadata"]["idempotency_key"] for c in first]
    keys_second = [c["metadata"]["idempotency_key"] for c in second]
    assert keys_first == keys_second
    assert len(set(keys_first)) == 2  # and distinct within the conversation


async def test_the_idempotency_key_is_team_scoped():
    """Two teams importing one conversation must not compute the same item ids."""
    conv = _conversation()
    _a, team_a = await _run(conv, team_scope="team-a")
    _b, team_b = await _run(conv, team_scope="team-b")
    keys_a = {c["metadata"]["idempotency_key"] for c in team_a}
    keys_b = {c["metadata"]["idempotency_key"] for c in team_b}
    assert keys_a.isdisjoint(keys_b)


async def test_the_idempotency_key_shape_is_stable():
    assert (
        import_ingest.turn_idempotency_key("team-a", "chatgpt:conv-1", 3)
        == "import:team-a:chatgpt:conv-1#3"
    )


async def test_the_truth_level_is_not_raised_by_importing():
    """The route advertises WORKING; the ingest path is what writes it.

    ingest_external_message hardcodes TruthLevel.WORKING for every external
    ingest, so the fan-out passes no truth_level of its own. If a future change
    adds one here, that is the moment to re-read TRUTH_LEVEL_RATIONALE.
    """
    _id, captured = await _run(_conversation())
    assert all("truth_level" not in c for c in captured)
    assert "WORKING" in import_ingest.TRUTH_LEVEL_RATIONALE


async def test_the_fan_out_never_raises():
    """A failed import must not take down the process it was fired from."""
    with patch(
        "app.services.brain_ingest.ingest_external_message",
        new=AsyncMock(side_effect=RuntimeError("qdrant is down")),
    ):
        result = await import_ingest.fan_out(
            team_scope="team-a",
            source_format="chatgpt",
            pending=[(uuid4(), _conversation())],
            author_sub="alice-sub",
            project_scope=None,
            concurrency=2,
        )
    assert result is None


async def test_concurrency_is_bounded():
    """A 500-turn export must not fire 500 classifier calls in one burst."""
    in_flight = 0
    peak = 0

    async def _slow(**_kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1

    conv = ParsedConversation(
        turns=[Turn("user", f"fact number {i} worth storing") for i in range(50)],
        source_id="conv-wide",
    )
    with patch(
        "app.services.brain_ingest.ingest_external_message",
        new=AsyncMock(side_effect=_slow),
    ):
        await import_ingest.fan_out(
            team_scope="team-a",
            source_format="chatgpt",
            pending=[(uuid4(), conv)],
            author_sub=None,
            project_scope=None,
            concurrency=3,
        )
    assert peak <= 3, f"{peak} turns were in flight at once with concurrency=3"


async def test_an_empty_pending_list_is_a_no_op():
    with patch(
        "app.services.brain_ingest.ingest_external_message", new=AsyncMock()
    ) as ingest:
        await import_ingest.fan_out(
            team_scope="team-a",
            source_format="chatgpt",
            pending=[],
            author_sub=None,
            project_scope=None,
            concurrency=4,
        )
    ingest.assert_not_called()

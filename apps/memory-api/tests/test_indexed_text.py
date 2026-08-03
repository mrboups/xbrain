"""Tests for GET /v1/media/{item_id}/indexed-text and its resolver.

Strategy: unit-level against a fake provider. `resolve_indexed_text` needs exactly
one capability — `get(item_id, team_scope=...)` — so a dict-backed stub exercises
every branch with no DB, no MinIO and no vector store. The route itself is a thin
auth + 404 wrapper over this function, and its auth gate is `get_team_scope`, which
is already covered where it is defined.

What is locked here:
  (a) every no-text state resolves to its OWN state + its OWN sentence — the
      failure mode this feature exists to prevent is three different situations
      rendering as one empty tooltip;
  (b) the `detail` sentence is never a stored machine reason. `describe_error:
      APIStatusError` names a vendor SDK class; the allow-list in
      `_detail_for_reason` is the only source of user-visible words, and its
      fallback is generic on purpose;
  (c) the child read carries the CALLER's team_scope — a child id is computed
      (uuid5), so its own scope check is the thing that stops a guessed id from
      crossing teams;
  (d) documents and images both resolve in ONE call, from the parent alone.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.doc_body_ingest import DOCBODY_INGEST_NS
from app.services.image_describe import IMAGE_DESCRIBE_NS
from app.services.indexed_text import (
    PREVIEW_CHARS,
    STATE_FAILED,
    STATE_INDEXED,
    STATE_NOT_INDEXED,
    STATE_PENDING,
    _detail_for_reason,
    resolve_indexed_text,
)

PARENT = "11111111-1111-4111-8111-111111111111"
TEAM = "team-alpha"


@dataclass
class FakeItem:
    id: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class FakeProvider:
    """Dict-backed `get`, recording the team_scope every read was made with."""

    def __init__(self, items: dict[str, FakeItem] | None = None) -> None:
        self.items = items or {}
        self.reads: list[tuple[str, str]] = []

    async def get(self, item_id: str, *, team_scope: str) -> FakeItem | None:
        self.reads.append((item_id, team_scope))
        item = self.items.get(item_id)
        # Mirrors the real contract: a team_scope mismatch reads as absent.
        if item is None:
            return None
        owner = item.metadata.get("_team_scope", TEAM)
        return item if owner == team_scope else None


def image_parent(flag: dict[str, Any] | None = None) -> FakeItem:
    meta: dict[str, Any] = {"media": {"mime": "image/png", "key": "k", "filename": "a.png"}}
    if flag is not None:
        meta["image_description"] = flag
    return FakeItem(id=PARENT, content="a.png", metadata=meta)


def doc_parent(**extra: Any) -> FakeItem:
    meta: dict[str, Any] = {
        "media": {"mime": "application/pdf", "key": "k", "filename": "a.pdf"}
    }
    meta.update(extra)
    return FakeItem(id=PARENT, content="a.pdf", metadata=meta)


def image_child(text: str) -> FakeItem:
    return FakeItem(id=str(uuid.uuid5(IMAGE_DESCRIBE_NS, PARENT)), content=text)


def doc_chunk(index: int, text: str, total: int) -> FakeItem:
    return FakeItem(
        id=str(uuid.uuid5(DOCBODY_INGEST_NS, f"{PARENT}:{index}")),
        content=text,
        metadata={"chunk_index": index, "chunk_total": total},
    )


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


class TestImage:
    @pytest.mark.asyncio
    async def test_described_returns_the_child_text(self):
        child = image_child("A whiteboard showing the deploy pipeline.")
        provider = FakeProvider({child.id: child})
        res = await resolve_indexed_text(
            provider=provider,
            item=image_parent({"state": "described", "item_id": child.id}),
            team_scope=TEAM,
        )
        assert res.state == STATE_INDEXED
        assert res.kind == "image"
        assert res.text == "A whiteboard showing the deploy pipeline."
        assert res.truncated is False
        assert res.detail == ""

    @pytest.mark.asyncio
    async def test_described_without_item_id_falls_back_to_the_deterministic_id(self):
        # The child id is uuid5(parent) by construction, so an older flag that
        # recorded no item_id is still resolvable rather than reported as absent.
        child = image_child("Recovered by computed id.")
        provider = FakeProvider({child.id: child})
        res = await resolve_indexed_text(
            provider=provider, item=image_parent({"state": "described"}), team_scope=TEAM
        )
        assert res.state == STATE_INDEXED
        assert res.text == "Recovered by computed id."

    @pytest.mark.asyncio
    async def test_long_description_is_clipped_and_says_so(self):
        child = image_child("x" * (PREVIEW_CHARS + 500))
        provider = FakeProvider({child.id: child})
        res = await resolve_indexed_text(
            provider=provider,
            item=image_parent({"state": "described", "item_id": child.id}),
            team_scope=TEAM,
        )
        assert res.state == STATE_INDEXED
        assert len(res.text) <= PREVIEW_CHARS
        assert res.truncated is True
        assert res.detail  # a clipped preview must not pretend to be the whole text

    @pytest.mark.asyncio
    async def test_no_flag_yet_is_pending_not_absent(self):
        res = await resolve_indexed_text(
            provider=FakeProvider(), item=image_parent(None), team_scope=TEAM
        )
        assert res.state == STATE_PENDING
        assert res.text == ""

    @pytest.mark.asyncio
    async def test_no_flag_with_vision_disabled_is_not_indexed(self, monkeypatch):
        # Nothing was ever scheduled, so "still working" would be a lie.
        from app.config import settings

        monkeypatch.setattr(settings, "VISION_DESCRIBE_ENABLED", False)
        res = await resolve_indexed_text(
            provider=FakeProvider(), item=image_parent(None), team_scope=TEAM
        )
        assert res.state == STATE_NOT_INDEXED
        assert "turned off" in res.detail

    @pytest.mark.asyncio
    async def test_skipped_is_not_indexed_with_its_own_sentence(self):
        res = await resolve_indexed_text(
            provider=FakeProvider(),
            item=image_parent({"state": "skipped", "reason": "over_budget"}),
            team_scope=TEAM,
        )
        assert res.state == STATE_NOT_INDEXED
        assert res.detail == "The team's daily image-indexing budget was already used up."

    @pytest.mark.asyncio
    async def test_failed_is_its_own_state(self):
        res = await resolve_indexed_text(
            provider=FakeProvider(),
            item=image_parent({"state": "failed", "reason": "describe_error:APIStatusError"}),
            team_scope=TEAM,
        )
        assert res.state == STATE_FAILED
        assert res.text == ""

    @pytest.mark.asyncio
    async def test_described_but_child_gone_is_not_indexed_not_pending(self):
        # Nothing is going to arrive, so reporting "indexing…" would spin forever.
        res = await resolve_indexed_text(
            provider=FakeProvider(),
            item=image_parent({"state": "described", "item_id": "missing"}),
            team_scope=TEAM,
        )
        assert res.state == STATE_NOT_INDEXED
        assert "no longer stored" in res.detail


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class TestDocument:
    @pytest.mark.asyncio
    async def test_first_chunk_is_returned_with_its_total(self):
        chunk = doc_chunk(0, "Quarterly plan. Hiring freeze lifted.", total=3)
        provider = FakeProvider({chunk.id: chunk})
        res = await resolve_indexed_text(
            provider=provider, item=doc_parent(), team_scope=TEAM
        )
        assert res.state == STATE_INDEXED
        assert res.kind == "document"
        assert res.text == "Quarterly plan. Hiring freeze lifted."
        assert res.chunk_total == 3
        # Three chunks exist and one is shown — the response must not imply it is all.
        assert res.truncated is True
        assert "first of 3" in res.detail

    @pytest.mark.asyncio
    async def test_single_chunk_is_not_reported_as_truncated(self):
        chunk = doc_chunk(0, "One page.", total=1)
        provider = FakeProvider({chunk.id: chunk})
        res = await resolve_indexed_text(
            provider=provider, item=doc_parent(), team_scope=TEAM
        )
        assert res.truncated is False
        assert res.detail == ""

    @pytest.mark.asyncio
    async def test_no_text_layer_is_its_own_sentence(self):
        res = await resolve_indexed_text(
            provider=FakeProvider(), item=doc_parent(no_text_layer=True), team_scope=TEAM
        )
        assert res.state == STATE_NOT_INDEXED
        assert "no text layer" in res.detail

    @pytest.mark.asyncio
    async def test_no_chunks_yet_is_pending(self):
        res = await resolve_indexed_text(
            provider=FakeProvider(), item=doc_parent(), team_scope=TEAM
        )
        assert res.state == STATE_PENDING

    @pytest.mark.asyncio
    async def test_no_chunks_with_extraction_disabled_is_not_indexed(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "DOCBODY_EXTRACTION_ENABLED", False)
        res = await resolve_indexed_text(
            provider=FakeProvider(), item=doc_parent(), team_scope=TEAM
        )
        assert res.state == STATE_NOT_INDEXED
        assert "turned off" in res.detail

    @pytest.mark.asyncio
    async def test_resolution_costs_one_child_read(self):
        chunk = doc_chunk(0, "Body.", total=1)
        provider = FakeProvider({chunk.id: chunk})
        await resolve_indexed_text(provider=provider, item=doc_parent(), team_scope=TEAM)
        assert len(provider.reads) == 1, "a tooltip must not walk every chunk"


# ---------------------------------------------------------------------------
# Cross-cutting: scope and vocabulary
# ---------------------------------------------------------------------------


class TestTeamScope:
    @pytest.mark.asyncio
    async def test_child_read_uses_the_callers_scope(self):
        child = image_child("secret")
        provider = FakeProvider({child.id: child})
        await resolve_indexed_text(
            provider=provider,
            item=image_parent({"state": "described", "item_id": child.id}),
            team_scope=TEAM,
        )
        assert provider.reads == [(child.id, TEAM)]

    @pytest.mark.asyncio
    async def test_a_child_owned_by_another_team_is_never_returned(self):
        # The id is deterministic, so guessing one is trivial; the scope on the
        # read is the whole defence.
        child = image_child("other team's document")
        child.metadata["_team_scope"] = "team-beta"
        provider = FakeProvider({child.id: child})
        res = await resolve_indexed_text(
            provider=provider,
            item=image_parent({"state": "described", "item_id": child.id}),
            team_scope=TEAM,
        )
        assert res.state == STATE_NOT_INDEXED
        assert res.text == ""


class TestDetailVocabulary:
    """The sentence handed to a team is written here, never echoed from storage."""

    RAW_REASONS = [
        "describe_error:APIStatusError",
        "describe_error:AuthenticationError",
        "unsupported_image_mime:image/bmp",
        "image_too_large:raw_bytes:9999999",
        "dimensions_too_large:12000x400",
        "ingest_error:ValueError",
        "some_future_reason:with:detail",
        "",
    ]

    @pytest.mark.parametrize("reason", RAW_REASONS)
    @pytest.mark.parametrize("failed", [True, False])
    def test_no_raw_reason_survives_into_the_sentence(self, reason: str, failed: bool):
        detail = _detail_for_reason(reason, failed=failed)
        assert detail, "every reason must produce SOME sentence — silence is not a state"
        # Nothing after the first token (the class name, the mime, the byte count)
        # may reach a reader, and neither may the code itself.
        for fragment in reason.split(":"):
            if fragment:
                assert fragment not in detail
        assert ":" not in detail

    @pytest.mark.parametrize("reason", RAW_REASONS)
    @pytest.mark.parametrize("failed", [True, False])
    def test_the_sentence_reads_as_a_sentence(self, reason: str, failed: bool):
        detail = _detail_for_reason(reason, failed=failed)
        assert detail[0].isupper() and detail.endswith(".")
        assert "_" not in detail  # snake_case is a machine's vocabulary

    @pytest.mark.asyncio
    async def test_a_failure_response_carries_no_reason_field_at_all(self):
        res = await resolve_indexed_text(
            provider=FakeProvider(),
            item=image_parent({"state": "failed", "reason": "describe_error:APIStatusError"}),
            team_scope=TEAM,
        )
        payload = res.as_dict()
        assert "reason" not in payload
        assert "APIStatusError" not in str(payload)


class TestRouteRegistration:
    """The endpoint has to be mounted, and behind the team-scope gate."""

    def test_route_is_mounted_under_v1(self):
        from app.main import app

        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/v1/media/{item_id}/indexed-text" in paths

    def test_route_depends_on_get_team_scope(self):
        # This dependency IS the blocked-member refusal (deps.get_team_scope raises
        # 403 on team_members.blocked_at, the same rule
        # team_chat._resolve_team_and_check_membership applies). Swapping it for a
        # bare principal check would silently re-admit a blocked member.
        import inspect

        from app.deps import get_team_scope
        from app.routes.media import serve_indexed_text

        params = inspect.signature(serve_indexed_text).parameters
        assert params["team_scope"].default.dependency is get_team_scope

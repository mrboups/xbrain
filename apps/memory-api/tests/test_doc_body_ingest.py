"""Unit tests for doc_body_ingest.extract_and_ingest_body (Phase 24, DOCBODY-01).

These are MODULE-LEVEL unit tests against a FAKE in-memory provider — no real
Postgres / Qdrant / MinIO and no real embedder (the full real-infra keyless
embed->retrieve gate is Plan 24-03, by design). They prove the wiring contract:

  - one linked chunk MemoryItem per chunk, tag INHERITED from the upload
  - the four link fields (media_key / parent_item_id / chunk_index / chunk_total)
  - source == "upload:body"
  - the cross-team invariant: every chunk's team_scope == the upload's (T-24-06)
  - deterministic (idempotent) chunk ids via the fixed uuid5 namespace
  - unknown mime -> skipped, zero upserts
  - no_text_layer doc -> flagged, ZERO chunks (never an empty embed, D-24-03)
  - oversized body -> truncated + bounded chunk count (T-24-08)
  - fail-soft: a provider.upsert that raises NEVER propagates (T-24-07)

Real documents (empty PDF for the no_text_layer path) are generated in-test with
reportlab rather than mocked, mirroring Phase 19 / Plan 24-01 non-mocked discipline.
"""
from __future__ import annotations

import io
import uuid

from app.config import settings
from app.services.doc_body_ingest import (
    DOCBODY_INGEST_NS,
    IngestResult,
    extract_and_ingest_body,
)
from app.services.doc_extract import PDF_MIME
from xbrain_memory.types import MemoryItem, TruthLevel, ValidationStatus, Visibility

TEAM = "team-alpha"
OTHER_TEAM = "team-beta"


class FakeProvider:
    """Captures every upserted item + update() call. No DB, no Qdrant, no embedder.

    `raise_on_index` makes the Nth (0-based) upsert raise, to exercise fail-soft.
    """

    def __init__(self, raise_on_index: int | None = None) -> None:
        self.items: list[MemoryItem] = []
        self.updates: list[tuple[str, str, dict]] = []
        self._raise_on_index = raise_on_index

    async def upsert(self, item: MemoryItem) -> str:
        if self._raise_on_index is not None and len(self.items) == self._raise_on_index:
            raise RuntimeError("simulated upsert failure")
        self.items.append(item)
        return item.id

    async def update(self, item_id: str, *, team_scope: str, patch: dict) -> None:
        self.updates.append((item_id, team_scope, patch))
        return None


# A distinctive body long enough to force multiple chunks at the 1500-char default.
_MULTI_CHUNK_BODY = ("The quarterly budget alpha distinctive phrase omega. " * 100).encode()


def _make_empty_pdf() -> bytes:
    """A blank real PDF — a genuine no-text-layer (scanned-doc analogue)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.showPage()
    c.save()
    return buf.getvalue()


async def _ingest(provider, *, data=_MULTI_CHUNK_BODY, mime="text/plain",
                  filename="notes.txt", media_key="media/team-alpha/x.txt",
                  parent_item_id="P", team_scope=TEAM, project_scope="proj",
                  truth_level="WORKING", visibility="team",
                  validation_status="pending") -> IngestResult:
    return await extract_and_ingest_body(
        provider=provider, data=data, mime=mime, filename=filename,
        media_key=media_key, parent_item_id=parent_item_id, team_scope=team_scope,
        project_scope=project_scope, truth_level=truth_level, visibility=visibility,
        validation_status=validation_status,
    )


# --- happy path: linked, inherited-tag chunk items ---


async def test_body_ingest_creates_one_linked_chunk_item_per_chunk():
    fake = FakeProvider()
    res = await _ingest(fake)

    assert isinstance(res, IngestResult)
    assert res.skipped is False
    assert res.no_text_layer is False
    assert res.chunk_count >= 1
    assert res.chunk_count == len(fake.items)  # one upsert per chunk


async def test_every_chunk_inherits_full_7field_tagging_and_source():
    fake = FakeProvider()
    res = await _ingest(fake)
    n = res.chunk_count

    for item in fake.items:
        assert item.team_scope == TEAM
        assert item.project_scope == "proj"
        assert item.truth_level == TruthLevel.WORKING
        assert item.visibility == Visibility.TEAM
        assert item.validation_status == ValidationStatus.PENDING
        assert item.source == "upload:body"

    # link metadata is exact + indices span 0..N-1 with a constant chunk_total
    for i, item in enumerate(fake.items):
        assert item.metadata == {
            "media_key": "media/team-alpha/x.txt",
            "parent_item_id": "P",
            "filename": "notes.txt",
            "chunk_index": i,
            "chunk_total": n,
        }


async def test_chunk_team_scope_is_never_another_team():
    """T-24-06 cross-team invariant: a body chunk can never land under another team."""
    fake = FakeProvider()
    await _ingest(fake, team_scope=TEAM)
    assert fake.items  # produced chunks
    assert all(item.team_scope == TEAM for item in fake.items)
    assert all(item.team_scope != OTHER_TEAM for item in fake.items)


async def test_chunk_ids_are_deterministic_and_idempotent():
    fake_a = FakeProvider()
    fake_b = FakeProvider()
    await _ingest(fake_a)
    await _ingest(fake_b)

    ids_a = [it.id for it in fake_a.items]
    ids_b = [it.id for it in fake_b.items]
    assert ids_a == ids_b  # a second identical call upserts the SAME ids
    # ids match the documented uuid5(namespace, "<parent>:<index>") scheme
    for i, item in enumerate(fake_a.items):
        assert item.id == str(uuid.uuid5(DOCBODY_INGEST_NS, f"P:{i}"))


# --- guards ---


async def test_unknown_mime_is_skipped_with_no_upsert():
    fake = FakeProvider()
    res = await _ingest(fake, data=b"\x00\x01\x02binary", mime="application/octet-stream",
                        filename="x.bin")
    assert res.skipped is True
    assert res.chunk_count == 0
    assert fake.items == []


async def test_no_text_layer_pdf_flags_and_creates_zero_chunks():
    fake = FakeProvider()
    res = await _ingest(fake, data=_make_empty_pdf(), mime=PDF_MIME, filename="scan.pdf")
    assert res.no_text_layer is True
    assert res.chunk_count == 0
    assert len(fake.items) == 0  # never an empty-body chunk


async def test_oversized_body_truncated_and_chunk_count_bounded(monkeypatch):
    # Force a tiny char cap + tiny chunk cap so a big body is truncated + bounded.
    monkeypatch.setattr(settings, "DOCBODY_MAX_TOTAL_CHARS", 100)
    monkeypatch.setattr(settings, "DOCBODY_MAX_CHUNKS", 3)
    fake = FakeProvider()
    res = await _ingest(fake, data=("z" * 50_000).encode())
    assert res.truncated is True
    assert res.chunk_count <= 3
    assert len(fake.items) == res.chunk_count


# --- fail-soft ---


async def test_upsert_failure_is_fail_soft_and_never_raises():
    # 2nd upsert (index 1) raises — extract_and_ingest_body must NOT propagate.
    fake = FakeProvider(raise_on_index=1)
    res = await _ingest(fake)  # must return normally, not raise
    assert isinstance(res, IngestResult)
    assert res.chunk_count == 1  # only the first chunk landed before the failure
    assert len(fake.items) == 1
    assert res.reason  # a machine-readable failure reason is recorded

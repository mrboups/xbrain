"""Extract an uploaded document's body and embed each chunk as a linked memory_item.

This is the SETTINGS-AWARE orchestration layer between the pure extraction core
(`doc_extract.py`, Plan 24-01) and the live upload path (`media.py`, wired in Plan 24-02).
`media.py` today embeds only `caption or filename`; this service closes DOCBODY-01's
delivery half by extracting the body, chunking it, and upserting one memory_item per
chunk — each INHERITING the upload's full 7-field tagging and back-linked to the MinIO
object.

Contract highlights:
  - It NEVER raises (D-24-01 fail-soft). The upload's 201 response, the MinIO object,
    and the parent card item are already committed before this runs (scheduled
    fire-and-forget in media.py); a slow or failing extraction must be invisible to
    the uploader. The whole body is wrapped in `try/except Exception`.
  - Every chunk's `team_scope` is INHERITED verbatim from the upload (T-24-06): never
    derived, never defaulted. A mis-scoped write is the only cross-team leak vector, so
    it is closed at construction — the caller passes the get_team_scope-enforced value.
  - Caps are read HERE from `settings` and passed as explicit args into the pure
    doc_extract functions, which import no config (keeps doc_extract unit-testable).
  - Chunk ids are deterministic — uuid5(DOCBODY_INGEST_NS, "<parent>:<index>") — so a
    retry re-upserts the SAME ids (idempotent), mirroring brain_ingest's BRAIN_INGEST_NS.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from xbrain_memory.types import MemoryItem

from app.config import settings
from app.services.doc_extract import chunk_text, extract_document

log = structlog.get_logger(__name__)

# Fixed UUID5 namespace for deterministic body-chunk ids. NEVER change it — changing
# this would break idempotency (a re-ingest of the same document would mint NEW ids
# instead of overwriting the existing chunks). Distinct from brain_ingest.BRAIN_INGEST_NS.
DOCBODY_INGEST_NS = uuid.UUID("1a7974d3-5cdb-492e-b706-0f67ed657832")


@dataclass
class IngestResult:
    """Outcome of a body extract+ingest attempt (returned, never raised).

    chunk_count    number of chunk memory_items actually upserted (partial on failure)
    no_text_layer  a supported surface (pdf/docx) yielded no text — parent gets flagged
    skipped        nothing to embed — unknown mime, over-size, or a caught parse error
    truncated      body exceeded the char cap and was truncated before chunking
    reason         machine-readable why for skipped / flagged / failed outcomes
    """

    chunk_count: int
    no_text_layer: bool
    skipped: bool
    truncated: bool
    reason: str = ""


async def extract_and_ingest_body(
    *,
    provider,
    data: bytes,
    mime: str,
    filename: str | None,
    media_key: str,
    parent_item_id: str,
    team_scope: str,
    project_scope: str | None,
    truth_level: str,
    visibility: str = "team",
    validation_status: str = "pending",
    confidence: float = 1.0,
) -> IngestResult:
    """Extract `data`'s body, chunk it, and upsert one linked memory_item per chunk.

    NEVER raises (D-24-01). Returns an IngestResult describing the outcome; on any
    failure it logs a warning and returns a partial/failed result so the caller (a
    detached task) can decide what to record — the upload itself is already committed.
    """
    upserted = 0
    try:
        # BL-01: pypdf / python-docx parsing is synchronous CPU-bound work. This runs
        # from a detached task, but the API is deployed at UVICORN_WORKERS=1 in the
        # OSS-light target, so a large/crafted document parsed inline on the event loop
        # would freeze EVERY concurrent request. Offload to a worker thread (the same
        # asyncio.to_thread convention embedders.py uses) so the loop stays responsive.
        res = await asyncio.to_thread(
            extract_document,
            data,
            mime,
            filename,
            max_file_bytes=settings.DOCBODY_MAX_FILE_BYTES,
            max_total_chars=settings.DOCBODY_MAX_TOTAL_CHARS,
            min_chars=settings.DOCBODY_MIN_CHARS,
        )

        # Unknown mime / over-size / caught parse error — nothing to embed.
        if res.skipped:
            return IngestResult(
                chunk_count=0, no_text_layer=res.no_text_layer, skipped=True,
                truncated=res.truncated, reason=res.reason,
            )

        # Scanned / image-only supported surface — flag the parent (caller), NO chunks.
        if res.no_text_layer:
            return IngestResult(
                chunk_count=0, no_text_layer=True, skipped=False,
                truncated=res.truncated, reason=res.reason,
            )

        chunks = chunk_text(
            res.text,
            chunk_size=settings.DOCBODY_CHUNK_SIZE,
            overlap=settings.DOCBODY_CHUNK_OVERLAP,
            max_chunks=settings.DOCBODY_MAX_CHUNKS,
        )
        total = len(chunks)
        now = datetime.now(UTC)

        for i, chunk in enumerate(chunks):
            item = MemoryItem(
                id=str(uuid.uuid5(DOCBODY_INGEST_NS, f"{parent_item_id}:{i}")),
                team_scope=team_scope,        # INHERITED — the T-24-06 security invariant
                project_scope=project_scope,
                content=chunk,
                metadata={
                    "media_key": media_key,
                    "parent_item_id": parent_item_id,
                    "filename": filename,
                    "chunk_index": i,
                    "chunk_total": total,
                },
                source="upload:body",
                truth_level=truth_level,          # type: ignore[arg-type]  # pydantic coerces str->enum
                visibility=visibility,            # type: ignore[arg-type]
                validation_status=validation_status,  # type: ignore[arg-type]
                confidence=confidence,
                created_at=now,
                updated_at=now,
            )
            await provider.upsert(item)
            upserted += 1

        log.info(
            "doc_body_ingest.ok",
            parent_item_id=parent_item_id,
            team_scope=team_scope,
            chunk_count=upserted,
            truncated=res.truncated,
        )
        return IngestResult(
            chunk_count=upserted, no_text_layer=False, skipped=False,
            truncated=res.truncated,
        )
    except Exception as exc:  # broad on purpose — body ingest must NEVER break the upload
        log.warning(
            "doc_body_ingest.failed",
            parent_item_id=parent_item_id,
            filename=filename,
            upserted=upserted,
            error=str(exc),
            exc_info=True,  # LOW: keep the stack trace for post-mortem, not just the message
        )
        return IngestResult(
            chunk_count=upserted, no_text_layer=False, skipped=False,
            truncated=False, reason=f"ingest_error:{type(exc).__name__}",
        )

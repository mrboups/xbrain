"""Resolve what text an uploaded attachment actually contributed to the brain.

An upload's own memory_item carries only the caption or the filename. The text that
makes it findable lives in CHILD items written after the 201, by two different paths:

  * images    — `image_describe.py` upserts ONE description child and records the
                outcome on the parent as `metadata.image_description`
                ({state, model, reason?, item_id?}).
  * documents — `doc_body_ingest.py` upserts N chunk children with deterministic
                ids `uuid5(DOCBODY_INGEST_NS, "<parent>:<index>")`, and the route
                flags `metadata.no_text_layer` when there was nothing to extract.

Neither shape is reachable from the message payload — the work finishes after the
message exists — so a surface that wants to show a person what was indexed has to
ask. This module is the ONE answer to that question, so the client makes one call
for either kind rather than stitching parent → child itself.

Two rules hold everything here together:

  1. **Silence is never an outcome.** Every path returns a named state —
     ``indexed`` / ``pending`` / ``not_indexed`` / ``failed`` — so "still working",
     "deliberately skipped" and "broke" can never render as the same empty box.
  2. **The `detail` sentence is OUR vocabulary, never a dependency's.** The stored
     reasons carry internal strings (`describe_error:APIStatusError`,
     `unsupported_image_mime:image/bmp`) that name SDK classes and internal
     mechanics. Those belong in the server log; a chat surface gets a sentence
     written here. `_detail_for_reason` is the whole allow-list, and its fallback
     is generic on purpose — an unrecognised reason must degrade to a vague
     sentence, never to the raw string.

team_scope is the caller's, threaded into BOTH the parent read and the child read:
a child is fetched by a computed id, so its own scope check is what stops a guessed
uuid5 from crossing teams.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.config import settings
from app.services.doc_body_ingest import DOCBODY_INGEST_NS
from app.services.image_describe import FLAG_KEY as IMAGE_FLAG_KEY
from app.services.image_describe import IMAGE_DESCRIBE_NS, is_image_mime

log = structlog.get_logger(__name__)

# How much text a caller gets back. This feeds a hover tooltip, not a reader: a
# 200-chunk document dumped into a floating box is unreadable and costs a payload
# per hover. The first chunk of a document is DOCBODY_CHUNK_SIZE (1500) chars, so
# this shows essentially all of it while capping a pathological single chunk.
PREVIEW_CHARS = 1200

STATE_INDEXED = "indexed"
STATE_PENDING = "pending"
STATE_NOT_INDEXED = "not_indexed"
STATE_FAILED = "failed"


@dataclass
class IndexedText:
    """What the brain holds for one uploaded attachment.

    state        one of the four STATE_* constants — always set
    kind         "image" | "document" | None (unknown mime)
    text         the indexed text; "" for every state except ``indexed``
    truncated    True when `text` is a prefix of something longer
    chunk_total  document chunk count when known, else None
    detail       a sentence safe to render to a whole team; "" when the text speaks
    """

    state: str
    kind: str | None = None
    text: str = ""
    truncated: bool = False
    chunk_total: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "kind": self.kind,
            "text": self.text,
            "truncated": self.truncated,
            "chunk_total": self.chunk_total,
            "detail": self.detail,
        }


def _clip(text: str) -> tuple[str, bool]:
    """Cut `text` to PREVIEW_CHARS, reporting whether anything was dropped."""
    cleaned = (text or "").strip()
    if len(cleaned) <= PREVIEW_CHARS:
        return cleaned, False
    return cleaned[:PREVIEW_CHARS].rstrip(), True


def _detail_for_reason(reason: str, *, failed: bool) -> str:
    """Translate a stored machine reason into a sentence a whole team may read.

    The allow-list is exhaustive by intent, and the fallback is deliberately vague:
    a reason this function does not recognise is, by definition, one nobody wrote a
    safe sentence for, and echoing it would be the same mistake as forwarding a
    provider's error text into a chat. The raw reason stays in the server log.
    """
    code = (reason or "").split(":", 1)[0].strip()
    mapping = {
        "disabled": "Attachment indexing is turned off on this server.",
        "vision_unavailable": "No image-indexing model is configured on this server.",
        "over_budget": "The team's daily image-indexing budget was already used up.",
        "unsupported_image_mime": "This image format cannot be indexed.",
        "image_too_large": "This image is too large to index.",
        "dimensions_too_large": "This image is too large to index.",
        "empty_description": "Indexing produced no text.",
    }
    if code in mapping:
        return mapping[code]
    if failed:
        return "Indexing failed. It may work if the file is uploaded again."
    return "This attachment was not indexed."


async def resolve_indexed_text(
    *,
    provider: Any,
    item: Any,
    team_scope: str,
) -> IndexedText:
    """Resolve the indexed text for an already-fetched media parent item.

    `item` is the PARENT (the upload's own memory_item); `provider.get` is used at
    most once more, for the single child that holds the text. Never raises for a
    missing child — an absent child is a state, not an error.
    """
    metadata = dict(item.metadata or {})
    media = metadata.get("media") or {}
    mime = media.get("mime") or ""

    if is_image_mime(mime):
        return await _resolve_image(
            provider=provider,
            parent_item_id=str(item.id),
            metadata=metadata,
            team_scope=team_scope,
        )
    return await _resolve_document(
        provider=provider,
        parent_item_id=str(item.id),
        metadata=metadata,
        team_scope=team_scope,
    )


async def _resolve_image(
    *,
    provider: Any,
    parent_item_id: str,
    metadata: dict[str, Any],
    team_scope: str,
) -> IndexedText:
    flag = metadata.get(IMAGE_FLAG_KEY)

    if not isinstance(flag, dict):
        # No outcome recorded. Either the detached describe task has not finished
        # yet, or the whole path is switched off and no task was ever scheduled —
        # and those are genuinely different answers, so they are not merged.
        if not settings.VISION_DESCRIBE_ENABLED:
            return IndexedText(
                state=STATE_NOT_INDEXED,
                kind="image",
                detail="Image indexing is turned off on this server.",
            )
        return IndexedText(state=STATE_PENDING, kind="image")

    state = str(flag.get("state") or "")
    reason = str(flag.get("reason") or "")

    if state == "described":
        child_id = flag.get("item_id") or str(
            uuid.uuid5(IMAGE_DESCRIBE_NS, parent_item_id)
        )
        child = await provider.get(str(child_id), team_scope=team_scope)
        if child is None or not (child.content or "").strip():
            # The parent says a description landed and it is not there any more —
            # deleted, or wiped. Reported as absent rather than as still-running,
            # because nothing is going to arrive.
            return IndexedText(
                state=STATE_NOT_INDEXED,
                kind="image",
                detail="The indexed text is no longer stored.",
            )
        text, truncated = _clip(child.content)
        return IndexedText(
            state=STATE_INDEXED,
            kind="image",
            text=text,
            truncated=truncated,
            detail="Showing the first part of a longer description." if truncated else "",
        )

    if state == "failed":
        log.info(
            "indexed_text.image_failed",
            parent_item_id=parent_item_id,
            reason=reason,  # the raw reason stays HERE, never in the response
        )
        return IndexedText(
            state=STATE_FAILED,
            kind="image",
            detail=_detail_for_reason(reason, failed=True),
        )

    if state == "skipped":
        return IndexedText(
            state=STATE_NOT_INDEXED,
            kind="image",
            detail=_detail_for_reason(reason, failed=False),
        )

    # An unknown state from a future/older writer. Neither claimed nor denied.
    log.info(
        "indexed_text.unknown_image_state",
        parent_item_id=parent_item_id,
        state=state,
    )
    return IndexedText(state=STATE_PENDING, kind="image")


async def _resolve_document(
    *,
    provider: Any,
    parent_item_id: str,
    metadata: dict[str, Any],
    team_scope: str,
) -> IndexedText:
    if metadata.get("no_text_layer") is True:
        return IndexedText(
            state=STATE_NOT_INDEXED,
            kind="document",
            detail="This document has no text layer, so there was nothing to index.",
        )

    # Chunk ids are deterministic, so the first chunk is addressable without a
    # search: one get, scoped to the caller's team, and its own metadata reports
    # how many siblings it has.
    first_id = str(uuid.uuid5(DOCBODY_INGEST_NS, f"{parent_item_id}:0"))
    chunk = await provider.get(first_id, team_scope=team_scope)

    if chunk is None or not (chunk.content or "").strip():
        if not settings.DOCBODY_EXTRACTION_ENABLED:
            return IndexedText(
                state=STATE_NOT_INDEXED,
                kind="document",
                detail="Document indexing is turned off on this server.",
            )
        return IndexedText(state=STATE_PENDING, kind="document")

    chunk_meta = dict(chunk.metadata or {})
    raw_total = chunk_meta.get("chunk_total")
    chunk_total = raw_total if isinstance(raw_total, int) and raw_total > 0 else None

    text, clipped = _clip(chunk.content)
    more_chunks = bool(chunk_total and chunk_total > 1)
    truncated = clipped or more_chunks

    detail = ""
    if truncated:
        detail = (
            f"Showing the first of {chunk_total} indexed parts."
            if more_chunks
            else "Showing the first part of a longer extract."
        )

    return IndexedText(
        state=STATE_INDEXED,
        kind="document",
        text=text,
        truncated=truncated,
        chunk_total=chunk_total,
        detail=detail,
    )

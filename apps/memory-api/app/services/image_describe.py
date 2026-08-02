"""Describe an uploaded IMAGE with a vision model and make the description recallable.

`media.py` sets an uploaded item's content to `caption or filename or "media"`. Phase 24
closed that gap for DOCUMENTS — `doc_body_ingest.py` extracts the body and embeds it as
linked chunks — but `doc_extract.py` handles pdf/docx/markdown/text only. So a screenshot
of an architecture diagram lands in the brain as the string "screenshot.png" and its
contents are invisible to every recall path. This module closes the image half.

Shape, deliberately mirroring `doc_body_ingest.py`:

  - It NEVER raises. It is scheduled fire-and-forget from `media.py` AFTER the object and
    the parent card item are committed, so a slow or failing model call can never delay or
    fail the upload's 201. The whole body is wrapped in `try/except Exception`.
  - The description becomes a LINKED CHILD memory_item, not a rewrite of the parent's
    content. The caption a person wrote and a machine's guess at what the image shows are
    different things and both are worth having; overwriting the parent would destroy the
    human one.
  - Every tag on the child is INHERITED from the upload (team_scope above all — a
    mis-scoped write is the only cross-team leak vector), except the three that must NOT
    be inherited: see `_description_truth_level` and DESCRIPTION_CONFIDENCE below.
  - The child's id is deterministic — uuid5(IMAGE_DESCRIBE_NS, parent_item_id) — so a
    retry re-upserts the SAME id instead of accumulating duplicate descriptions.

Every exit path flags the PARENT (`metadata.image_description`) with a visible state:
described / skipped / failed, plus a machine-readable reason. That is the point of the
flag — a missing description that looks identical to a described one is the failure mode
here, so silence is never an outcome. Owning the flag in this module (rather than in the
route, as the document path does) is deliberate: the outcome states are the core of this
feature rather than one `no_text_layer` bit, and keeping the write here means no exit path
can forget it.

Cost and egress are both gated. `settings.VISION_DESCRIBE_ENABLED` is a real kill-switch —
this path sends user images to a third-party API, and a self-hoster may refuse that. The
per-team daily token bucket is `relevance_filter`'s mechanism with its OWN cap, so a team
dragging 500 screenshots into a chat cannot silently spend the relevance filter's day;
when it is exhausted the description is SKIPPED and recorded as skipped, never retried.
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from xbrain_memory.types import MemoryItem, TruthLevel

from app.config import settings
from app.services.relevance_filter import check_token_budget, record_token_usage

log = structlog.get_logger(__name__)

# Fixed UUID5 namespace for the deterministic description-child id. NEVER change it —
# changing it would break idempotency (a re-describe would mint a NEW id instead of
# overwriting the existing description). Distinct from DOCBODY_INGEST_NS / BRAIN_INGEST_NS.
IMAGE_DESCRIBE_NS = uuid.UUID("ea842cc0-9679-4fb3-b192-52be4cc24d8a")

# The mimes the Anthropic Messages API actually accepts for an image block. This is not a
# guess: it is the literal from the SDK's `Base64ImageSourceParam.media_type`. An image in
# any other format (bmp, svg, tiff, heic) is skipped with a recorded reason rather than
# sent to be rejected.
VISION_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

# Namespaces this path's daily token accounting inside relevance_filter's shared store.
BUDGET_BUCKET = "vision"

# Rough per-call input estimate used for the PRE-call budget check (a mid-size image is
# ~1.1k tokens once tiled, plus the prompt). The real figure replaces it after the call
# via record_token_usage, so this only has to be honest enough to stop a flood.
ESTIMATED_TOKENS_PER_CALL = 1_600

# A machine's description of an image is inference, not testimony, and confidence is part
# of the tagging contract. The parent keeps 1.0 (a person really did upload that file);
# its description does not inherit that certainty.
DESCRIPTION_CONFIDENCE = 0.6

# The parent metadata key carrying the auditable outcome (see module docstring).
FLAG_KEY = "image_description"

DESCRIBE_PROMPT = (
    "Describe this image so that a teammate searching a shared memory six months from now "
    "can find it and know what it contains.\n\n"
    "Transcribe every text string you can read, verbatim — labels, headings, code, error "
    "messages, axis names, values. If it is a diagram, chart, screenshot or document, state "
    "its structure and the relationships it shows (what connects to what, what the trend is). "
    "Name the concrete entities present.\n\n"
    "Write plain prose. No preamble, no markdown, no bullet list, no speculation about why "
    "the image was made. Describe only what is actually visible. If the image is unreadable "
    "or empty, say exactly that."
)


@dataclass
class DescribeResult:
    """Outcome of a description attempt (returned, never raised).

    state          "described" | "skipped" | "failed" — mirrors the parent flag
    reason         machine-readable why, for skipped/failed (e.g. "over_budget")
    description    the stored description ("" unless state == "described")
    child_item_id  id of the linked description item (None unless described)
    model          the model that produced it ("" when nothing was called)
    """

    state: str
    reason: str = ""
    description: str = ""
    child_item_id: str | None = None
    model: str = ""


# ---------------------------------------------------------------------------
# Dimension probe — pure header parsing, no image library
# ---------------------------------------------------------------------------
#
# Anthropic rejects images above ~8000 px on an edge. Checking that BEFORE the call needs
# the pixel dimensions, which live in the first few dozen bytes of every format we accept.
# Reading them by hand keeps a compiled dependency (Pillow) off a 4 GB VM that already
# builds for two architectures, and costs a handful of byte offsets.


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    return (
        int.from_bytes(data[6:8], "little"),
        int.from_bytes(data[8:10], "little"),
    )


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Walk the JPEG segment chain to the SOFn frame header carrying the dimensions."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # Standalone markers carry no length field: padding (FF), SOI/EOI, RSTn, TEM.
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
        if seg_len < 2:
            return None
        # SOF0..SOF15 hold the frame dimensions — except DHT (C4), JPG (C8) and DAC (CC),
        # which share the range but are different segments.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            return (width, height)
        i += 2 + seg_len
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":  # extended: 24-bit little-endian (width-1, height-1)
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8L":  # lossless: 0x2F signature, then 14 bits each, LE bitstream
        if data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8 ":  # lossy: 3-byte frame tag, 3-byte start code, then 14 bits each
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    return None


def probe_image_size(data: bytes, mime: str) -> tuple[int, int] | None:
    """Return (width, height) in px from the file header, or None if unreadable.

    Pure and total: hostile or truncated bytes yield None, never an exception — this runs
    on the upload path and a crafted header must not become a traceback.
    """
    try:
        if mime == "image/png":
            return _png_size(data)
        if mime == "image/gif":
            return _gif_size(data)
        if mime == "image/jpeg":
            return _jpeg_size(data)
        if mime == "image/webp":
            return _webp_size(data)
    except Exception:  # a malformed header is "unknown", never a crash
        return None
    return None


def is_image_mime(mime: str | None) -> bool:
    """True for any real image mime — the route-level gate.

    Deliberately broader than VISION_MIMES: an `image/bmp` upload SHOULD reach this
    service so it is flagged "skipped (unsupported_image_mime)". A PDF should not — the
    document path owns that, and an "image description skipped" flag on it would be noise.
    """
    return bool(mime) and mime.split(";", 1)[0].strip().lower().startswith("image/")


# ---------------------------------------------------------------------------
# Client + tagging
# ---------------------------------------------------------------------------

_vision_client: Any | None = None


def _get_client() -> Any | None:
    """Lazy-init the Anthropic async client. None if disabled, keyless, or SDK absent."""
    global _vision_client
    if _vision_client is not None:
        return _vision_client
    if not settings.ANTHROPIC_API_KEY or not settings.VISION_DESCRIBE_ENABLED:
        return None
    try:
        from anthropic import AsyncAnthropic  # lazy — matches the relevance_filter convention
    except ImportError:
        log.warning("image_describe.anthropic_not_installed")
        return None
    _vision_client = AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.VISION_DESCRIBE_TIMEOUT_S,
    )
    return _vision_client


def _description_truth_level(parent_truth_level: str) -> TruthLevel:
    """The description's truth_level: never above WORKING, never above its parent.

    A vision model's account of an image is INFERENCE, not testimony — durable enough to
    keep, but nobody has reviewed it, which is precisely WORKING. VALIDATED would assert a
    review that never happened, and the promotion path exists for that.

    Capping at the parent matters too: a person may upload a screenshot tagged CANONICAL,
    and a machine's guess at its contents must not inherit that standing. The cap runs both
    ways — an EPHEMERAL upload's description stays EPHEMERAL rather than being promoted by
    a default, since a description cannot outlive the thing it describes.
    """
    try:
        parent = TruthLevel(parent_truth_level)
    except ValueError:  # unknown value from a form field — fall back to the ceiling
        return TruthLevel.WORKING
    return min(parent, TruthLevel.WORKING)


async def _flag_parent(
    provider: Any,
    *,
    parent_item_id: str,
    team_scope: str,
    parent_metadata: dict[str, Any],
    flag: dict[str, Any],
) -> None:
    """Write the outcome onto the parent's metadata, off a FRESH read.

    `provider.update()` REPLACES metadata wholesale, so this patches off a fresh
    `provider.get` — NOT the closure-captured snapshot taken before the model call started.
    A concurrent PATCH /v1/memory/{id} landing during the seconds-long vision call would
    otherwise be silently clobbered. The snapshot is the fallback only if the item vanished
    (deleted mid-flight). This is the same lesson `_run_body_ingest` carries; a review found
    it once already there.
    """
    fresh = await provider.get(parent_item_id, team_scope=team_scope)
    base_meta = dict(fresh.metadata) if fresh and fresh.metadata else dict(parent_metadata)
    await provider.update(
        parent_item_id,
        team_scope=team_scope,
        patch={"metadata": {**base_meta, FLAG_KEY: flag}},
    )


# ---------------------------------------------------------------------------
# The model call
# ---------------------------------------------------------------------------


class _TooLarge(Exception):
    """Raised internally when the encoded payload exceeds the provider's cap."""


async def _request_description(data: bytes, mime: str, *, team_scope: str) -> tuple[str, int]:
    """Call the vision model and return (description, input_tokens). May raise — caller guards."""
    client = _get_client()
    if client is None:  # pragma: no cover — callers check first; belt and braces
        raise RuntimeError("vision client unavailable")

    # BL-01 lesson: base64-encoding a multi-MB image is CPU-bound work. This app is deployed
    # at UVICORN_WORKERS=1 in the OSS-light target, and it froze its entire API once by doing
    # synchronous parsing inline — the same shape of mistake. Encode on a worker thread.
    encoded = await asyncio.to_thread(lambda: base64.b64encode(data).decode("ascii"))

    # The provider's size limit applies to the payload that actually crosses the wire, and
    # base64 inflates by ~33%. Checked here, before the request is built, so an oversized
    # image is skipped rather than becoming a guaranteed-rejected call.
    if len(encoded) > settings.VISION_MAX_IMAGE_BYTES:
        raise _TooLarge(f"encoded_bytes:{len(encoded)}")

    msg = await client.messages.create(
        model=settings.VISION_DESCRIBE_MODEL,
        max_tokens=settings.VISION_DESCRIBE_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": encoded},
                    },
                    {"type": "text", "text": DESCRIBE_PROMPT},
                ],
            }
        ],
    )

    parts = [getattr(b, "text", "") for b in (msg.content or [])]
    description = "".join(parts).strip()

    usage = getattr(msg, "usage", None)
    tokens_in = getattr(usage, "input_tokens", ESTIMATED_TOKENS_PER_CALL) if usage else (
        ESTIMATED_TOKENS_PER_CALL
    )
    record_token_usage(team_scope, tokens_in, bucket=BUDGET_BUCKET)
    return description, tokens_in


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def describe_and_ingest_image(
    *,
    provider: Any,
    data: bytes,
    mime: str,
    filename: str | None,
    media_key: str,
    parent_item_id: str,
    team_scope: str,
    project_scope: str | None,
    truth_level: str,
    visibility: str = "team",
    parent_metadata: dict[str, Any] | None = None,
) -> DescribeResult:
    """Describe `data`, upsert the description as a linked child item, flag the parent.

    NEVER raises. Returns a DescribeResult describing the outcome; the parent is flagged on
    every path so "described", "skipped (over_budget)", "skipped (unsupported_image_mime)"
    and "failed" are all visible states rather than silence.
    """
    parent_metadata = parent_metadata or {}
    model = settings.VISION_DESCRIBE_MODEL
    result: DescribeResult

    try:
        result = await _describe(
            provider=provider,
            data=data,
            mime=mime,
            filename=filename,
            media_key=media_key,
            parent_item_id=parent_item_id,
            team_scope=team_scope,
            project_scope=project_scope,
            truth_level=truth_level,
            visibility=visibility,
        )
    except _TooLarge as exc:
        result = DescribeResult(state="skipped", reason=f"image_too_large:{exc}", model=model)
    except Exception as exc:  # broad on purpose: never break the upload
        log.warning(
            "image_describe.failed",
            parent_item_id=parent_item_id,
            filename=filename,
            error=str(exc),
            exc_info=True,  # keep the stack trace for a detached-task post-mortem
        )
        result = DescribeResult(
            state="failed", reason=f"describe_error:{type(exc).__name__}", model=model
        )

    # Flag the parent on EVERY path — including the failure paths above. Guarded
    # separately so a flag write that fails cannot discard a description that landed.
    try:
        flag: dict[str, Any] = {"state": result.state, "model": result.model or model}
        if result.reason:
            flag["reason"] = result.reason
        if result.child_item_id:
            flag["item_id"] = result.child_item_id
        await _flag_parent(
            provider,
            parent_item_id=parent_item_id,
            team_scope=team_scope,
            parent_metadata=parent_metadata,
            flag=flag,
        )
    except Exception as exc:  # a failed flag write must not discard a landed description
        log.warning(
            "image_describe.flag_failed",
            parent_item_id=parent_item_id,
            state=result.state,
            error=str(exc),
            exc_info=True,
        )

    log.info(
        "image_describe.done",
        parent_item_id=parent_item_id,
        state=result.state,
        reason=result.reason,
        child_item_id=result.child_item_id,
        team_scope=team_scope,
    )
    return result


async def _describe(
    *,
    provider: Any,
    data: bytes,
    mime: str,
    filename: str | None,
    media_key: str,
    parent_item_id: str,
    team_scope: str,
    project_scope: str | None,
    truth_level: str,
    visibility: str,
) -> DescribeResult:
    """The guarded path. May raise — describe_and_ingest_image() catches and flags."""
    model = settings.VISION_DESCRIBE_MODEL

    if not settings.VISION_DESCRIBE_ENABLED:
        return DescribeResult(state="skipped", reason="disabled", model=model)

    # Only mimes the API actually accepts. image/bmp, image/svg+xml, image/heic and
    # friends are real images but would be rejected, so they never leave the box.
    normalized = (mime or "").split(";", 1)[0].strip().lower()
    if normalized not in VISION_MIMES:
        return DescribeResult(
            state="skipped", reason=f"unsupported_image_mime:{normalized or 'unknown'}", model=model
        )

    # Cheap size pre-check on the RAW bytes: base64 only grows, so anything already over
    # the cap is hopeless and must not even be encoded. (The encoded payload is checked
    # again in _request_description — that is the figure the provider measures.)
    if len(data) > settings.VISION_MAX_IMAGE_BYTES:
        return DescribeResult(
            state="skipped", reason=f"image_too_large:raw_bytes:{len(data)}", model=model
        )

    # Dimension check before the call, from the file header.
    size = probe_image_size(data, normalized)
    if size is not None:
        width, height = size
        limit = settings.VISION_MAX_IMAGE_DIMENSION
        if width > limit or height > limit:
            return DescribeResult(
                state="skipped", reason=f"dimensions_too_large:{width}x{height}", model=model
            )
    # size is None => an unreadable header. Proceeding is deliberate: the byte cap above
    # already stops the case that motivates this guard, and failing closed here would
    # refuse valid images whose header this probe simply does not recognise.

    if _get_client() is None:
        # No API key, SDK not installed, or the kill-switch is off. Recorded, not silent.
        return DescribeResult(state="skipped", reason="vision_unavailable", model=model)

    # Budget BEFORE the call. Exhausted => skipped and recorded as skipped; the caller is a
    # detached one-shot task, so there is no retry loop to fall into.
    if not check_token_budget(
        team_scope,
        ESTIMATED_TOKENS_PER_CALL,
        cap=settings.VISION_DAILY_TOKEN_CAP_PER_TEAM,
        bucket=BUDGET_BUCKET,
    ):
        log.info("image_describe.budget_exceeded", team_scope=team_scope)
        return DescribeResult(state="skipped", reason="over_budget", model=model)

    description, _tokens = await _request_description(data, normalized, team_scope=team_scope)

    if not description:
        # Never embed an empty vector — the same discipline the no_text_layer path keeps.
        return DescribeResult(state="failed", reason="empty_description", model=model)

    now = datetime.now(UTC)
    child_id = str(uuid.uuid5(IMAGE_DESCRIBE_NS, parent_item_id))
    item = MemoryItem(
        id=child_id,
        team_scope=team_scope,          # INHERITED — the cross-team invariant
        project_scope=project_scope,    # INHERITED
        content=description,
        metadata={
            "media_key": media_key,
            "parent_item_id": parent_item_id,
            "filename": filename,
            "kind": "image_description",
            "mime": normalized,
            "model": model,
        },
        # Names the model, so six months from now a human caption is distinguishable from a
        # machine guess without archaeology. `source` is capped at 128 chars by the schema.
        source=f"vision:{model}"[:128],
        truth_level=_description_truth_level(truth_level),
        visibility=visibility,                # type: ignore[arg-type]  # pydantic coerces str->enum
        validation_status="pending",          # type: ignore[arg-type]  # inference is always unreviewed
        confidence=DESCRIPTION_CONFIDENCE,
        created_at=now,
        updated_at=now,
    )
    await provider.upsert(item)

    return DescribeResult(
        state="described", description=description, child_item_id=child_id, model=model
    )

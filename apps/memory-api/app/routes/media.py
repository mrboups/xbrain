"""Media upload and raw-serve endpoints (BL-003 slice 1).

POST /v1/media/upload — multipart upload to MinIO, creates a media memory_item.
GET  /v1/media/{item_id}/raw — streams the object back to an authenticated caller.
GET  /v1/media/{item_id}/indexed-text — what this attachment contributed to the brain.

Design notes (from BL-003-media-design.md):
- MinIO is internal-only (not exposed via nginx), so this proxy endpoint is the
  only way for authed clients to read objects.  A signed-token variant for bare
  <img src> use is deferred to slice 2 (first render surface).
- A media item is an ordinary memory_item; metadata.media carries the blob
  metadata: {key, mime, size, filename}.  No DB schema change is required.
- Max upload size: 25 MB (acceptable to buffer in RAM given the cap).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from xbrain_memory import MemoryItem, MemoryProvider

from app.config import settings
from app.db.minio import get_minio_client
from app.deps import (
    get_current_principal,
    get_memory_provider,
    get_team_scope,
)
from app.routes.media_helpers import (
    _MAX_UPLOAD_BYTES,
    derive_key_and_mime,
    mint_media_token,
    verify_media_token,
)
from app.services import background
from app.services.doc_body_ingest import extract_and_ingest_body
from app.services.image_describe import describe_and_ingest_image, is_image_mime
from app.services.indexed_text import resolve_indexed_text

log = structlog.get_logger(__name__)

router = APIRouter()


async def _run_image_describe(**kw: Any) -> None:
    """Detached image-description task.

    Scheduled fire-and-forget from upload_media AFTER the parent card item is committed,
    for the same reason as _run_body_ingest below: a vision call takes seconds and may
    fail, and neither may delay or fail the upload's 201.

    Thin on purpose — describe_and_ingest_image already never raises, upserts the linked
    description item, and flags the parent off a FRESH read on every exit path (including
    its own failures). This wrapper exists only so a truly unexpected error (task
    cancellation, an OOM inside the provider) still cannot surface as an unhandled
    exception in a detached task.
    """
    try:
        await describe_and_ingest_image(**kw)
    except Exception as exc:  # detached task — never surface an unhandled exception
        log.warning(
            "media.image_describe_failed",
            parent_item_id=kw.get("parent_item_id"),
            error=str(exc),
            exc_info=True,
        )


async def _run_body_ingest(*, provider: MemoryProvider, parent_metadata: dict[str, Any], **kw: Any) -> None:
    """Detached body-ingest task (Phase 24, DOCBODY-01).

    Scheduled fire-and-forget from upload_media AFTER the parent card item is
    committed, so a slow / failing extraction can never affect the 201 response
    (D-24-01). Extracts the body, embeds each chunk as a linked memory_item, and —
    when the document has no text layer (scanned / image-only) — sets an explicit
    `no_text_layer: true` flag on the PARENT item's metadata (D-24-03: auditable,
    not a silent no-op) via provider.update. The whole body is guarded: this task
    runs after the request returned, so it must swallow everything.
    """
    try:
        res = await extract_and_ingest_body(provider=provider, **kw)
        if res.no_text_layer:
            # HI-01: provider.update() REPLACES metadata wholesale, so patch off a FRESH
            # read — not the closure-captured snapshot taken before extraction started.
            # A concurrent PATCH /v1/memory/{id} that landed during the (seconds-long)
            # extraction window would otherwise be silently clobbered. Fall back to the
            # snapshot only if the item vanished (deleted mid-flight).
            fresh = await provider.get(kw["parent_item_id"], team_scope=kw["team_scope"])
            base_meta = dict(fresh.metadata) if fresh and fresh.metadata else dict(parent_metadata)
            await provider.update(
                kw["parent_item_id"],
                team_scope=kw["team_scope"],
                patch={"metadata": {**base_meta, "no_text_layer": True}},
            )
        log.info(
            "media.body_ingest_done",
            parent_item_id=kw.get("parent_item_id"),
            chunk_count=res.chunk_count,
            no_text_layer=res.no_text_layer,
            skipped=res.skipped,
        )
    except Exception as exc:  # detached task — never surface an unhandled exception
        log.warning(
            "media.body_ingest_failed",
            parent_item_id=kw.get("parent_item_id"),
            error=str(exc),
            exc_info=True,  # LOW: retain the stack trace for a detached-task post-mortem
        )


def _ensure_bucket(client: Any, bucket: str) -> None:
    """Create the bucket if it does not yet exist (idempotent)."""
    from botocore.exceptions import ClientError  # lazy — not available outside Docker

    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=bucket)
        else:
            raise


# ---------------------------------------------------------------------------
# POST /media/upload
# ---------------------------------------------------------------------------


@router.post("/media/upload", status_code=201)
async def upload_media(
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    project_scope: str | None = Form(default=None),
    truth_level: str = Form(default="WORKING"),
    source_surface: str = Form(default="extension"),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
) -> dict[str, Any]:
    """Upload a file (≤25 MB) to MinIO and create a media memory_item.

    Returns {item_id, key, mime, size, raw_path, signed_url}. `raw_path` needs a
    Bearer header; `signed_url` carries its own short-lived token so a browser can
    open it directly (used to hand a file to a teammate).
    """
    client = get_minio_client()
    if client is None:
        raise HTTPException(503, "media storage not configured")

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file exceeds maximum size of {_MAX_UPLOAD_BYTES // (1024*1024)} MB")

    item_id = str(uuid.uuid4())
    key, mime, _ext = derive_key_and_mime(
        file.filename, file.content_type, team_scope, item_id
    )

    # Ensure the media bucket exists (idempotent — cheap HEAD request).
    try:
        _ensure_bucket(client, settings.MINIO_BUCKET)
    except Exception as exc:
        log.error("media.bucket_ensure_failed", bucket=settings.MINIO_BUCKET, error=str(exc))
        raise HTTPException(503, "media storage bucket unavailable") from exc

    try:
        client.put_object(
            Bucket=settings.MINIO_BUCKET,
            Key=key,
            Body=data,
            ContentType=mime,
        )
    except Exception as exc:
        log.error("media.put_failed", key=key, error=str(exc))
        raise HTTPException(503, "media upload failed") from exc

    now = datetime.now(timezone.utc)
    source_tag = f"upload:{source_surface}"[:128]
    item = MemoryItem(
        id=item_id,
        team_scope=team_scope,
        project_scope=project_scope,
        content=caption or file.filename or "media",
        metadata={
            "media": {
                "key": key,
                "mime": mime,
                "size": len(data),
                "filename": file.filename,
            }
        },
        source=source_tag,
        truth_level=truth_level,  # type: ignore[arg-type]  # pydantic coerces str→TruthLevel
        confidence=1.0,
        visibility="team",  # type: ignore[arg-type]
        validation_status="pending",  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
    )
    await provider.upsert(item)

    # Phase 24 (DOCBODY-01): extract the document body and embed each chunk as a
    # linked memory_item, fire-and-forget AFTER the object + parent card item are
    # committed. The 201 response is never blocked and any extraction failure is
    # invisible to the uploader (D-24-01). Gated by a kill-switch so a zero-key
    # install can disable it entirely.
    if settings.DOCBODY_EXTRACTION_ENABLED:
        background.spawn(
            _run_body_ingest(
                provider=provider,
                data=data,
                mime=mime,
                filename=file.filename,
                media_key=key,
                parent_item_id=item_id,
                team_scope=team_scope,
                project_scope=project_scope,
                truth_level=truth_level,
                # MD-03: thread the parent's remaining tagging fields EXPLICITLY so body
                # chunks inherit the real upload values, not defaults that merely happen
                # to match. If the parent's visibility/validation/confidence ever change,
                # the chunks follow instead of silently diverging.
                visibility=item.visibility,
                validation_status=item.validation_status,
                confidence=item.confidence,
                parent_metadata=item.metadata,
            ),
            name="media.body_ingest",
        )

    # An IMAGE has no text layer to extract, so the branch above leaves its contents
    # invisible to recall — the item's content is just the caption or the filename. Ask a
    # vision model what it shows and embed THAT as a linked child item, fire-and-forget
    # after the 201 for the same reasons as the body path.
    #
    # The gate is any real image mime, not just the four the API accepts: an image/bmp
    # upload SHOULD reach the service so it gets flagged "skipped (unsupported_image_mime)"
    # rather than vanishing. A PDF must NOT — the document path owns it, and an
    # image-description flag on it would be noise.
    if settings.VISION_DESCRIBE_ENABLED and is_image_mime(mime):
        background.spawn(
            _run_image_describe(
                provider=provider,
                data=data,
                mime=mime,
                filename=file.filename,
                media_key=key,
                parent_item_id=item_id,
                team_scope=team_scope,
                project_scope=project_scope,
                truth_level=truth_level,
                # Threaded explicitly so the description inherits the real upload values
                # rather than defaults that merely happen to match today (MD-03).
                visibility=item.visibility,
                parent_metadata=item.metadata,
            ),
            name="media.image_describe",
        )

    log.info(
        "media.uploaded",
        item_id=item_id,
        key=key,
        mime=mime,
        size=len(data),
        team_scope=team_scope,
    )
    return {
        "item_id": item_id,
        "key": key,
        "mime": mime,
        "size": len(data),
        "raw_path": f"/v1/media/{item_id}/raw",
        # Signed, header-free URL for the object the uploader just created.
        #
        # `raw_path` above needs Authorization + X-Team-Scope, which a BROWSER cannot
        # send when it merely opens a URL — so a client that wants to hand this file to
        # a teammate (Phase 22 nudge) or drop it in an <img src> had no usable link.
        # This mints the same short-lived HS256 token /v1/brain/events already returns
        # (mint_media_token, 1h, claims item_id + team_scope), so the serve endpoint
        # validates it without a DB lookup and it stays bound to THIS item and THIS
        # team. Same exposure model as the chat attachments that already ship signed
        # URLs: whoever holds the link can fetch until it expires.
        "signed_url": (
            f"/v1/media/{item_id}/img?t={mint_media_token(item_id, team_scope)}"
        ),
    }


# ---------------------------------------------------------------------------
# GET /media/{item_id}/raw
# ---------------------------------------------------------------------------


@router.get("/media/{item_id}/raw")
async def serve_media_raw(
    item_id: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
) -> Response:
    """Stream a media object back to an authenticated caller.

    Auth: Bearer token + X-Team-Scope (team-scoped — callers cannot fetch
    objects belonging to another team even if they know the item_id).
    """
    item = await provider.get(item_id, team_scope=team_scope)
    if item is None or not (item.metadata or {}).get("media"):
        raise HTTPException(404, "media item not found in this team")

    media = item.metadata["media"]
    client = get_minio_client()
    if client is None:
        raise HTTPException(503, "media storage not configured")

    try:
        from botocore.exceptions import ClientError  # lazy — not available outside Docker

        obj = client.get_object(Bucket=settings.MINIO_BUCKET, Key=media["key"])
        body: bytes = obj["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise HTTPException(404, "media object not found in storage") from exc
        log.error("media.get_failed", key=media["key"], error=str(exc))
        raise HTTPException(503, "media storage error") from exc

    mime: str = media.get("mime", "application/octet-stream")
    filename: str = media.get("filename") or item_id
    # Inline disposition: browsers display images/PDFs directly; other types are
    # downloaded.  The signed-token variant for bare <img src> is slice 2.
    disposition = f'inline; filename="{filename}"'

    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": disposition},
    )


# ---------------------------------------------------------------------------
# GET /media/{item_id}/indexed-text
# ---------------------------------------------------------------------------


@router.get("/media/{item_id}/indexed-text")
async def serve_indexed_text(
    item_id: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
) -> dict[str, Any]:
    """Report what text this attachment actually contributed to the brain.

    ONE call answers it for BOTH kinds. An image's description and a document's
    body chunks are written by different services into differently-shaped child
    items, and the pointer to either only exists on the parent — so a client that
    had to stitch parent → child would need two round trips, per attachment, for
    a hover. `resolve_indexed_text` does the stitching here.

    Auth: Bearer + X-Team-Scope, through the same `get_team_scope` gate the raw
    serve endpoint uses — which is where a blocked member is refused (403), on the
    identical rule `team_chat._resolve_team_and_check_membership` applies. The
    scope is threaded into the child read too, so a computed child id cannot be
    used to read across teams.

    Never returns the stored machine reason: see `indexed_text._detail_for_reason`.
    """
    item = await provider.get(item_id, team_scope=team_scope)
    if item is None or not (item.metadata or {}).get("media"):
        raise HTTPException(404, "media item not found in this team")

    resolved = await resolve_indexed_text(
        provider=provider, item=item, team_scope=team_scope
    )
    return {"item_id": item_id, **resolved.as_dict()}


# ---------------------------------------------------------------------------
# GET /media/{item_id}/img  (BL-003 slice 2)
# ---------------------------------------------------------------------------
#
# Token-gated image/document serve endpoint designed for bare ``<img src>``
# and ``<a href>`` usage — no Bearer header is possible from those contexts.
# The signed token ``t`` is minted by brain.py's ``_enrich_event`` helper
# and is short-lived (1 hour by default).  It embeds the item_id + team_scope
# so this endpoint can authorise without an additional database lookup.
#
# The existing Bearer-authed ``GET /media/{item_id}/raw`` is left unchanged —
# it is still used by programmatic callers (e.g. extension fetch).


@router.get("/media/{item_id}/img")
async def serve_media_img(
    item_id: str,
    t: str = Query(..., description="Signed media token minted by /v1/brain/events"),
    provider: MemoryProvider = Depends(get_memory_provider),
) -> Response:
    """Stream a media object to a browser ``<img>`` or ``<a>`` element.

    Auth: short-lived signed token in query param ``t`` (no Bearer header —
    browsers cannot send one from an img src).  The token is validated via
    HS256 + claim checks before any storage access.
    """
    # Token validation — raises 403 on any failure.
    team_scope = verify_media_token(t, item_id)

    item = await provider.get(item_id, team_scope=team_scope)
    if item is None or not (item.metadata or {}).get("media"):
        raise HTTPException(404, "media item not found")

    media = item.metadata["media"]
    client = get_minio_client()
    if client is None:
        raise HTTPException(503, "media storage not configured")

    try:
        obj = client.get_object(Bucket=settings.MINIO_BUCKET, Key=media["key"])
        body: bytes = obj["Body"].read()
    except Exception as exc:
        log.error("media.img_get_failed", key=media.get("key"), error=str(exc))
        raise HTTPException(404, "object not found") from exc

    mime: str = media.get("mime") or "application/octet-stream"
    filename: str = media.get("filename") or item_id

    return Response(
        content=body,
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            # Short private cache: the token is already short-lived (1h); caching
            # 5 min avoids hammering memory-api for every thumbnail re-render.
            "Cache-Control": "private, max-age=300",
        },
    )

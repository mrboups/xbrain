"""Media upload and raw-serve endpoints (BL-003 slice 1).

POST /v1/media/upload — multipart upload to MinIO, creates a media memory_item.
GET  /v1/media/{item_id}/raw — streams the object back to an authenticated caller.

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
from app.routes.media_helpers import _MAX_UPLOAD_BYTES, derive_key_and_mime, verify_media_token

log = structlog.get_logger(__name__)

router = APIRouter()


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

    Returns {item_id, key, mime, size, raw_path}.
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

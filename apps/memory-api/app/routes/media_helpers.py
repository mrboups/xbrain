"""Pure helpers for the media upload/serve endpoints (BL-003 slice 1).

Isolated here so unit tests can import them without triggering FastAPI route
registration (which requires python-multipart and other runtime deps that are
not installed in the local dev environment outside Docker).
"""
from __future__ import annotations

import mimetypes
import os

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def derive_key_and_mime(
    filename: str | None,
    content_type: str | None,
    team_scope: str,
    item_id: str,
) -> tuple[str, str, str]:
    """Return (object_key, mime, ext) for a given upload.

    All three values are derived deterministically from the inputs so
    callers can unit-test this without touching MinIO.
    """
    mime: str = (
        content_type
        or (mimetypes.guess_type(filename or "")[0])
        or "application/octet-stream"
    )
    raw_ext = os.path.splitext(filename or "")[1]
    if not raw_ext:
        raw_ext = mimetypes.guess_extension(mime) or ""
    # Normalise: keep only the first extension component (mimetypes can return
    # e.g. ".jpe" for image/jpeg — that's fine; we just strip any spurious chars).
    ext = raw_ext if raw_ext.startswith(".") else ("." + raw_ext if raw_ext else "")
    key = f"media/{team_scope}/{item_id}{ext}"
    return key, mime, ext

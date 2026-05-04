"""Google Drive client helpers for mcp-drive-read sidecar.

Supports:
- export_file_as_text(): Google Docs/Sheets/Slides -> plain text via files.export
- get_pdf_bytes(): PDF files -> bytes (for pypdf extraction)
- update_file_content(): Write plain text back to a Drive file (drive.file scope)

Credentials: via google.oauth2.credentials.Credentials from environment vars.
For production: credentials are passed via headers from mcp-gateway (decrypted
from team_drive_mappings.oauth_credentials_enc).
"""
from __future__ import annotations

import os
import structlog
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

log = structlog.get_logger(__name__)

MAX_BYTES = 50_000

# MIME types that support files.export (Google Workspace formats)
EXPORTABLE_MIMES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
}

PDF_MIME = "application/pdf"


def _build_service(access_token: str, refresh_token: str | None = None):
    """Build Drive v3 service from OAuth tokens."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_service():
    """Build service from environment variables (Phase 3 pattern)."""
    access_token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "")
    if not access_token:
        raise RuntimeError(
            "GOOGLE_DRIVE_ACCESS_TOKEN not set — Drive tools require OAuth setup"
        )
    return _build_service(access_token, refresh_token or None)


def export_file_as_text(file_id: str) -> str:
    """Export a Google Workspace file (Doc/Sheet/Slide) as plain text.

    Also handles PDFs via binary download + pypdf extraction.
    Falls back to direct media download for other text files (e.g., Markdown).
    """
    service = _get_service()
    try:
        # First: get file metadata to determine MIME type
        meta = service.files().get(fileId=file_id, fields="id,name,mimeType").execute()
        mime = meta.get("mimeType", "")
        log.info("drive_read.file_meta", file_id=file_id, mime=mime, name=meta.get("name"))

        if mime in EXPORTABLE_MIMES:
            # Google Workspace file — use export
            content = service.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute()
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")[:MAX_BYTES]
            return str(content)[:MAX_BYTES]

        elif mime == PDF_MIME:
            # PDF — download binary and extract text via pypdf
            import io
            from googleapiclient.http import MediaIoBaseDownload
            from pypdf import PdfReader

            fh = io.BytesIO()
            request = service.files().get_media(fileId=file_id)
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)
            reader = PdfReader(fh)
            text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
            return text[:MAX_BYTES]

        else:
            # Markdown or other text file — get as raw bytes
            content = service.files().get_media(fileId=file_id).execute()
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")[:MAX_BYTES]
            return str(content)[:MAX_BYTES]

    except HttpError as exc:
        log.error("drive_read.export_error", file_id=file_id, status=exc.resp.status)
        raise


def update_file_content(file_id: str, content: str) -> str:
    """Overwrite the text content of a Drive file. Requires drive.file scope.

    Returns the file name on success.
    """
    from googleapiclient.http import MediaInMemoryUpload

    service = _get_service()
    try:
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
        result = service.files().update(fileId=file_id, media_body=media).execute()
        log.info("drive_write.done", file_id=file_id, name=result.get("name"))
        return result.get("name", file_id)
    except HttpError as exc:
        log.error("drive_write.error", file_id=file_id, status=exc.resp.status)
        raise

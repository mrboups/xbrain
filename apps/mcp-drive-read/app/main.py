"""MCP Drive Read/Write sidecar — live Drive file access.

Exposes two tools:
- read_drive_file(file_id): Live read without going through sync cache.
  Use case: "I just saved a doc 30s ago, read it now".
- write_drive_file(file_id, content, user_consent): Write back with explicit opt-in (INT-04).

Standalone FastMCP, port 8101. Single worker only.

NOTE: Do NOT run with --workers N in uvicorn. FastMCP uses in-memory session state
per process; multi-worker breaks session continuity (issue #658).
NOTE: Do NOT mount this ASGI app inside a parent FastAPI instance; use standalone
uvicorn (issue #1367).
"""
from __future__ import annotations

import asyncio

import structlog
from mcp.server.fastmcp import FastMCP

from app.drive_client import export_file_as_text, update_file_content

log = structlog.get_logger(__name__)

# Single FastMCP instance — tools are registered as module-level async functions.
# The gateway discovers tools via GET /mcp (tool list endpoint, MCP protocol).
mcp = FastMCP("xbrain-drive-read", host="0.0.0.0", port=8101)


@mcp.tool()
async def read_drive_file(file_id: str) -> str:
    """Read a Google Drive file live by its file ID. Returns plain text.

    Bypasses the sync cache — use this for files modified in the last 5 minutes
    before the drive-sync polling has picked them up.

    Supported file types:
    - Google Docs, Sheets, Slides (exported as plain text via files.export)
    - PDF (binary download + pypdf text extraction)
    - Markdown and other text files (direct media download)

    Args:
        file_id: Google Drive file ID (found in the file's URL after /d/).

    Returns:
        Plain text content of the file (max 50KB).
    """
    log.info("mcp.tool_call.read_drive_file", file_id=file_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, export_file_as_text, file_id)


@mcp.tool()
async def write_drive_file(file_id: str, content: str, user_consent: bool) -> str:
    """Write text content back to a Google Drive file (INT-04 write-back).

    REQUIRES explicit user consent. Refuses to write if user_consent is not True.
    This opt-in is mandatory per xbrain architecture (INT-04).

    The write uses the drive.file scope — limited to files created by the app
    or explicitly shared with it. Audit entry is logged via mcp-gateway.

    Args:
        file_id: Google Drive file ID.
        content: Text content to write (overwrites existing content).
        user_consent: Must be True. If False, returns an error without writing.

    Returns:
        File name on success, or error message if consent not given.
    """
    if not user_consent:
        log.warning("mcp.tool_call.write_drive_file.denied", file_id=file_id, reason="no_consent")
        return "ERROR: user_consent must be True to write to Drive. User must explicitly opt in."
    log.info("mcp.tool_call.write_drive_file", file_id=file_id, content_len=len(content))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, update_file_content, file_id, content)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

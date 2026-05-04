"""MCP Scraper sidecar — URL -> text, max 50KB.

Standalone FastMCP server (streamable-http transport, port 8100).
Reuses the document_loader.load_url() pattern from agent-runtime.

IMPORTANT: Do NOT mount this inside a parent FastAPI app (issue #1367 — RuntimeError:
Task group is not initialized). Run as standalone uvicorn process only.
Single worker mandatory — multi-worker splits in-memory session state (issue #658).
"""
from __future__ import annotations

import httpx
import structlog
from mcp.server.fastmcp import FastMCP

log = structlog.get_logger(__name__)

MAX_BYTES = 50_000  # Match agent-runtime document_loader (~50KB bounds LLM input cost)

mcp = FastMCP("xbrain-scraper")


async def _load_url(url: str) -> str:
    """Fetch URL body, capped at MAX_BYTES.

    Mirror of apps/agent-runtime/app/tools/document_loader.load_url().
    Kept local to avoid cross-service import coupling between sidecars.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text[:MAX_BYTES]


@mcp.tool()
async def scrape(url: str) -> str:
    """Fetch a URL and return its text content (max 50KB).

    Args:
        url: The URL to scrape. Must be http:// or https://.

    Returns:
        Text content of the page, truncated at 50KB.
        Raises on HTTP errors (4xx, 5xx) or network failures.
    """
    log.info("scraper.fetch", url=url[:100])
    try:
        text = await _load_url(url)
        log.info("scraper.done", url=url[:100], bytes=len(text))
        return text
    except httpx.HTTPStatusError as exc:
        log.warning(
            "scraper.http_error",
            url=url[:100],
            status=exc.response.status_code,
        )
        raise
    except Exception as exc:
        log.error("scraper.error", url=url[:100], error=str(exc))
        raise


if __name__ == "__main__":
    # Single worker — critical: FastMCP session state is in-memory per process.
    # Multi-worker mode causes session 404s (issue #658).
    # Transport streamable-http binds to /mcp endpoint on the specified port.
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8100)

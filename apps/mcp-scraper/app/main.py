"""MCP Scraper sidecar — URL -> readable text, max 50KB.

Standalone FastMCP server (streamable-http transport, port 8100).
Reuses the document_loader.load_url() pattern from agent-runtime.

IMPORTANT: Do NOT mount this inside a parent FastAPI app (issue #1367 — RuntimeError:
Task group is not initialized). Run as standalone uvicorn process only.
Single worker mandatory — multi-worker splits in-memory session state (issue #658).

WHY THIS RETURNS TEXT AND NOT `r.text` (260806-5zq)
---------------------------------------------------
It used to return the raw body. The caller — `team_chat_agent._fetch_url_via_scraper`
— then keeps the FIRST 6000 characters. On a real page those 6000 characters are
`<head>`, inline CSS and `<script>`: measured on `pitch.digitalaf.xyz`, 6000
characters delivered carried **306 characters of readable text**, and the page's
own dates sat at raw offset ~18,300 — three times past the cut. The model was
handed markup and no content.

The turn that exposed it still answered correctly, which is the trap: on Pro/Max
the request goes to claude.ai's own completion endpoint, which fetched the page
itself. That fallback does not exist on the team-API-key path or in the OSS
edition, where the same question gets the 306 characters.

Extracting first puts the whole page (8,208 characters for that URL) inside the
same budget, and the dates land at offset 1,966.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx
import structlog
from mcp.server.fastmcp import FastMCP

log = structlog.get_logger(__name__)

# Bound on the RAW body we are willing to parse. It is deliberately far larger
# than the returned text: on a big page the content sits deep behind navigation
# markup — Wikipedia's article body only appears after ~1MB of HTML — so capping
# the input at the output size guarantees the caller receives menus and never
# the article.
MAX_RAW_BYTES = 1_000_000

# Bound on what we RETURN. Unchanged from the original contract (~50KB bounds
# LLM input cost, matching agent-runtime document_loader).
MAX_BYTES = 50_000

# Below this, a <main>/<article> subtree is treated as decorative rather than
# the page's content, and the whole document is used instead.
MIN_MAIN_CHARS = 200

# Sent because httpx otherwise announces `python-httpx/x.y`, which Wikipedia and
# anything behind a bot filter answer with 403 (reproduced 2026-08-06).
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Elements whose contents are never page text. `head` covers <title> too, which
# is why the title is re-attached separately below — it is often the single most
# useful line on the page.
#
# `nav` and `aside` are here because they are navigation by definition and they
# sit in front of the content, so a caller keeping the first N characters reads
# menus. `footer` is deliberately NOT dropped: on an event or product page it
# routinely carries the address, the contact and the dates.
_DROP_ELEMENTS = frozenset(
    {
        "script", "style", "noscript", "svg", "head", "template", "iframe",
        "canvas", "nav", "aside",
    }
)

# Tags that end a line. Without them "Register here" and the next label glue
# into one token and the text reads as garbage.
_BLOCK_ELEMENTS = frozenset(
    {
        "p", "div", "br", "hr", "li", "ul", "ol", "tr", "td", "th", "table",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
        "footer", "nav", "aside", "main", "form", "blockquote", "pre", "figure",
        "figcaption", "dt", "dd", "dl", "option", "label",
    }
)

# When one of these wraps the page's real content, only its text is returned.
# `article` and `main` are also block elements above — membership here is about
# *selection*, not line breaks.
_MAIN_ELEMENTS = frozenset({"main", "article"})

mcp = FastMCP("xbrain-scraper", host="0.0.0.0", port=8100)


class _TextExtractor(HTMLParser):
    """HTML -> text, stdlib only.

    No new dependency on purpose: the sidecar runs under `mem_limit: 128m`, and
    the image is built on the VM (dev is ARM64, prod amd64), so a wheel with a
    native extension is a deploy risk this does not need to take.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False
        # Second buffer, filled only while inside <main>/<article>. Wikipedia
        # puts ~50,000 characters of navigation before the prose; a caller that
        # keeps the first few thousand characters would otherwise receive the
        # sidebar every time.
        self._main_parts: list[str] = []
        self._main_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and self._skip_depth <= 1:
            self._in_title = True
        if tag in _DROP_ELEMENTS:
            self._skip_depth += 1
            return
        if tag in _MAIN_ELEMENTS:
            self._main_depth += 1
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")
            if self._main_depth:
                self._main_parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/> and <hr/> never reach handle_endtag — handle the self-closing form.
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")
            if self._main_depth:
                self._main_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _DROP_ELEMENTS:
            # Guard against a stray close tag driving the counter negative, which
            # would then swallow the rest of the document.
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")
            if self._main_depth:
                self._main_parts.append("\n")
        # Decrement AFTER the newline so the closing tag still terminates the
        # last line of the subtree.
        if tag in _MAIN_ELEMENTS and self._main_depth > 0:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._skip_depth == 0:
            self._parts.append(data)
            if self._main_depth:
                self._main_parts.append(data)

    def text(self) -> str:
        """Prefer the <main>/<article> subtree, fall back to the whole document.

        Truncating a page from the front hands the caller whatever the site put
        first, which on a large site is navigation. Selecting the content
        element is what makes the first characters worth reading.
        """
        body = _collapse("".join(self._parts))
        main = _collapse("".join(self._main_parts))
        if len(main) >= MIN_MAIN_CHARS:
            body = main
        title = _collapse("".join(self._title_parts))
        if title and not body.startswith(title):
            return f"{title}\n\n{body}" if body else title
        return body


def _collapse(raw: str) -> str:
    """Squeeze whitespace without destroying paragraph structure."""
    raw = raw.replace(" ", " ")
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r" *\n *", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _looks_like_html(body: str) -> bool:
    head = body[:1000].lstrip().lower()
    return head.startswith(("<!doctype html", "<html", "<?xml")) or "<body" in head


def extract_text(body: str, content_type: str = "") -> str:
    """Return readable text for HTML; return non-HTML bodies untouched.

    A JSON API response or a plain-text file must survive verbatim — running
    them through an HTML parser would mangle them for no gain.

    Fail-soft: a parser error returns the original body. Losing formatting is
    recoverable, losing the fetch is not.
    """
    ctype = (content_type or "").lower()
    is_html = "html" in ctype or (not ctype and _looks_like_html(body))
    if not is_html:
        return body
    try:
        parser = _TextExtractor()
        parser.feed(body)
        parser.close()
        text = parser.text()
    except Exception as exc:  # noqa: BLE001 — never lose a successful fetch
        log.warning("scraper.extract_failed", error=str(exc))
        return body
    # An empty result means the page is client-rendered (or we mis-parsed it).
    # The raw body is worth more to the model than nothing at all.
    return text or body


async def _load_url(url: str) -> str:
    """Fetch a URL and return its readable text.

    Two separate bounds, and the distinction matters: the RAW body is capped at
    MAX_RAW_BYTES so parsing stays memory-safe, and the RETURNED text is capped
    at MAX_BYTES so the caller's contract is unchanged. Capping the input at the
    output size — what this used to do — truncates big pages before their content
    begins.
    """
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, headers=BROWSER_HEADERS
    ) as c:
        r = await c.get(url)
        r.raise_for_status()
        raw = r.text[:MAX_RAW_BYTES]
        return extract_text(raw, r.headers.get("content-type", ""))[:MAX_BYTES]


@mcp.tool()
async def scrape(url: str) -> str:
    """Fetch a URL and return its readable text content.

    HTML is reduced to text (scripts, styles and tags removed) so the caller's
    character budget is spent on content. Non-HTML bodies are returned as-is.

    Args:
        url: The URL to scrape. Must be http:// or https://.

    Returns:
        Readable text of the page. The raw body is capped at 50KB before
        extraction, so the returned text is normally far shorter.
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
    mcp.run(transport="streamable-http")

"""Document loaders for the ingestion agent.

URL: HTTP fetch with redirect follow. Caps body to MAX_BYTES to bound LLM cost.
PDF: pypdf text extraction (Phase 3 may swap for OCR / unstructured.io).

Phase 3 will add: readability/trafilatura cleanup for HTML, OCR fallback for
scanned PDFs, MIME detection.
"""

from __future__ import annotations

from io import BytesIO

import httpx

MAX_BYTES = 50_000  # ~50KB — bounds extract_facts LLM input


async def load_url(url: str) -> str:
    """Fetch a URL and return its text body, capped at MAX_BYTES."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text[:MAX_BYTES]


async def load_pdf_bytes(content: bytes) -> str:
    """Extract text from a PDF byte payload, capped at MAX_BYTES."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    pages = [p.extract_text() or "" for p in reader.pages]
    return "\n\n".join(pages)[:MAX_BYTES]

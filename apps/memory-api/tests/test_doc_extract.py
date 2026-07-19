"""Pure unit tests for doc_extract — mime dispatch, guards, chunking, no_text_layer.

No DB, no network, no MinIO, no embedder, no markers: this runs in the normal suite.
Real PDFs / DOCX are generated IN-test with reportlab / python-docx (lazy-imported in
the helpers so the import cost is test-only) so the extraction path is exercised against
GENUINE document bytes, not fixtures or mocks — mirroring Phase 19's non-mocked discipline.
The full real-Postgres+Qdrant embed→retrieve gate is Plan 24-03; this proves the pure core.
"""
from __future__ import annotations

import io
import itertools

from app.services.doc_extract import (
    DOCX_MIME,
    PDF_MIME,
    ExtractResult,
    chunk_text,
    extract_document,
)

# Caps mirrored from config safe defaults. The module stays PURE — it never reads
# settings; every cap arrives as an explicit keyword arg (that is exactly what keeps it
# unit-testable without a live Settings()).
_CAPS = dict(max_file_bytes=10 * 1024 * 1024, max_total_chars=200_000, min_chars=20)


# --- helpers: generate REAL documents (lazy imports so cost is test-only) ---


def _make_text_pdf(body: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in body.split("\n"):
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_empty_pdf() -> bytes:
    """A blank page — a real PDF with NO text layer (the scanned-doc analogue)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_docx(paragraphs: list[str]) -> bytes:
    import docx

    buf = io.BytesIO()
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    d.save(buf)
    return buf.getvalue()


# --- dispatch + extraction ---


def test_pdf_with_text_layer_extracts_body():
    pdf = _make_text_pdf("BUDGET APPROVED distinctive phrase alpha")
    r = extract_document(pdf, PDF_MIME, "f.pdf", **_CAPS)
    assert isinstance(r, ExtractResult)
    assert r.skipped is False
    assert r.no_text_layer is False
    assert r.surface == "pdf"
    assert r.truncated is False
    assert "distinctive" in r.text


def test_empty_pdf_flags_no_text_layer_and_emits_no_text():
    pdf = _make_empty_pdf()
    r = extract_document(pdf, PDF_MIME, "scan.pdf", **_CAPS)
    assert r.no_text_layer is True
    assert r.text == ""
    assert r.skipped is False
    assert r.surface == "pdf"


def test_docx_extracts_paragraph_text():
    docx_bytes = _make_docx(["First paragraph body.", "Second distinctive marker beta."])
    r = extract_document(docx_bytes, DOCX_MIME, "f.docx", **_CAPS)
    assert r.surface == "docx"
    assert r.skipped is False
    assert r.no_text_layer is False
    assert "distinctive marker beta" in r.text
    assert "First paragraph body." in r.text


def test_markdown_decoded_as_is():
    r = extract_document(b"# hi\nbody text here", "text/markdown", "n.md", **_CAPS)
    assert r.surface == "markdown"
    assert r.skipped is False
    assert r.no_text_layer is False
    # syntax kept as-is (D-24 discretion — markdown embedded raw, never rendered here)
    assert "# hi" in r.text
    assert "body text here" in r.text


def test_plain_text_surface():
    r = extract_document(b"plain body content here", "text/plain", "n.txt", **_CAPS)
    assert r.surface == "text"
    assert r.skipped is False
    assert r.text == "plain body content here"


def test_short_text_is_short_not_no_text_layer():
    # For text/markdown, a tiny body is just short — NOT a "no text layer" signal
    # (that concept only applies to pdf/docx surfaces).
    r = extract_document(b"hi", "text/plain", "n.txt", **_CAPS)
    assert r.surface == "text"
    assert r.no_text_layer is False
    assert r.text == "hi"


# --- guards ---


def test_unknown_mime_skipped_no_crash():
    r = extract_document(b"\x00\x01\x02binary", "application/octet-stream", "x.bin", **_CAPS)
    assert r.skipped is True
    assert r.surface is None
    assert r.text == ""


def test_oversized_text_truncated_to_char_cap():
    big = ("a" * 5000).encode()
    r = extract_document(
        big, "text/plain", "big.txt", max_file_bytes=10 * 1024 * 1024, max_total_chars=100, min_chars=20
    )
    assert r.truncated is True
    assert len(r.text) == 100


def test_file_too_large_skipped_before_parse():
    # 2KB body, 1KB cap -> skipped on the size guard, never parsed (DoS defence).
    r = extract_document(
        b"x" * 2048, "text/plain", "big.txt", max_file_bytes=1024, max_total_chars=200_000, min_chars=20
    )
    assert r.skipped is True
    assert r.reason == "file_too_large"
    assert r.text == ""


def test_corrupt_pdf_does_not_raise():
    r = extract_document(b"%PDF-not-real garbage", PDF_MIME, "bad.pdf", **_CAPS)
    # A malformed/crafted document must be caught INSIDE the module — never propagate.
    assert r.skipped is True or r.no_text_layer is True
    if r.skipped:
        assert r.reason.startswith("parse_error")


# --- chunking ---


def test_chunk_sliding_window_count_and_size():
    chunks = chunk_text("a" * 5000, chunk_size=1500, overlap=150, max_chunks=200)
    assert len(chunks) == 4
    assert all(len(c) <= 1500 for c in chunks)


def test_chunk_overlap_is_exact():
    # Distinctive (whitespace-free) content so the overlap assertion is real, not a
    # trivially-equal run of identical characters, and strip() is a no-op on the edges.
    text = "".join(chr(65 + (i % 26)) for i in range(5000))
    chunks = chunk_text(text, chunk_size=1500, overlap=150, max_chunks=200)
    assert len(chunks) >= 2
    for a, b in itertools.pairwise(chunks):
        if len(a) == 1500:  # a full chunk carries a full overlap tail
            assert a[-150:] == b[:150]


def test_chunk_count_cap_honored():
    chunks = chunk_text("x" * 100000, chunk_size=1500, overlap=150, max_chunks=2)
    assert len(chunks) == 2


def test_chunk_empty_input_returns_empty():
    assert chunk_text("", chunk_size=1500, overlap=150, max_chunks=200) == []


def test_chunk_whitespace_only_dropped():
    assert chunk_text("   \n\t   ", chunk_size=1500, overlap=150, max_chunks=200) == []

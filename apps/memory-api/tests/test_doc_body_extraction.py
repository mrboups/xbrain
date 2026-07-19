"""Phase 24 (DOCBODY-01) — the gate-lesson proof for document BODY extraction.

Against a REAL Postgres + REAL Qdrant (testcontainers) with NO OpenAI key set, a real
PDF / DOCX / text-markdown document whose BODY carries a distinctive phrase (absent from
the caption AND the filename) is run through `extract_and_ingest_body`; its chunks land
as linked memory_items and `memory_search` for that BODY phrase RETRIEVES the document —
using the REAL local fastembed model (384-dim), NOT a fabricated vector.

Why this is the gate: a caption-only test, or a stubbed embedder, would pass even if the
body was never embedded. This one cannot — the retrieval query is the distinctive BODY
phrase (never present in the caption/filename), and the embedder is the real keyless
fastembed on the actual write+read path. A fabricated vector could not rank the matching
body chunk first, so a broken integration fails here instead of sailing through green.

SKIP=FAIL: the ONLY sanctioned skip is the conftest Docker gate (Docker genuinely absent)
plus the `local_model_ready` guard (Docker up but the fastembed model neither cached NOR
downloadable). A ranking / linkage / guard assertion NEVER skips.

Mirrors apps/memory-api/tests/test_local_embeddings.py (the Phase-19 sibling gate): the
same `local_env` / `local_model_ready` / `_build_native_provider` / `_aclose` discipline,
the same real 384-dim keyless model, the same `@pytest.mark.integration` marker.
"""
from __future__ import annotations

import io
import os
import tempfile

import pytest
import pytest_asyncio

from xbrain_memory.types import TruthLevel

from app.services.doc_body_ingest import extract_and_ingest_body
from app.services.doc_extract import DOCX_MIME, PDF_MIME

# --- Fixed team/project. A DECOY team proves cross-team isolation (T-24-12). ---
TEAM = "phase24-docbody"
PROJECT = "gate"
DECOY_TEAM = "phase24-docbody-decoy"

# Distinctive BODY phrases — vivid + unique so a semantic hit is unambiguous, and chosen
# so NONE of them appears in any filename/caption literal used below (that is the gate:
# only the extracted BODY can carry the phrase, so only a real body embed can retrieve it).
PDF_PHRASE = "the peregrine falcon dives at three hundred kilometres per hour"
DOCX_PHRASE = "the mitochondrion is the powerhouse of the quarterly budget"
TEXT_PHRASE = "aubergines ferment best at eleven degrees in a clay amphora"


# === Fixtures (shape copied from test_local_embeddings.py) ===================


@pytest.fixture
def local_env():
    """Force the keyless local provider + native backend; restore afterward.

    Mutates the `settings` singleton in place (the established convention). Pins the
    fastembed cache to a writable temp dir so the pytest run — which HAS network — can
    materialize the real model once and reuse it. OPENAI_API_KEY is emptied so the OpenAI
    path can NOT be selected: this gate is keyless by construction.
    """
    from app.config import settings
    import app.deps as deps

    orig = (
        settings.EMBEDDINGS_PROVIDER,
        settings.OPENAI_API_KEY,
        settings.MEMORY_BACKEND,
        settings.EMBEDDING_CACHE_DIR,
    )
    orig_singleton = deps._memory_provider_singleton

    cache_dir = os.path.join(tempfile.gettempdir(), "xbrain_fastembed_cache")
    os.makedirs(cache_dir, exist_ok=True)

    settings.EMBEDDINGS_PROVIDER = "local"
    settings.OPENAI_API_KEY = ""
    settings.MEMORY_BACKEND = "native"
    settings.EMBEDDING_CACHE_DIR = cache_dir
    deps._memory_provider_singleton = None
    try:
        yield settings
    finally:
        (
            settings.EMBEDDINGS_PROVIDER,
            settings.OPENAI_API_KEY,
            settings.MEMORY_BACKEND,
            settings.EMBEDDING_CACHE_DIR,
        ) = orig
        deps._memory_provider_singleton = orig_singleton


@pytest_asyncio.fixture
async def local_model_ready(local_env):
    """Materialize the real fastembed model ONCE — the narrow gate-lesson skip guard.

    In a restricted CI/sandbox where Docker is up but the model is neither cached NOR
    downloadable, this skips cleanly instead of hard-failing. The guard wraps ONLY the
    model materialization here — NEVER a ranking/linkage/guard assertion in a test body
    (a wrong retrieval MUST fail, not skip). Mirrors the conftest `_docker_available()`
    -> pytest.skip pattern.
    """
    from app.embedders import local_embedder

    try:
        vec = await local_embedder("warmup: materialize the real fastembed model")
    except (ConnectionError, OSError) as e:  # model not cached AND no network to fetch it
        pytest.skip(f"local model not cached and no network to download it: {e}")
    assert len(vec) == 384  # sanity: the REAL keyless model is loaded and returns 384 dims
    return True


# === Helpers =================================================================


def _build_native_provider():
    """Construct a fresh NativeProvider bound to the testcontainers PG + Qdrant.

    Built directly (not via the deps singleton) so each test gets a provider whose
    asyncpg pool + Qdrant client live in THIS test's event loop.
    """
    from app.config import settings
    from app.embedders import get_embedder
    from xbrain_memory.providers.native_provider import NativeProvider

    pg_dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return NativeProvider(
        pg_dsn=pg_dsn,
        qdrant_url=settings.QDRANT_URL,
        embedder=get_embedder(),
        qdrant_api_key=settings.QDRANT_API_KEY,
    )


async def _aclose(provider) -> None:
    """Close the provider's asyncpg pool + Qdrant client to avoid stale-loop warnings."""
    try:
        if getattr(provider, "_pool", None) is not None:
            await provider._pool.close()
    finally:
        try:
            await provider._qdrant.close()
        except Exception:
            pass


# --- Real document generators (lazy imports so collection is unaffected on hosts ---
# --- without the dev wheels; integration tests run under Docker/CI Linux). ---


def _make_pdf(text: str) -> bytes:
    """A real PDF WITH a text layer carrying `text` (reportlab, dev-only)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    # Split into short lines so the full phrase stays on the page's text layer.
    y = 720
    for line in text.split(". "):
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_empty_pdf() -> bytes:
    """A real PDF with a blank page and NO drawString → extract_text() == '' (no text layer)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.showPage()  # blank page, no text drawn
    c.save()
    return buf.getvalue()


def _make_docx(text: str) -> bytes:
    """A real DOCX whose single paragraph carries `text` (python-docx, dev-only)."""
    import docx

    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph(text)
    d.save(buf)
    return buf.getvalue()


# === THE GATE: BODY → embedded → retrieved keyless (real infra, non-faked) ====


@pytest.mark.integration
async def test_pdf_body_extracted_embedded_retrieved_keyless(
    pg_url, qdrant_url, local_model_ready
):
    """A real PDF's BODY phrase (absent from filename) is extracted, embedded keyless, and
    retrieved by memory_search — and a DECOY team's identical body never leaks (T-24-12)."""
    from app.qdrant_setup import ensure_collections

    await ensure_collections()
    provider = _build_native_provider()
    try:
        # The PHRASE lives ONLY inside the generated PDF body; the filename is neutral.
        data = _make_pdf(f"Meeting notes. {PDF_PHRASE}. End of memo.")
        res = await extract_and_ingest_body(
            provider=provider,
            data=data,
            mime=PDF_MIME,
            filename="q3-notes.pdf",  # no *_PHRASE anywhere in the caption/filename
            media_key="media/phase24/q3-notes.pdf",
            parent_item_id="PARENT-PDF",
            team_scope=TEAM,
            project_scope=PROJECT,
            truth_level="WORKING",
        )
        assert res.skipped is False
        assert res.no_text_layer is False
        assert res.chunk_count >= 1

        # Cross-team decoy: the SAME distinctive body under a DIFFERENT team. A team-scoped
        # search for TEAM must never surface the decoy — proving team isolation live.
        await extract_and_ingest_body(
            provider=provider,
            data=_make_pdf(f"Decoy memo. {PDF_PHRASE}. Decoy end."),
            mime=PDF_MIME,
            filename="decoy.pdf",
            media_key="media/decoy/decoy.pdf",
            parent_item_id="PARENT-PDF-DECOY",
            team_scope=DECOY_TEAM,
            project_scope=PROJECT,
            truth_level="WORKING",
        )

        # The load-bearing assertion: the BODY phrase (never the caption) retrieves the doc,
        # ranked first, via the REAL keyless embedder. A fabricated vector could not do this.
        hits = await provider.search(PDF_PHRASE, team_scope=TEAM, limit=5)
    finally:
        await _aclose(provider)

    assert hits, "semantic search for the PDF BODY phrase returned no hits"
    top = hits[0]
    assert top.item.team_scope == TEAM  # T-24-12: the hit belongs to the querying team
    assert top.item.source == "upload:body"
    assert top.item.truth_level == TruthLevel.WORKING
    assert top.item.metadata["parent_item_id"] == "PARENT-PDF"
    assert top.item.metadata["media_key"] == "media/phase24/q3-notes.pdf"
    # No decoy-team chunk leaked into TEAM's results.
    assert all(h.item.team_scope == TEAM for h in hits)
    assert all(
        h.item.metadata.get("parent_item_id") != "PARENT-PDF-DECOY" for h in hits
    )


@pytest.mark.integration
async def test_docx_body_extracted_embedded_retrieved_keyless(
    pg_url, qdrant_url, local_model_ready
):
    """A real DOCX's BODY phrase (absent from filename) is extracted, embedded keyless, and
    retrieved by memory_search."""
    from app.qdrant_setup import ensure_collections

    await ensure_collections()
    provider = _build_native_provider()
    try:
        data = _make_docx(DOCX_PHRASE)
        res = await extract_and_ingest_body(
            provider=provider,
            data=data,
            mime=DOCX_MIME,
            filename="spec.docx",  # no *_PHRASE in the filename
            media_key="media/phase24/spec.docx",
            parent_item_id="PARENT-DOCX",
            team_scope=TEAM,
            project_scope=PROJECT,
            truth_level="WORKING",
        )
        assert res.skipped is False
        assert res.no_text_layer is False
        assert res.chunk_count >= 1

        hits = await provider.search(DOCX_PHRASE, team_scope=TEAM, limit=5)
    finally:
        await _aclose(provider)

    assert hits, "semantic search for the DOCX BODY phrase returned no hits"
    top = hits[0]
    assert top.item.team_scope == TEAM
    assert top.item.source == "upload:body"
    assert top.item.metadata["parent_item_id"] == "PARENT-DOCX"
    assert top.item.metadata["media_key"] == "media/phase24/spec.docx"


@pytest.mark.integration
async def test_text_markdown_body_extracted_embedded_retrieved_keyless(
    pg_url, qdrant_url, local_model_ready
):
    """A real text/markdown BODY phrase (absent from filename) is extracted, embedded
    keyless, and retrieved by memory_search."""
    from app.qdrant_setup import ensure_collections

    await ensure_collections()
    provider = _build_native_provider()
    try:
        data = f"# Recipe\n\n{TEXT_PHRASE}\n".encode()
        res = await extract_and_ingest_body(
            provider=provider,
            data=data,
            mime="text/markdown",
            filename="recipe.md",  # no *_PHRASE in the filename
            media_key="media/phase24/recipe.md",
            parent_item_id="PARENT-MD",
            team_scope=TEAM,
            project_scope=PROJECT,
            truth_level="WORKING",
        )
        assert res.skipped is False
        assert res.no_text_layer is False
        assert res.chunk_count >= 1

        hits = await provider.search(TEXT_PHRASE, team_scope=TEAM, limit=5)
    finally:
        await _aclose(provider)

    assert hits, "semantic search for the text/markdown BODY phrase returned no hits"
    top = hits[0]
    assert top.item.team_scope == TEAM
    assert top.item.source == "upload:body"
    assert top.item.metadata["parent_item_id"] == "PARENT-MD"
    assert top.item.metadata["media_key"] == "media/phase24/recipe.md"

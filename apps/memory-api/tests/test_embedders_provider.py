"""Phase 19 (EMBED-01) — embedder provider selector + dimension helper.

Unit-only (no DB, no Docker): covers the selector/shape logic named in
19-01-PLAN.md Task 2 <behavior>. The real fastembed inference (local_embedder
actually returning a 384-dim vector from a loaded ONNX model) is covered by
Plan 03's testcontainers-backed end-to-end test — this file only proves the
config → function selection wiring and the fail-safe fallback.

`app.config` / `app.embedders` are imported lazily (inside each test), matching
the established convention in test_edition_gating.py — a top-level import would
freeze `settings` during pytest collection before conftest's env defaults are
guaranteed to apply consistently across the session.
"""

import inspect


def test_garbage_provider_falls_back_to_local():
    from app.config import settings
    from app.embedders import EmbeddingDimensionMismatch, get_embedder, get_embedding_dimension, local_embedder

    original = settings.EMBEDDINGS_PROVIDER
    try:
        settings.EMBEDDINGS_PROVIDER = "garbage"
        assert get_embedder() is local_embedder
        assert get_embedding_dimension() == 384
        assert issubclass(EmbeddingDimensionMismatch, Exception)
    finally:
        settings.EMBEDDINGS_PROVIDER = original


def test_unset_default_is_local():
    from app.config import settings
    from app.embedders import get_embedder, get_embedding_dimension, local_embedder

    # Settings default (no override) must already be "local" per D-19-02.
    assert settings.EMBEDDINGS_PROVIDER == "local"
    assert get_embedder() is local_embedder
    assert get_embedding_dimension() == 384


def test_openai_provider_selectable():
    from app.config import settings
    from app.embedders import get_embedder, get_embedding_dimension, openai_embedder

    original = settings.EMBEDDINGS_PROVIDER
    try:
        settings.EMBEDDINGS_PROVIDER = "openai"
        assert get_embedder() is openai_embedder
        assert get_embedding_dimension() == 1536
    finally:
        settings.EMBEDDINGS_PROVIDER = original


def test_openai_provider_case_insensitive():
    from app.config import settings
    from app.embedders import get_embedder, openai_embedder

    original = settings.EMBEDDINGS_PROVIDER
    try:
        settings.EMBEDDINGS_PROVIDER = "OpenAI"
        assert get_embedder() is openai_embedder
    finally:
        settings.EMBEDDINGS_PROVIDER = original


def test_local_embedder_matches_embedder_contract():
    """Both embedders MUST match Embedder = Callable[[str], Awaitable[list[float]]]."""
    from app.embedders import local_embedder, openai_embedder

    assert inspect.iscoroutinefunction(local_embedder)
    assert inspect.iscoroutinefunction(openai_embedder)


def test_embedding_dimension_mismatch_is_importable_exception():
    from app.embedders import EmbeddingDimensionMismatch

    assert issubclass(EmbeddingDimensionMismatch, Exception)
    # Boot-fatal exception must carry an actionable message when raised.
    exc = EmbeddingDimensionMismatch("dimension mismatch detail")
    assert "dimension mismatch detail" in str(exc)

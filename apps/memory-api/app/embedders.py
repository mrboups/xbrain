"""Embedding wrappers for backends that need explicit embedding."""

import asyncio

from openai import AsyncOpenAI

from app.config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured for embeddings")
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def openai_embedder(text: str) -> list[float]:
    """Embed text via OpenAI's text-embedding-3-small (1536 dims)."""
    client = _get_client()
    r = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return r.data[0].embedding


# === Phase 19 (EMBED-01) — local keyless embeddings ===

_local_model = None


def _get_local_model():
    """Lazy module-level singleton for the fastembed model.

    Only imports/loads fastembed when EMBEDDINGS_PROVIDER=local actually
    selects local_embedder — matches the neo4j/boto3 lazy-import convention
    already used elsewhere in this app.
    """
    global _local_model
    if _local_model is None:
        from fastembed import TextEmbedding  # lazy import — matches neo4j/boto3 lazy convention

        _local_model = TextEmbedding(
            model_name=settings.LOCAL_EMBEDDING_MODEL,
            cache_dir=settings.EMBEDDING_CACHE_DIR,  # SAME path Plan 02 bakes into the image
        )
    return _local_model


async def local_embedder(text: str) -> list[float]:
    """Embed text via the in-container fastembed model (keyless, offline)."""
    # Offload BOTH the first-call model construction AND the embed to a thread (WR-01):
    # TextEmbedding(...) does a blocking disk/model load on first use, which — if left on
    # the event loop — stalls every concurrent request until the model finishes loading.
    # fastembed's .embed() is likewise a synchronous generator (RESEARCH Pitfall 4).
    vecs = await asyncio.to_thread(lambda: list(_get_local_model().embed([text])))
    return vecs[0].tolist()


def get_embedder():
    """Select the embedder function based on EMBEDDINGS_PROVIDER.

    Falls back to local on any unknown/unset value — mirrors the LOCAL_AUTH
    fail-safe pattern (garbage input must never crash boot).
    """
    if settings.EMBEDDINGS_PROVIDER.lower() == "openai":
        return openai_embedder
    return local_embedder


# Local dims are keyed off the ACTUAL model (WR-02) so changing LOCAL_EMBEDDING_MODEL to
# another known model keeps the write (ingest) and read (search) dimensions in sync. An
# unknown local model falls back to bge-small's 384 — add new models HERE rather than letting
# the collection dim silently desync from what local_embedder actually produces.
_LOCAL_MODEL_DIMENSIONS = {"BAAI/bge-small-en-v1.5": 384}
_DEFAULT_LOCAL_DIM = 384


def get_embedding_dimension() -> int:
    """Single source of truth for the Qdrant vector dimension of the active provider."""
    if settings.EMBEDDINGS_PROVIDER.lower() == "openai":
        return 1536
    return _LOCAL_MODEL_DIMENSIONS.get(settings.LOCAL_EMBEDDING_MODEL, _DEFAULT_LOCAL_DIM)


class EmbeddingDimensionMismatch(Exception):
    """Existing Qdrant collection vector size disagrees with the configured provider's dimension.

    Boot-fatal on purpose (D-19-03) — never silently write wrong-dim vectors.
    """

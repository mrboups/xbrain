"""Idempotent Qdrant collection setup — runs at FastAPI lifespan startup."""

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

from app.config import settings
from app.embedders import EmbeddingDimensionMismatch, get_embedding_dimension

log = structlog.get_logger()

# Single source of truth for the collection name. It MUST come from settings: this module
# CREATES the collection, brain_metrics.py QUERIES it, and admin_wipe.py DELETES it. If any
# of the three disagreed, an operator who set QDRANT_COLLECTION would get a split brain —
# writes landing in one collection while reads and wipes targeted another.
COLLECTION_NAME = settings.QDRANT_COLLECTION


async def ensure_collections() -> None:
    """Create the messages collection if it doesn't exist. Idempotent.

    Phase 19 (EMBED-01): the vector dimension is derived from get_embedding_dimension()
    (the active EMBEDDINGS_PROVIDER) rather than a hardcoded literal — read INSIDE this
    function (not at module import time) so a test that reloads config sees the new value.
    If the collection already exists, its real dimension is compared against the provider's
    expected dimension; a mismatch raises EmbeddingDimensionMismatch (boot-fatal, D-19-03)
    rather than silently writing wrong-dimension vectors into an existing collection.
    """
    vector_size = get_embedding_dimension()
    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    try:
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if COLLECTION_NAME not in names:
            log.info("qdrant_create_collection", name=COLLECTION_NAME, vector_size=vector_size)
            await client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            # Index payload fields used for filter-at-retrieval (team isolation invariant)
            await client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name="team_scope",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name="truth_level",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        else:
            log.info("qdrant_collection_exists", name=COLLECTION_NAME)
            info = await client.get_collection(COLLECTION_NAME)
            vectors = info.config.params.vectors
            # `vectors` is either a single VectorParams (unnamed default vector — what this
            # module always creates) or a Dict[str, VectorParams] (named vectors). Handle both
            # defensively even though only the unnamed shape is ever produced here.
            if isinstance(vectors, dict):
                actual = next(iter(vectors.values())).size if vectors else None
            else:
                actual = vectors.size if vectors is not None else None
            if actual is not None and actual != vector_size:
                raise EmbeddingDimensionMismatch(
                    f"Qdrant collection {COLLECTION_NAME!r} has dimension {actual} but provider "
                    f"{settings.EMBEDDINGS_PROVIDER!r} expects {vector_size}. Re-embed the corpus "
                    f"or use a fresh collection — see docs/embeddings.md. Refusing to write "
                    f"mismatched vectors."
                )
    finally:
        await client.close()

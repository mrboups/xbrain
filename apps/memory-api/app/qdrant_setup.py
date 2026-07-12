"""Idempotent Qdrant collection setup — runs at FastAPI lifespan startup."""

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

from app.config import settings

log = structlog.get_logger()

# Single source of truth for the collection name. It MUST come from settings: this module
# CREATES the collection, brain_metrics.py QUERIES it, and admin_wipe.py DELETES it. If any
# of the three disagreed, an operator who set QDRANT_COLLECTION would get a split brain —
# writes landing in one collection while reads and wipes targeted another.
COLLECTION_NAME = settings.QDRANT_COLLECTION
VECTOR_SIZE = 1536  # OpenAI text-embedding-3-small / placeholder Phase 2


async def ensure_collections() -> None:
    """Create the messages collection if it doesn't exist. Idempotent.

    Phase 1 only sets up the schema — actual embedding writes happen in Phase 2
    when mem0 + the extraction pipeline come online.
    """
    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    try:
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if COLLECTION_NAME not in names:
            log.info("qdrant_create_collection", name=COLLECTION_NAME)
            await client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
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
    finally:
        await client.close()

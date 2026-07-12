"""brain-janitor settings -- pydantic-settings, env-driven.

Phase 11 plan 11-07. All values come from the docker-compose env block; defaults
match the canonical xbrain dev compose so the service boots out-of-the-box for tests.

RETENTION_DAYS=30 is locked by Phase 11 CONTEXT.md -- DO NOT change without a phase-replan.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    QDRANT_URL: str = "http://qdrant:6333"
    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    RETENTION_DAYS: int = 30  # locked by Phase 11 CONTEXT.md
    LOG_LEVEL: str = "INFO"
    # The Qdrant COLLECTION that memory-api creates and writes — canonically "messages"
    # (apps/memory-api/app/qdrant_setup.py). Do NOT confuse it with `memory_items`, which is the
    # POSTGRES TABLE. That conflation is exactly what broke this service: docker-compose passed
    # QDRANT_COLLECTION=memory_items, so the purge below targeted a collection that does not
    # exist, qdrant_purger.py swallowed the error, and vector hard-deletes were a silent no-op.
    # Each Qdrant point uses the memory_items ROW id as its PointStruct.id, so purging by UUID
    # list is exact — that is where the table name legitimately enters, and nowhere else.
    QDRANT_COLLECTION: str = "messages"

    class Config:
        env_file = ".env"

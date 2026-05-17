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
    # memory_items vector collection (canonical name from apps/memory-api/app/qdrant_setup.py
    # and packages/memory-models/xbrain_memory/providers/native_provider.py). Each Qdrant
    # point uses memory_items.id directly as PointStruct.id, so purging by UUID list is exact.
    QDRANT_COLLECTION: str = "messages"

    class Config:
        env_file = ".env"

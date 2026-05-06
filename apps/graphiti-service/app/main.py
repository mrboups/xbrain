"""graphiti-service — FastAPI wrapper around graphiti-core for temporal fact extraction.

Exposes 3 endpoints:
  POST /v1/ingest  — async episode ingestion (returns 202, runs add_episode as background task)
  POST /v1/search  — synchronous semantic search over extracted facts
  GET  /v1/healthz — health check

Architecture note:
  graphiti_client is initialised ONLY inside the lifespan context manager to avoid the
  "Future attached to a different loop" RuntimeError that occurs when Graphiti is
  initialised at module level or inside a thread (graphiti-core maintains persistent
  async Neo4j connections that must live in the same event loop as FastAPI).

  Reference: https://help.getzep.com/graphiti/getting-started/quick-start
             RESEARCH.md Q1 "Async pitfall: event loop conflict"
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import structlog
from fastapi import FastAPI, HTTPException
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.anthropic_client import AnthropicClient, LLMConfig
from graphiti_core.nodes import EpisodeType
from pydantic import BaseModel

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env vars)
# ---------------------------------------------------------------------------
NEO4J_URI         = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER        = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD    = os.environ.get("NEO4J_PASSWORD", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL         = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

# SEMAPHORE_LIMIT: reduces concurrent add_episode calls to avoid hitting Anthropic
# Tier-1 rate limit (50 req/min). Each add_episode makes several LLM calls.
# RESEARCH.md Assumption A5 — default 3 for Haiku Tier 1.
SEMAPHORE_LIMIT = int(os.environ.get("SEMAPHORE_LIMIT", "3"))

# ---------------------------------------------------------------------------
# Module-level state (populated during lifespan — never at import time)
# ---------------------------------------------------------------------------
graphiti_client: Graphiti | None = None
_semaphore: asyncio.Semaphore | None = None


# ---------------------------------------------------------------------------
# Lifespan: init + shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise graphiti_client inside the FastAPI event loop.

    Failure during init is logged but does NOT abort startup — the service will
    start in degraded mode and return 503 on ingest/search until Neo4j is ready.
    This avoids restart loops when the container starts before Neo4j is healthy.
    """
    global graphiti_client, _semaphore
    _semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
    log.info("graphiti.init_start", neo4j_uri=NEO4J_URI, llm_model=LLM_MODEL)
    try:
        graphiti_client = Graphiti(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            llm_client=AnthropicClient(
                config=LLMConfig(
                    api_key=ANTHROPIC_API_KEY,
                    model=LLM_MODEL,
                    small_model=LLM_MODEL,
                )
            ),
            embedder=OpenAIEmbedder(
                config=OpenAIEmbedderConfig(
                    api_key=OPENAI_API_KEY,
                    embedding_model="text-embedding-3-small",
                )
            ),
            cross_encoder=OpenAIRerankerClient(
                config=LLMConfig(
                    api_key=OPENAI_API_KEY,
                    # gpt-4.1-nano preferred; fall back to gpt-4o-mini if unavailable (A3)
                    model="gpt-4.1-nano",
                )
            ),
        )
        await graphiti_client.build_indices_and_constraints()
        log.info("graphiti.init_done")
    except Exception as exc:
        log.warning("graphiti.init_failed", error=str(exc))
        graphiti_client = None  # service stays up in degraded mode

    yield

    if graphiti_client is not None:
        try:
            await graphiti_client.close()
            log.info("graphiti.closed")
        except Exception as exc:
            log.warning("graphiti.close_error", error=str(exc))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="xbrain graphiti-service", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class IngestBody(BaseModel):
    content: str
    group_id: str
    source: str = "memory-api"
    reference_time: datetime | None = None


class SearchBody(BaseModel):
    query: str
    group_ids: list[str]
    max_facts: int = 10


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/v1/ingest", status_code=202)
async def ingest(body: IngestBody):
    """Queue an episode for async ingestion into the Graphiti graph.

    Returns 202 Accepted immediately — add_episode runs as a background asyncio Task.
    add_episode typically takes 3-10 seconds (multiple LLM calls: NER, dedup, summary).
    """
    if graphiti_client is None:
        raise HTTPException(503, "Graphiti not initialised — Neo4j may not be ready yet")
    log.info("graphiti.ingest_queued", group_id=body.group_id, content_len=len(body.content))

    async def _task():
        async with _semaphore:
            try:
                await graphiti_client.add_episode(
                    name=f"ep-{uuid4()}",
                    episode_body=body.content,
                    source=EpisodeType.text,
                    source_description=body.source,
                    reference_time=body.reference_time or datetime.now(timezone.utc),
                    group_id=body.group_id,
                )
                log.info("graphiti.ingest_done", group_id=body.group_id)
            except Exception as exc:
                log.error("graphiti.ingest_error", group_id=body.group_id, error=str(exc))

    asyncio.create_task(_task())
    return {"status": "queued"}


@app.post("/v1/search")
async def search(body: SearchBody):
    """Search extracted facts scoped by group_ids (= team_scopes)."""
    if graphiti_client is None:
        raise HTTPException(503, "Graphiti not initialised — Neo4j may not be ready yet")
    log.info("graphiti.search", group_ids=body.group_ids, query=body.query[:100])
    try:
        results = await graphiti_client.search(
            query=body.query,
            group_ids=body.group_ids,
            num_results=body.max_facts,
        )
        facts = [r.fact for r in results]
        log.info("graphiti.search_done", count=len(facts))
        return {"facts": facts, "count": len(facts)}
    except Exception as exc:
        log.error("graphiti.search_error", error=str(exc))
        raise HTTPException(500, f"Search failed: {exc}")


@app.get("/v1/healthz")
async def healthz():
    """Health check. Returns graphiti=true only if the client is initialised."""
    return {"status": "ok", "graphiti": graphiti_client is not None}

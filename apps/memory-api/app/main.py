"""FastAPI app entrypoint with lifespan startup."""

import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import settings
from app.neo4j_client import close_driver, init_driver
from app.outbox_worker import drain_outbox
from app.qdrant_setup import ensure_collections
from app.routes import (
    audit,
    conversations,
    graph,
    health,
    me,
    memory,
    messages,
    promotions,
    system_prompt,
    teams,
)

logging.basicConfig(level=settings.LOG_LEVEL)
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = structlog.get_logger()
    log.info("memory_api_startup")
    try:
        await ensure_collections()
    except Exception as e:
        log.warning("qdrant_setup_skipped", err=str(e))

    # Init Neo4j driver (graceful degrade if NEO4J_URI/NEO4J_PASSWORD not set)
    await init_driver()

    # Start outbox worker background task (drains neo4j_outbox every 2s)
    _outbox_task = asyncio.create_task(drain_outbox(settings.DATABASE_URL))

    yield

    # Clean shutdown: cancel worker, then close Neo4j driver
    _outbox_task.cancel()
    try:
        await _outbox_task
    except asyncio.CancelledError:
        pass
    await close_driver()
    log.info("memory_api_shutdown")


app = FastAPI(title="xbrain memory-api", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, prefix="/v1", tags=["health"])
app.include_router(me.router, prefix="/v1", tags=["me"])
app.include_router(teams.router, prefix="/v1", tags=["teams"])
app.include_router(conversations.router, prefix="/v1", tags=["conversations"])
app.include_router(messages.router, prefix="/v1", tags=["messages"])
app.include_router(audit.router, prefix="/v1", tags=["audit"])
app.include_router(memory.router, prefix="/v1", tags=["memory"])
app.include_router(promotions.router, prefix="/v1", tags=["promotions"])
app.include_router(graph.router, prefix="/v1", tags=["graph"])
app.include_router(system_prompt.router, prefix="/v1", tags=["system-prompt"])

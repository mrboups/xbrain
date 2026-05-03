"""FastAPI app entrypoint with lifespan startup."""

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import settings
from app.qdrant_setup import ensure_collections
from app.routes import audit, conversations, health, me, messages, teams

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
    yield
    log.info("memory_api_shutdown")


app = FastAPI(title="xbrain memory-api", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, prefix="/v1", tags=["health"])
app.include_router(me.router, prefix="/v1", tags=["me"])
app.include_router(teams.router, prefix="/v1", tags=["teams"])
app.include_router(conversations.router, prefix="/v1", tags=["conversations"])
app.include_router(messages.router, prefix="/v1", tags=["messages"])
app.include_router(audit.router, prefix="/v1", tags=["audit"])

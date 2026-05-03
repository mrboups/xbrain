"""FastAPI entrypoint — boots the checkpointer, registers all graphs, mounts routes."""

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

import app.graphs  # noqa: F401 — side-effect import to register all agents
from app.checkpointer import close_checkpointer, get_checkpointer
from app.config import settings
from app.routes import agents, health

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
    log.info("agent_runtime_startup")
    try:
        await get_checkpointer()  # eager init + create tables if needed
        log.info("checkpointer_ready")
    except Exception as e:
        log.error("checkpointer_init_failed", err=str(e))
        # Don't crash — endpoints will surface a clearer error on first call
    yield
    log.info("agent_runtime_shutdown")
    try:
        await close_checkpointer()
    except Exception as e:
        log.warning("checkpointer_close_failed", err=str(e))


app = FastAPI(title="xbrain agent-runtime", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, tags=["health"])
app.include_router(agents.router, prefix="/v1", tags=["agents"])

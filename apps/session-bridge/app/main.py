"""FastAPI app entrypoint for session-bridge."""
from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI

from app import healthz, routes_chat, routes_ws
from app.config import settings


def _configure_logging() -> None:
    level_name = settings.LOG_LEVEL.upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


app = FastAPI(title="xbrain session-bridge", version="0.1.0")


@app.on_event("startup")
async def _on_startup() -> None:
    _configure_logging()
    log = structlog.get_logger(__name__)
    log.info(
        "session-bridge.started",
        memory_api_url=settings.MEMORY_API_URL,
        bridge_secret_set=bool(settings.BRIDGE_SHARED_SECRET),
        token_ttl_s=settings.TOKEN_TTL_S,
    )


app.include_router(healthz.router)
app.include_router(routes_chat.router)
app.include_router(routes_ws.router)

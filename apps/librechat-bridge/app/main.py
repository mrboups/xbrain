"""Bridge entrypoint: run watcher + heartbeat in parallel."""

import asyncio
import logging

import structlog

from app.config import settings
from app.mongo_watcher import heartbeat_loop, watch_loop

logging.basicConfig(level=settings.LOG_LEVEL)
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)


async def main() -> None:
    await asyncio.gather(watch_loop(), heartbeat_loop())


if __name__ == "__main__":
    asyncio.run(main())

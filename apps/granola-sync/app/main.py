"""granola-sync entry point -- runs the polling loop only (Granola has no webhooks as of 2026-05)."""
import asyncio

import structlog

from app.config import settings
from app.granola_poller import run_poll_loop

log = structlog.get_logger(__name__)


async def main() -> None:
    log.info(
        "granola_sync.boot",
        poll_interval=settings.GRANOLA_POLL_INTERVAL_SECONDS,
        memory_api=settings.MEMORY_API_URL,
    )
    await run_poll_loop(settings.DATABASE_URL)


if __name__ == "__main__":
    asyncio.run(main())

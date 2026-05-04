"""drive-sync entry point -- runs the polling loop and webhook server concurrently."""
import asyncio

import structlog
import uvicorn

from app.config import settings
from app.drive_poller import run_poll_loop
from app.webhook_server import webhook_app

log = structlog.get_logger(__name__)


async def main():
    """Start webhook server (port 8200) and poll loop concurrently."""
    webhook_config = uvicorn.Config(
        webhook_app,
        host="0.0.0.0",
        port=8200,
        log_level=settings.LOG_LEVEL.lower(),
    )
    webhook_server = uvicorn.Server(webhook_config)

    log.info("drive_sync.boot", poll_interval=settings.POLL_INTERVAL_SECONDS)
    await asyncio.gather(
        webhook_server.serve(),
        run_poll_loop(settings.DATABASE_URL),
    )


if __name__ == "__main__":
    asyncio.run(main())

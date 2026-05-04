"""drive-sync entry point -- runs the polling loop."""
import asyncio
import structlog
from app.config import settings
from app.drive_poller import run_poll_loop

log = structlog.get_logger(__name__)

if __name__ == "__main__":
    log.info("drive_sync.boot", poll_interval=settings.POLL_INTERVAL_SECONDS)
    asyncio.run(run_poll_loop(settings.DATABASE_URL))

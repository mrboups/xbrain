"""xbrain logger pipeline: best-effort POST of (user_msg + assistant_msg) to memory-api."""

import asyncio

import structlog

from app.memory_api_client import MemoryApiClient

log = structlog.get_logger()


async def log_exchange(
    *,
    mem: MemoryApiClient,
    sub: str,
    team_scope: str,
    conversation_id: str,
    model: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """Best-effort: log user + assistant messages. Failures are warnings, not exceptions.

    The pipeline must NEVER block the response to Open WebUI on a memory-api hiccup.
    """
    source = f"openwebui:{model}"

    async def safe_post(role: str, content: str) -> None:
        if not content:
            return
        try:
            await mem.post_message(
                sub=sub,
                team_scope=team_scope,
                conversation_id=conversation_id,
                role=role,
                content=content,
                source=source,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "log_exchange_failed",
                role=role,
                err=str(e),
                err_type=type(e).__name__,
                source=source,
            )

    # Both POSTs in parallel; return_exceptions=True ensures one failure doesn't kill the other
    await asyncio.gather(
        safe_post("user", user_content),
        safe_post("assistant", assistant_content),
        return_exceptions=True,
    )

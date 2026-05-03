"""Langfuse instrumentation for agent-runtime.

Usage from routes:
    handler = make_callback_handler(thread_id, agent_name, team_scope, sub)
    config = {"configurable": {...}, "callbacks": [handler] if handler else []}
    async for _ in graph.astream(state, config): ...

If LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is empty, returns None and
the graph runs un-traced — no exceptions, no log spam. Lets us ship before
the user has signed in to Langfuse and generated keys.
"""

from __future__ import annotations

import structlog

from app.config import settings

log = structlog.get_logger()


def _is_configured() -> bool:
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


def make_callback_handler(
    *,
    thread_id: str,
    agent_name: str,
    team_scope: str,
    sub: str,
):
    """Per-run Langfuse CallbackHandler. Returns None when Langfuse is not configured."""
    if not _is_configured():
        return None
    try:
        # Lazy import — keep langfuse out of import-time graph for the no-key case
        from langfuse.callback import CallbackHandler
    except ImportError:
        log.warning("langfuse_sdk_missing")
        return None

    try:
        return CallbackHandler(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
            trace_name=f"agent:{agent_name}",
            session_id=thread_id,
            user_id=sub,
            metadata={"team_scope": team_scope, "agent": agent_name, "thread_id": thread_id},
            tags=[f"team:{team_scope}", f"agent:{agent_name}"],
        )
    except Exception as e:  # noqa: BLE001 — never crash the agent on observability failure
        log.warning("langfuse_callback_init_failed", err=str(e))
        return None

"""Langfuse trace per chat completion — best-effort, never blocks the response.

Used by main.py around the Anthropic / OpenAI provider call. Captures latency,
input/output token counts, model, team_scope, and the OWUI user sub. Failures
to push to Langfuse are logged at WARN and swallowed — observability must
never degrade the user-facing path.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger()


def _is_configured() -> bool:
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


_langfuse_client = None


def _get_client():
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    if not _is_configured():
        return None
    try:
        from langfuse import Langfuse  # lazy import — no SDK cost when unconfigured
    except ImportError:
        log.warning("langfuse_sdk_missing")
        return None
    try:
        _langfuse_client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse_client_init_failed", err=str(e))
        return None
    return _langfuse_client


def trace_chat_completion(
    *,
    sub: str,
    team_scope: str,
    model: str,
    prompt_messages: list[dict[str, Any]],
    response_text: str,
    latency_ms: int,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    error: str | None = None,
) -> None:
    """Push one trace+generation. No-op when Langfuse is not configured."""
    client = _get_client()
    if client is None:
        return
    try:
        trace = client.trace(
            name=f"chat:{model}",
            user_id=sub,
            session_id=None,
            tags=[f"team:{team_scope}", f"model:{model}", "source:openwebui-pipeline"],
            metadata={"team_scope": team_scope, "model": model, "source": "openwebui-pipeline"},
        )
        usage: dict[str, Any] = {}
        if tokens_in is not None:
            usage["input"] = tokens_in
        if tokens_out is not None:
            usage["output"] = tokens_out
        trace.generation(
            name=f"completion:{model}",
            model=model,
            input=prompt_messages,
            output=response_text,
            usage=usage or None,
            metadata={"latency_ms": latency_ms},
            level="ERROR" if error else "DEFAULT",
            status_message=error,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse_trace_failed", err=str(e), model=model)

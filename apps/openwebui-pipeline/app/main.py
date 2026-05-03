"""OpenAI-compatible FastAPI app — Open WebUI sees this as an OpenAI provider.

Proxifies to Anthropic / OpenAI based on requested model, logs each exchange
to memory-api in best-effort mode (failure does not block response).
"""

import asyncio
import logging
import time
from typing import Any

import structlog
from anthropic import AsyncAnthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.memory_api_client import MemoryApiClient
from app.observability import trace_chat_completion
from app.pipelines import ingestion_trigger, promotion_manager, second_opinion_trigger
from app.pipelines.xbrain_logger import log_exchange

logging.basicConfig(level=settings.LOG_LEVEL)
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()

app = FastAPI(title="xbrain openwebui-pipeline", version="0.1.0")
mem = MemoryApiClient()
anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

# Mapping "external model id" → (provider, real model name)
MODEL_MAP: dict[str, tuple[str, str]] = {
    # Claude 4.x (current generation)
    "claude-opus-4-7": ("anthropic", "claude-opus-4-7"),
    "claude-sonnet-4-6": ("anthropic", "claude-sonnet-4-6"),
    "claude-haiku-4-5": ("anthropic", "claude-haiku-4-5-20251001"),
    # Claude 3.x (legacy, kept for compat)
    "claude-3-5-sonnet": ("anthropic", "claude-3-5-sonnet-latest"),
    "claude-3-5-haiku": ("anthropic", "claude-3-5-haiku-latest"),
    "claude-3-opus": ("anthropic", "claude-3-opus-latest"),
    # OpenAI
    "gpt-4o": ("openai", "gpt-4o"),
    "gpt-4o-mini": ("openai", "gpt-4o-mini"),
    "gpt-4-turbo": ("openai", "gpt-4-turbo"),
}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")  # OpenAI clients send extra fields sometimes
    role: str
    content: str


class ChatCompletionsBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    user: str | None = None  # Open WebUI passes user identifier here
    max_tokens: int | None = None
    temperature: float | None = None


def check_auth(authorization: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing api key")
    if authorization.removeprefix("Bearer ") != settings.PIPELINE_API_KEY:
        raise HTTPException(401, "Invalid api key")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models(authorization: str = Header(default="")) -> dict:
    check_auth(authorization)
    return {
        "object": "list",
        "data": [{"id": k, "object": "model", "owned_by": "xbrain"} for k in MODEL_MAP],
    }


def _resolve_principal(
    user_id_hdr: str | None,
    user_email_hdr: str | None,
    body_user: str | None,
) -> str:
    return user_id_hdr or user_email_hdr or body_user or "anonymous"


def _openai_compat_completion(model: str, content: str) -> dict:
    """Wrap a plain string in an OpenAI chat.completion response shape."""
    return {
        "id": "chatcmpl-cmd",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _make_conversation_id(sub: str, messages: list[ChatMessage]) -> str:
    """Phase 1: derive a stable conv id from sub + first user message hash.

    Open WebUI doesn't pass a conv id to the OpenAI provider, so we synthesize one.
    Phase 2: switch to a real conv id passed via custom header from Open WebUI.
    """
    seed = next((m.content for m in messages if m.role == "user"), "")
    return f"openwebui-{sub}-{abs(hash(seed)) % 10**10}"


@app.post("/v1/chat/completions")
async def chat(
    body: ChatCompletionsBody,
    authorization: str = Header(default=""),
    x_openwebui_user_id: str | None = Header(default=None, alias="X-OpenWebUI-User-Id"),
    x_openwebui_user_email: str | None = Header(default=None, alias="X-OpenWebUI-User-Email"),
    x_team_scope: str | None = Header(default=None, alias="X-Team-Scope"),
):
    check_auth(authorization)
    if body.model not in MODEL_MAP:
        raise HTTPException(400, f"unknown model {body.model}")

    provider, real_model = MODEL_MAP[body.model]
    user_message = next((m.content for m in reversed(body.messages) if m.role == "user"), "")
    sub = _resolve_principal(x_openwebui_user_id, x_openwebui_user_email, body.user)
    team_scope = x_team_scope or settings.PIPELINE_DEFAULT_TEAM_SCOPE
    conversation_id = _make_conversation_id(sub, body.messages)

    # Slash-command intercept — short-circuits the LLM path.
    # Order is "most-likely-first" for cheap regex bail-outs.
    # Wrapped in try/except so unrecognized commands fall through to LLM AND
    # commands targeting Phase 2 services that aren't deployed yet (agent-runtime,
    # promotions API) return a clean message instead of crashing the request.
    if user_message.lstrip().startswith("/"):
        try:
            cmd_response = await ingestion_trigger.try_handle(
                user_message=user_message, user_sub=sub, team_scope=team_scope,
            )
            if cmd_response is None:
                cmd_response = await second_opinion_trigger.try_handle(
                    user_message=user_message, user_sub=sub, team_scope=team_scope,
                )
            if cmd_response is None:
                cmd_response = await promotion_manager.try_handle(
                    mem=mem,
                    user_message=user_message,
                    user_sub=sub,
                    user_email=x_openwebui_user_email or sub,
                    user_name=x_openwebui_user_id,
                    team_scope=team_scope,
                )
            if cmd_response is not None:
                return _openai_compat_completion(body.model, cmd_response)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "slash_command_failed",
                err=str(e),
                cmd=user_message.lstrip().split()[0][:60],
                sub=sub,
            )
            return _openai_compat_completion(
                body.model,
                "⚠️ **Cette commande n'est pas encore disponible.**\n\n"
                "Les commandes `/ingest`, `/second-opinion`, `/approve-thread`, "
                "`/reject-thread`, `/promotions-pending`, `/propose`, `/approve`, "
                "`/reject` requièrent les services xbrain Phase 2 "
                "(`agent-runtime` + endpoints promotions du `memory-api`).\n\n"
                "Ils seront actifs une fois Phase 2 déployée. En attendant, "
                "envoie un message normal pour parler à l'LLM."
            )

    log.info(
        "chat_request",
        model=body.model,
        provider=provider,
        sub=sub,
        team_scope=team_scope,
        stream=body.stream,
    )

    if provider == "anthropic":
        if anthropic_client is None:
            raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
        return await _handle_anthropic(
            body, real_model, sub, team_scope, conversation_id, user_message
        )

    if provider == "openai":
        if openai_client is None:
            raise HTTPException(500, "OPENAI_API_KEY not configured")
        return await _handle_openai(
            body, real_model, sub, team_scope, conversation_id, user_message
        )

    raise HTTPException(500, f"provider not implemented: {provider}")


async def _handle_anthropic(
    body: ChatCompletionsBody,
    real_model: str,
    sub: str,
    team_scope: str,
    conversation_id: str,
    user_message: str,
) -> Any:
    # Strip system messages out of `messages` and pass them as `system` parameter to Anthropic
    system_msgs = [m.content for m in body.messages if m.role == "system"]
    chat_msgs = [{"role": m.role, "content": m.content} for m in body.messages if m.role != "system"]
    system_param = "\n".join(system_msgs) if system_msgs else None
    max_tokens = body.max_tokens or 4096

    if body.stream:
        async def gen():
            full_response: list[str] = []
            kwargs: dict[str, Any] = {"model": real_model, "max_tokens": max_tokens, "messages": chat_msgs}
            if system_param:
                kwargs["system"] = system_param
            assert anthropic_client is not None
            stream_start = time.time()
            async with anthropic_client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    full_response.append(chunk)
                    # OpenAI SSE format
                    yield f'data: {{"choices":[{{"delta":{{"content":{chunk!r}}}}}]}}\n\n'
            yield "data: [DONE]\n\n"
            stream_latency_ms = int((time.time() - stream_start) * 1000)
            assistant_content = "".join(full_response)
            asyncio.create_task(
                log_exchange(
                    mem=mem,
                    sub=sub,
                    team_scope=team_scope,
                    conversation_id=conversation_id,
                    model=body.model,
                    user_content=user_message,
                    assistant_content=assistant_content,
                )
            )
            trace_chat_completion(
                sub=sub, team_scope=team_scope, model=body.model,
                prompt_messages=chat_msgs, response_text=assistant_content,
                latency_ms=stream_latency_ms,
            )

        return StreamingResponse(gen(), media_type="text/event-stream")

    assert anthropic_client is not None
    kwargs: dict[str, Any] = {"model": real_model, "max_tokens": max_tokens, "messages": chat_msgs}
    if system_param:
        kwargs["system"] = system_param
    start = time.time()
    r = await anthropic_client.messages.create(**kwargs)
    latency_ms = int((time.time() - start) * 1000)
    assistant_content = "".join(b.text for b in r.content if b.type == "text")
    usage = getattr(r, "usage", None)
    trace_chat_completion(
        sub=sub, team_scope=team_scope, model=body.model,
        prompt_messages=chat_msgs, response_text=assistant_content,
        latency_ms=latency_ms,
        tokens_in=getattr(usage, "input_tokens", None) if usage else None,
        tokens_out=getattr(usage, "output_tokens", None) if usage else None,
    )
    asyncio.create_task(
        log_exchange(
            mem=mem,
            sub=sub,
            team_scope=team_scope,
            conversation_id=conversation_id,
            model=body.model,
            user_content=user_message,
            assistant_content=assistant_content,
        )
    )
    return {
        "id": f"chatcmpl-{r.id}",
        "object": "chat.completion",
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": assistant_content},
                "finish_reason": "stop",
            }
        ],
    }


async def _handle_openai(
    body: ChatCompletionsBody,
    real_model: str,
    sub: str,
    team_scope: str,
    conversation_id: str,
    user_message: str,
) -> Any:
    msgs = [{"role": m.role, "content": m.content} for m in body.messages]
    assert openai_client is not None
    if body.stream:
        async def gen():
            full_response: list[str] = []
            stream_start = time.time()
            stream = await openai_client.chat.completions.create(
                model=real_model, messages=msgs, stream=True
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    full_response.append(delta)
                    yield f'data: {{"choices":[{{"delta":{{"content":{delta!r}}}}}]}}\n\n'
            yield "data: [DONE]\n\n"
            stream_latency_ms = int((time.time() - stream_start) * 1000)
            assistant_content = "".join(full_response)
            asyncio.create_task(
                log_exchange(
                    mem=mem,
                    sub=sub,
                    team_scope=team_scope,
                    conversation_id=conversation_id,
                    model=body.model,
                    user_content=user_message,
                    assistant_content=assistant_content,
                )
            )
            trace_chat_completion(
                sub=sub, team_scope=team_scope, model=body.model,
                prompt_messages=msgs, response_text=assistant_content,
                latency_ms=stream_latency_ms,
            )

        return StreamingResponse(gen(), media_type="text/event-stream")

    start = time.time()
    r = await openai_client.chat.completions.create(model=real_model, messages=msgs)
    latency_ms = int((time.time() - start) * 1000)
    assistant_content = r.choices[0].message.content or ""
    usage = getattr(r, "usage", None)
    trace_chat_completion(
        sub=sub, team_scope=team_scope, model=body.model,
        prompt_messages=msgs, response_text=assistant_content,
        latency_ms=latency_ms,
        tokens_in=getattr(usage, "prompt_tokens", None) if usage else None,
        tokens_out=getattr(usage, "completion_tokens", None) if usage else None,
    )
    asyncio.create_task(
        log_exchange(
            mem=mem,
            sub=sub,
            team_scope=team_scope,
            conversation_id=conversation_id,
            model=body.model,
            user_content=user_message,
            assistant_content=assistant_content,
        )
    )
    return {
        "id": r.id,
        "object": "chat.completion",
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": assistant_content},
                "finish_reason": "stop",
            }
        ],
    }

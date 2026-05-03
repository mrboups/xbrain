"""OpenAI-compatible FastAPI app — Open WebUI sees this as an OpenAI provider.

Proxifies to Anthropic / OpenAI based on requested model, logs each exchange
to memory-api in best-effort mode (failure does not block response).
"""

import asyncio
import logging
from typing import Any

import structlog
from anthropic import AsyncAnthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.memory_api_client import MemoryApiClient
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
    "claude-3-5-sonnet": ("anthropic", "claude-3-5-sonnet-latest"),
    "claude-3-5-haiku": ("anthropic", "claude-3-5-haiku-latest"),
    "claude-3-opus": ("anthropic", "claude-3-opus-latest"),
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
            async with anthropic_client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    full_response.append(chunk)
                    # OpenAI SSE format
                    yield f'data: {{"choices":[{{"delta":{{"content":{chunk!r}}}}}]}}\n\n'
            yield "data: [DONE]\n\n"
            asyncio.create_task(
                log_exchange(
                    mem=mem,
                    sub=sub,
                    team_scope=team_scope,
                    conversation_id=conversation_id,
                    model=body.model,
                    user_content=user_message,
                    assistant_content="".join(full_response),
                )
            )

        return StreamingResponse(gen(), media_type="text/event-stream")

    assert anthropic_client is not None
    kwargs: dict[str, Any] = {"model": real_model, "max_tokens": max_tokens, "messages": chat_msgs}
    if system_param:
        kwargs["system"] = system_param
    r = await anthropic_client.messages.create(**kwargs)
    assistant_content = "".join(b.text for b in r.content if b.type == "text")
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
            stream = await openai_client.chat.completions.create(
                model=real_model, messages=msgs, stream=True
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    full_response.append(delta)
                    yield f'data: {{"choices":[{{"delta":{{"content":{delta!r}}}}}]}}\n\n'
            yield "data: [DONE]\n\n"
            asyncio.create_task(
                log_exchange(
                    mem=mem,
                    sub=sub,
                    team_scope=team_scope,
                    conversation_id=conversation_id,
                    model=body.model,
                    user_content=user_message,
                    assistant_content="".join(full_response),
                )
            )

        return StreamingResponse(gen(), media_type="text/event-stream")

    r = await openai_client.chat.completions.create(model=real_model, messages=msgs)
    assistant_content = r.choices[0].message.content or ""
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

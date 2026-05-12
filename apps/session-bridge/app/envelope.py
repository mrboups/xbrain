"""Pydantic v2 models for the WS envelope spoken by the extension and the bridge."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class RegisterFrame(BaseModel):
    """Extension → bridge handshake.

    Sent once right after WS open. Triggers a fire-and-forget upsert into
    memory-api's `user_external_sessions` table so the popup (plan 09-05) and
    other observability surfaces know the user is connected.
    """

    type: Literal["register"]
    provider: str
    extension_id: str | None = None
    email_logged: str | None = None
    org_id: str | None = None


class ChatRequestFrame(BaseModel):
    """Bridge → extension. Pushed when LibreChat hits /v1/chat/completions."""

    type: Literal["chat_request"]
    request_id: str
    openai_body: dict


class ChunkFrame(BaseModel):
    """Extension → bridge. One streamed chunk (already translated to OpenAI SSE shape)."""

    type: Literal["chunk"]
    request_id: str
    openai_chunk: dict


class EndFrame(BaseModel):
    """Extension → bridge. Stream complete — bridge yields `data: [DONE]\\n\\n`."""

    type: Literal["end"]
    request_id: str


class ErrorFrame(BaseModel):
    """Extension → bridge. Error during the upstream claude.ai fetch."""

    type: Literal["error"]
    request_id: str
    detail: dict


class PingFrame(BaseModel):
    """Extension → bridge. Keepalive; ignored server-side."""

    type: Literal["ping"]
    ts: int


# Discriminated union for everything the bridge accepts from the extension.
IncomingFrame = Annotated[
    Union[RegisterFrame, ChunkFrame, EndFrame, ErrorFrame, PingFrame],
    Field(discriminator="type"),
]


class RegisterAck(BaseModel):
    """Bridge → extension. Response to a RegisterFrame."""

    type: Literal["register_ack"] = "register_ack"
    ok: bool
    error: str | None = None

"""POST /oauth/register — Dynamic Client Registration (RFC 7591).

Mounted WITHOUT the /v1 prefix (public path /oauth/register). Accepts public
clients (no client_secret returned). Claude.ai calls this to register its
connector with ``redirect_uris=["https://claude.ai/api/mcp/auth_callback"]``.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import oauth_store
from app.deps import get_session

router = APIRouter()
log = structlog.get_logger(__name__)


class RegisterClientBody(BaseModel):
    client_name: str | None = None
    redirect_uris: list[str] = Field(..., min_length=1)
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None


@router.post("/oauth/register", status_code=201)
async def register_client(
    body: RegisterClientBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await oauth_store.register_client(
            session,
            client_name=body.client_name,
            redirect_uris=body.redirect_uris,
            grant_types=body.grant_types,
        )
        await session.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    log.info("oauth.register", client_id=result["client_id"], name=body.client_name)
    return result

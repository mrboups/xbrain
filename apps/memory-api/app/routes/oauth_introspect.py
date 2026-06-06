"""POST /oauth/introspect — RFC 7662 token introspection (internal only).

Mounted WITHOUT the /v1 prefix (public path /oauth/introspect). This endpoint
is called by mcp-brain to validate an ``oat_`` access token. It is gated by the
shared ``BRIDGE_SHARED_SECRET`` presented as the ``X-Internal-Secret`` header
and compared in constant time (hmac.compare_digest). A missing or wrong secret
returns 401 WITHOUT calling the store and WITHOUT leaking any token claims
(threat T-glo-05). It does NOT require the bearer it is validating.

Accepts the ``token`` either as a JSON body field or as form-encoded data
(RFC 7662 uses form encoding; we accept both for robustness).
"""
from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import oauth_store
from app.config import settings
from app.deps import get_session

router = APIRouter()
log = structlog.get_logger(__name__)


def _check_internal_secret(provided: str | None) -> None:
    """Constant-time gate. 401 (no claims) on missing/empty/wrong secret."""
    expected = settings.BRIDGE_SHARED_SECRET
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        # Do NOT call the store, do NOT leak claims.
        raise HTTPException(401, "Unauthorized")


async def _extract_token(request: Request) -> str | None:
    """Read `token` from a JSON body or form-encoded body."""
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            data = await request.json()
        except Exception:
            return None
        if isinstance(data, dict):
            return data.get("token")
        return None
    # Default: form-encoded (the RFC 7662 wire format).
    try:
        form = await request.form()
    except Exception:
        return None
    val = form.get("token")
    return val if isinstance(val, str) else None


@router.post("/oauth/introspect")
async def introspect(
    request: Request,
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _check_internal_secret(x_internal_secret)
    token = await _extract_token(request)
    if not token:
        # Credentialed caller, but no token to inspect → inactive (RFC 7662).
        return {"active": False}
    result = await oauth_store.introspect_token(session, token)
    return result

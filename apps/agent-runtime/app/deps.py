"""FastAPI dependencies — auth + team scope guard for agent-runtime.

Mirrors memory-api's deps but with no DB session (we go through memory-api HTTP).
"""

from typing import Any

from fastapi import Depends, Header, HTTPException

from app.auth import verify_bridge_jwt, verify_google_id_token
from app.config import settings


async def get_current_principal(
    authorization: str = Header(..., description="Bearer <jwt>"),
) -> dict[str, Any]:
    """Resolve the JWT to either a user (Google OIDC) or a service principal (bridge)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.removeprefix("Bearer ")

    # Try Google ID token first.
    if settings.GOOGLE_CLIENT_ID:
        try:
            claims = await verify_google_id_token(token, settings.GOOGLE_CLIENT_ID)
            return {
                "kind": "user",
                "claims": claims,
                "sub": claims["sub"],
                "email": claims.get("email", ""),
            }
        except Exception:
            pass  # fall through to bridge

    try:
        claims = verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)
    except Exception as e:
        raise HTTPException(401, "Invalid token") from e
    return {
        "kind": "bridge",
        "claims": claims,
        "sub": claims.get("sub"),
        "team_scope": claims.get("team_scope"),
    }


async def get_team_scope(
    principal: dict[str, Any] = Depends(get_current_principal),
    x_team_scope: str = Header(..., alias="X-Team-Scope"),
) -> str:
    """Verify the principal is allowed to operate within X-Team-Scope.

    Bridge tokens must carry the team_scope they're scoped to.
    User tokens are accepted optimistically here — the actual membership check is
    enforced when memory-api receives the downstream call (single source of truth).
    """
    if principal["kind"] == "bridge":
        if principal["team_scope"] != x_team_scope:
            raise HTTPException(403, "Bridge JWT team_scope mismatch with header")
    return x_team_scope

"""FastAPI dependencies: DB session, current user, team scope guard, memory provider."""

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryProvider

from app.auth import verify_bridge_jwt, verify_google_id_token
from app.config import settings
from app.db.session import async_session_factory
from app.repos.teams import get_membership
from app.repos.users import get_or_create_user


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as s:
        yield s


async def get_current_principal(
    authorization: str = Header(..., description="Bearer <jwt>"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Resolve the JWT to either a user (Google OIDC) or a service principal (bridge)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.removeprefix("Bearer ")

    # Try Google ID token first.
    if settings.GOOGLE_CLIENT_ID:
        try:
            claims = await verify_google_id_token(token, settings.GOOGLE_CLIENT_ID)
            user = await get_or_create_user(
                session,
                source_user_id=claims["sub"],
                email=claims.get("email", ""),
                display_name=claims.get("name") or claims.get("given_name"),
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": claims,
                "sub": claims["sub"],
            }
        except Exception:
            # Fall through to bridge JWT attempt
            pass

    # Try bridge service JWT.
    try:
        claims = verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)
        return {
            "kind": "bridge",
            "claims": claims,
            "sub": claims.get("sub"),
            "team_scope": claims.get("team_scope"),
        }
    except Exception as e:
        raise HTTPException(401, "Invalid token") from e


async def get_team_scope(
    principal: dict[str, Any] = Depends(get_current_principal),
    x_team_scope: str = Header(..., alias="X-Team-Scope"),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Verify the principal is allowed to operate within X-Team-Scope. Returns the slug."""
    if principal["kind"] == "bridge":
        if principal["team_scope"] != x_team_scope:
            raise HTTPException(403, "Bridge JWT team_scope mismatch with header")
        return x_team_scope

    user = principal["user"]
    membership = await get_membership(session, user_id=user.id, team_slug=x_team_scope)
    if membership is None:
        raise HTTPException(403, f"Not a member of team {x_team_scope}")
    return x_team_scope


# === Memory provider singleton (lazy-loaded based on env MEMORY_BACKEND) ===

_memory_provider_singleton: MemoryProvider | None = None


def _build_provider() -> MemoryProvider:
    backend = settings.MEMORY_BACKEND.lower()
    if backend == "mem0":
        from xbrain_memory.providers.mem0_provider import Mem0Provider
        return Mem0Provider(
            qdrant_url=settings.QDRANT_URL,
            openai_api_key=settings.OPENAI_API_KEY,
        )
    if backend == "native":
        from xbrain_memory.providers.native_provider import NativeProvider
        from app.embedders import openai_embedder
        # asyncpg DSN format (no SQLAlchemy driver prefix)
        pg_dsn = settings.DATABASE_URL.replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        return NativeProvider(
            pg_dsn=pg_dsn,
            qdrant_url=settings.QDRANT_URL,
            embedder=openai_embedder,
            qdrant_api_key=settings.QDRANT_API_KEY,
        )
    # Default: stub (no external deps, in-process)
    from xbrain_memory.providers.native_stub import NativeStubProvider
    return NativeStubProvider()


def get_memory_provider() -> MemoryProvider:
    global _memory_provider_singleton
    if _memory_provider_singleton is None:
        _memory_provider_singleton = _build_provider()
    return _memory_provider_singleton

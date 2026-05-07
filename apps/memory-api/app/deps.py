"""FastAPI dependencies: DB session, current user, team scope guard, memory provider."""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryProvider

from app.auth import check_github_org_membership, verify_bridge_jwt, verify_google_id_token
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
                # D7: Google-only users always get team access (no GitHub org check)
                "github_is_org_member": None,
            }
        except Exception:
            # Fall through to GitHub OAuth token attempt
            pass

    # Try GitHub OAuth token (tokens start with "gho_" prefix).
    # D4: GitHub Org members get full team access; non-members get team_scope=None.
    if settings.GITHUB_API_PAT and token.startswith("gho_"):
        try:
            gh = await check_github_org_membership(
                token, settings.GITHUB_ORG, settings.GITHUB_API_PAT
            )
            # Use GitHub numeric ID as the stable source_user_id (login can change)
            github_source_id = f"github:{gh['login']}"
            user = await get_or_create_user(
                session,
                source_user_id=github_source_id,
                email=gh.get("email") or f"{gh['login']}@github.noreply",
                display_name=gh.get("name") or gh["login"],
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": {"sub": github_source_id, "login": gh["login"]},
                "sub": github_source_id,
                "github_is_org_member": gh["is_org_member"],
                # team_scope enforcement: non-members cannot access team routes (T-05-02-02)
                # The get_team_scope dependency will reject them via membership check.
            }
        except Exception:
            # Fall through to bridge JWT attempt
            pass

    # Try bridge service JWT.
    try:
        claims = verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)
        # OpenWebUI Pipeline can act on behalf of an OWUI user via `acting_user_sub`+`acting_user_email`.
        # The pipeline trust boundary: the JWT signature proves the pipeline issued it;
        # the pipeline is the auth source for OWUI user identity.
        acting_sub = claims.get("acting_user_sub")
        acting_email = claims.get("acting_user_email")
        if claims.get("iss") == "openwebui-pipeline" and acting_sub and acting_email:
            user = await get_or_create_user(
                session,
                source_user_id=acting_sub,
                email=acting_email,
                display_name=claims.get("acting_user_name"),
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": claims,
                "sub": acting_sub,
            }
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

    # T-05-02-02: GitHub users who are not org members cannot access team-scoped routes.
    # github_is_org_member=False means non-member; None means Google user (D7 — always allowed).
    if principal.get("github_is_org_member") is False:
        raise HTTPException(403, "GitHub account is not a member of the required org")

    user = principal["user"]
    membership = await get_membership(session, user_id=user.id, team_slug=x_team_scope)
    if membership is None:
        raise HTTPException(403, f"Not a member of team {x_team_scope}")
    return x_team_scope


async def require_paid_tier(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
) -> str:
    """Raises 403 if the team's plan is 'starter'. Used for /v1/crm/* and /v1/tasks/* (D2).

    The dependency chain (require_paid_tier → get_team_scope → get_current_principal)
    means membership and authentication are already validated. This adds the plan check.
    """
    row = (await session.execute(
        sa.text("SELECT plan FROM teams WHERE slug = :slug"),
        {"slug": team_scope},
    )).fetchone()
    if row is None or row.plan == "starter":
        raise HTTPException(
            status_code=403,
            detail="CRM and task tracking require a Team or Enterprise plan",
        )
    return team_scope


def _user_id_from_principal(principal: dict[str, Any]) -> UUID | None:
    """Extract the user.id from a resolved principal, or None for bridge JWTs."""
    user = principal.get("user")
    if user is None:
        return None
    return user.id


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

"""FastAPI dependencies: DB session, current user, team scope guard, memory provider."""

import asyncio
import hashlib
import types
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryProvider

from app.auth import (
    check_github_org_membership,
    verify_bridge_jwt,
    verify_google_access_token,
    verify_google_id_token,
)
from app.config import settings
from app.db.session import async_session_factory
from app.repos.teams import get_membership
from app.repos.users import get_or_create_user


async def _touch_token(token_id: str) -> None:
    """Fire-and-forget update of last_used_at for an API token."""
    try:
        async with async_session_factory() as s:
            await s.execute(
                sa.text("UPDATE user_api_tokens SET last_used_at = now() WHERE id = :id"),
                {"id": token_id},
            )
            await s.commit()
    except Exception:
        pass


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

    # Try Google ID token first (JWT shape: header.payload.signature).
    if settings.GOOGLE_CLIENT_ID and token.count(".") == 2:
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

    # Try Google OAuth2 access token (opaque, no JWT shape). Used by the
    # Chrome extension's chrome.identity.getAuthToken flow — silent when the
    # user is already signed into Chrome.
    if (
        not token.startswith("xbt_")
        and not token.startswith("gho_")
        and "." not in token  # access tokens are opaque; bridge JWTs / ID tokens have dots
    ):
        try:
            claims = await verify_google_access_token(token)
            user = await get_or_create_user(
                session,
                source_user_id=claims["sub"],
                email=claims.get("email") or "",
                display_name=claims.get("name") or claims.get("given_name"),
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": claims,
                "sub": claims["sub"],
                "github_is_org_member": None,
            }
        except Exception:
            # Fall through to other auth methods
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
            # Phase 10 GHA-06 — if this user row was soft-merged into another, redirect.
            from app.repos.users import follow_merge_pointer  # local import to keep top-level minimal
            user = await follow_merge_pointer(session, user)
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

    # Try personal API token (tokens start with "xbt_" prefix).
    if token.startswith("xbt_"):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = (await session.execute(sa.text("""
            SELECT t.id, t.user_id, t.team_scope,
                   u.source_user_id, u.email, u.display_name,
                   u.github_username, u.github_id, u.merged_into_user_id
            FROM user_api_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = :hash AND t.revoked_at IS NULL
        """), {"hash": token_hash})).mappings().fetchone()
        if row is None:
            raise HTTPException(401, "Invalid or revoked API token")
        # Phase 10 GHA-06 — follow merge pointer if the token's user has been merged.
        # The row's user_id may point at an orphan if a merge happened after this
        # token was minted (merge_user_rows re-parents user_api_tokens.user_id,
        # but a token row read before the merge can still resolve to the orphan
        # join row if the request raced). Re-resolve the survivor row in-band.
        if row["merged_into_user_id"] is not None:
            row = (await session.execute(sa.text("""
                SELECT t.id, t.user_id, t.team_scope,
                       u.source_user_id, u.email, u.display_name,
                       u.github_username, u.github_id, u.merged_into_user_id
                FROM user_api_tokens t
                JOIN users u ON u.id = :survivor_id
                WHERE t.id = :token_id
            """), {
                "survivor_id": row["merged_into_user_id"],
                "token_id": row["id"],
            })).mappings().fetchone()
            if row is None:
                raise HTTPException(401, "Token survivor row missing")
        # Update last_used_at async (fire-and-forget — non-blocking)
        asyncio.create_task(_touch_token(str(row["id"])))
        user = types.SimpleNamespace(
            id=row["user_id"],
            source_user_id=row["source_user_id"],
            email=row["email"],
            display_name=row["display_name"],
            github_username=row["github_username"],
            github_id=row["github_id"],
        )
        return {
            "kind": "user_api_token",
            "user": user,
            "sub": row["source_user_id"],
            "api_token_team_scope": row["team_scope"],
            "github_is_org_member": None,
        }

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
        # LibreChat onboarding tokens: iss=librechat-onboarding, email=user email
        if claims.get("iss") == "librechat-onboarding" and claims.get("email"):
            raw_gh_id = claims.get("github_id")
            github_id = int(raw_gh_id) if raw_gh_id else None
            user = await get_or_create_user(
                session,
                source_user_id=f"email:{claims['email']}",
                email=claims["email"],
                display_name=None,
                github_id=github_id,
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": claims,
                "sub": f"email:{claims['email']}",
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

    # API tokens are scoped to exactly one team — validate that the requested scope matches.
    # Phase 10 — multi-team tokens (minted by /v1/auth/github/signin) carry the empty-string
    # sentinel '' in user_api_tokens.team_scope. For those, skip the scope-match guard and
    # fall through to the team_members membership check below (the user must still be a
    # member of the requested team to operate within it).
    if principal["kind"] == "user_api_token":
        scope = principal.get("api_token_team_scope")
        if scope and scope != x_team_scope:
            raise HTTPException(403, "API token team_scope mismatch with X-Team-Scope header")
        if scope:
            # Single-team scoped token — match confirmed, return.
            return x_team_scope
        # Multi-team token: fall through to membership check below.

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


def _is_admin(principal: dict[str, Any]) -> bool:
    """Return True for bridge service JWTs (kind=service/bridge) or listed admin subs.

    Bridge tokens (kind='bridge') are emitted by internal services and implicitly
    trusted as admin for configuration endpoints. Real-user tokens (kind='user')
    require the caller's sub to appear in ADMIN_USER_SUBS.
    """
    if principal.get("kind") in ("service", "bridge"):
        return True
    sub = principal.get("sub", "")
    admin_subs = [s.strip() for s in (settings.ADMIN_USER_SUBS or "").split(",") if s.strip()]
    return sub in admin_subs


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

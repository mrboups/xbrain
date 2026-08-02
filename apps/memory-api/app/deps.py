"""FastAPI dependencies: DB session, current user, team scope guard, memory provider."""

import asyncio
import hashlib
import types
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from fastapi import Depends, Header, HTTPException, Request
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
from app.services import token_capabilities


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


def _assert_capability_allows(request: Request | None, capability: str | None, token_id: Any) -> None:
    """Refuse a narrowed token anywhere outside its capability's allow-list.

    This lives in ``get_current_principal`` — the ONE chokepoint every
    authenticated route passes through — rather than in a per-route dependency,
    because a per-route check is a check somebody forgets. A scoped token
    reaching a route added next year must fail without anyone having thought
    about it.

    Fails closed when ``request`` is absent: a caller that resolved a principal
    outside the HTTP path cannot prove which endpoint is being reached, so a
    restricted token is refused rather than waved through.
    """
    if capability is None:
        return
    path = request.url.path if request is not None else None
    if token_capabilities.is_path_allowed(capability, path):
        return
    log_path = path or "<no-request-context>"
    structlog.get_logger(__name__).warning(
        "auth.scoped_token_refused",
        capability=capability,
        path=log_path,
        token_id=str(token_id),
    )
    raise HTTPException(
        403,
        f"This token is restricted to {capability} and cannot be used on this endpoint.",
    )


async def get_current_principal(
    request: Request,
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
    # NOTE the xbi_ exclusion below is not cosmetic: without it a scoped import
    # token would be POSTed to Google's tokeninfo endpoint on every request —
    # leaking one of our credentials to a third party before failing.
    if (
        not token.startswith("xbt_")
        and not token.startswith(token_capabilities.SCOPED_TOKEN_PREFIXES)
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

    # Phase 12 (Plan 12-06) — GitHub App user-to-server token. Same logical
    # principal as the legacy gho_ branch below, but with transparent refresh
    # handling AND O(log n) lookup via the indexed token_hash column.
    if token.startswith("ghu_"):
        try:
            from sqlalchemy import select

            from app.models.user import User as UserModel
            from app.services.github_user_token import (
                GitHubReauthRequired,
                refresh_user_token_if_needed,
            )
            from app.services.token_crypto import (
                TokenCryptoInvalid,
                decrypt_token,
                token_lookup_hash,
            )

            # REVISION 2 (M-5 fix) — indexed hash lookup instead of O(n)
            # decrypt-all-rows scan. HMAC-SHA256(FERNET_KEY, plaintext) is
            # deterministic, the index is partial (WHERE NOT NULL — skips
            # legacy rows). Collision space is 2^256.
            hashed = token_lookup_hash(token)
            candidate_user = (await session.execute(
                select(UserModel).where(
                    UserModel.github_access_token_hash == hashed
                )
            )).scalar_one_or_none()
            if candidate_user is None:
                raise HTTPException(401, "Unknown GitHub user token")

            # Defense in depth — verify the decrypted plaintext matches.
            # Catches the implausible-but-possible case where the hash leaked
            # separately from FERNET_KEY, or DB corruption.
            try:
                if decrypt_token(candidate_user.github_access_token_enc) != token:
                    raise HTTPException(401, "GitHub user token mismatch")
            except TokenCryptoInvalid as exc:
                raise HTTPException(
                    401, "GitHub user token corrupt — re-authorize required"
                ) from exc

            # Transparent refresh before returning the principal.
            try:
                await refresh_user_token_if_needed(session, candidate_user)
            except GitHubReauthRequired as exc:
                raise HTTPException(
                    401, "GitHub re-authorization required"
                ) from exc

            # Phase 10 GHA-06 — follow merge pointer if this user row was
            # soft-merged into another.
            from app.repos.users import follow_merge_pointer
            candidate_user = await follow_merge_pointer(session, candidate_user)
            await session.commit()

            return {
                "kind": "user",
                "user": candidate_user,
                "claims": {
                    "sub": f"github:{candidate_user.github_username}",
                    "login": candidate_user.github_username,
                },
                "sub": f"github:{candidate_user.github_username}",
                # ghu_ branch trusts the App OAuth flow (user.id is the
                # canonical principal); team_scope is enforced by get_team_scope.
                "github_is_org_member": None,
            }
        except HTTPException:
            raise
        except Exception:
            # Fall through to other auth methods on unexpected failures.
            pass

    # Try GitHub OAuth token (tokens start with "gho_" prefix).
    # D4: GitHub Org members get full team access; non-members get team_scope=None.
    #
    # Phase 12 (Plan 12-04) — legacy gho_ branch retained for transitional
    # compatibility ONLY (Phase 5 LibreChat OAuth App tokens; Plan 12-06
    # adds the ghu_ parallel branch above). The membership check no longer needs a
    # PAT — installation tokens are minted internally via check_github_org_membership.
    if token.startswith("gho_"):
        try:
            gh = await check_github_org_membership(
                session, token, settings.GITHUB_ORG
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
            # Phase 12 (Plan 12-04) — INSTALL_REQUIRED is treated as "signed in
            # but no team access yet". The get_team_scope guard still rejects
            # non-members via the team_members membership check below; the
            # install_required flag is surfaced so route handlers (Plan 12-06
            # SigninGithubOut) can hand the install URL to the frontend. We do
            # NOT 403 here — Phase 12 UX policy.
            return {
                "kind": "user",
                "user": user,
                "claims": {"sub": github_source_id, "login": gh["login"]},
                "sub": github_source_id,
                "github_is_org_member": gh["is_org_member"],
                "github_install_required": gh.get("install_required", False),
                # team_scope enforcement: non-members cannot access team routes (T-05-02-02)
                # The get_team_scope dependency will reject them via membership check.
            }
        except Exception:
            # Fall through to bridge JWT attempt
            pass

    # Try personal API token. "xbt_" is the unrestricted personal token; the
    # prefixes in token_capabilities.SCOPED_TOKEN_PREFIXES (today: "xbi_" for
    # transcript import) live in the SAME table and resolve to the same kind of
    # principal — the difference is the `capability` column, enforced below.
    if token.startswith(("xbt_", *token_capabilities.SCOPED_TOKEN_PREFIXES)):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = (await session.execute(sa.text("""
            SELECT t.id, t.user_id, t.team_scope, t.capability,
                   u.source_user_id, u.email, u.display_name,
                   u.github_username, u.github_id, u.merged_into_user_id
            FROM user_api_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = :hash AND t.revoked_at IS NULL
        """), {"hash": token_hash})).mappings().fetchone()
        if row is None:
            # Covers both "never existed" and "revoked_at IS NOT NULL" — a
            # revoked token is refused here, before any capability logic, so
            # revocation kills a scoped token on the import endpoint too.
            raise HTTPException(401, "Invalid or revoked API token")
        # The DATABASE row decides what this token may do, never the prefix a
        # caller happens to send. Enforce before anything else touches state.
        _assert_capability_allows(request, row["capability"], row["id"])
        # Phase 10 GHA-06 — follow merge pointer if the token's user has been merged.
        # The row's user_id may point at an orphan if a merge happened after this
        # token was minted (merge_user_rows re-parents user_api_tokens.user_id,
        # but a token row read before the merge can still resolve to the orphan
        # join row if the request raced). Re-resolve the survivor row in-band.
        if row["merged_into_user_id"] is not None:
            row = (await session.execute(sa.text("""
                SELECT t.id, t.user_id, t.team_scope, t.capability,
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
            # None = unrestricted. Non-None = this principal reached an
            # allow-listed path and may do nothing beyond that capability.
            "capability": row["capability"],
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
            # If the caller carries a github_id that already maps to a user
            # (e.g. they signed in via GitHub on the web app first), reuse that
            # row instead of minting an email:<...> identity — the github_id
            # UNIQUE constraint would otherwise reject the insert and surface a
            # spurious 401. This is the account-linking convergence point
            # between the GitHub-primary and LibreChat-onboarding entry paths.
            from app.repos.users import find_user_by_github_id

            user = None
            if github_id is not None:
                user = await find_user_by_github_id(session, github_id)
            if user is None:
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
                "sub": user.source_user_id,
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
    """Verify the principal is allowed to operate within X-Team-Scope. Returns the slug.

    Phase 10 (REVISION 1 B-3): blocked_at on team_members ALWAYS raises 403 —
    both in the user (gho_/Google) branch AND the user_api_token (xbt_) branch.
    Without the xbt_-side check, a user blocked AFTER minting a scoped token
    could keep using that pre-minted token to bypass enforcement on every
    team-scoped endpoint. The block check now fires before any return.
    """
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
            # Single-team scoped token — scope match confirmed. Phase 10 B-3:
            # we MUST still consult team_members.blocked_at here. Without this
            # check, a user blocked after the token was minted bypasses the
            # block entirely (the bypass is total and silent).
            user = principal["user"]
            member = await get_membership(
                session, user_id=user.id, team_slug=x_team_scope
            )
            if member is not None and member.blocked_at is not None:
                raise HTTPException(
                    403, f"Member blocked from team {x_team_scope}"
                )
            # We do NOT require membership existence in the scoped xbt_ branch
            # (the token was minted with this scope by an admin path that
            # already validated it). The block check is the only added
            # enforcement here.
            return x_team_scope
        # Multi-team token (empty-string sentinel): fall through to membership
        # check below — that path also enforces blocked_at.

    # T-05-02-02: GitHub users who are not org members cannot access team-scoped routes.
    # github_is_org_member=False means non-member; None means Google user (D7 — always allowed).
    if principal.get("github_is_org_member") is False:
        raise HTTPException(403, "GitHub account is not a member of the required org")

    user = principal["user"]
    membership = await get_membership(session, user_id=user.id, team_slug=x_team_scope)
    if membership is None:
        raise HTTPException(403, f"Not a member of team {x_team_scope}")
    # Phase 10 GHA-03 — block enforcement in the user (and multi-team xbt_) branch.
    if membership.blocked_at is not None:
        raise HTTPException(403, f"Member blocked from team {x_team_scope}")
    return x_team_scope


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
        from app.embedders import get_embedder
        # asyncpg DSN format (no SQLAlchemy driver prefix)
        pg_dsn = settings.DATABASE_URL.replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        return NativeProvider(
            pg_dsn=pg_dsn,
            qdrant_url=settings.QDRANT_URL,
            embedder=get_embedder(),
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


# === Brain monitor authorization (Phase 11 BMO-04 / BMO-08) ===


async def assert_can_edit_brain_event(
    principal: dict[str, Any],
    *,
    created_by: UUID | None,
    team_slug: str,
    session: AsyncSession,
) -> None:
    """Raise HTTPException(403) unless the principal can edit a brain event.

    Shared helper for plan 11-05's PATCH / DELETE / restore endpoints on
    /v1/brain/events/{entity_type}/{entity_id}. Defining it once here
    prevents the per-event authorisation rule from drifting across the
    three mutation endpoints.

    Rules (matches Phase 11 CONTEXT.md "Permissions model — option A"):

    - ``kind='bridge'`` → always allowed. Bridge service JWTs are
      implicitly trusted, mirroring ``_is_admin()``.
    - ``kind='user'`` or ``kind='user_api_token'`` → allowed when EITHER
      the principal is a per-team admin (``team_members.role = 'admin'``
      for the requested ``team_slug``) OR the row's ``created_by`` is
      not NULL and equals ``principal['user'].id``.
    - ``created_by IS NULL`` (memory_items, messages, contacts —
      the view exposes NULL because those tables have no author column)
      → admin-only. Plain members get 403.
    - The global ``_is_admin()`` (superadmin sub list) also bypasses,
      so a configured superadmin can edit anything across any team.

    Principal-shape audit (deps.py:46-235, validated against Phase 10):

    | # | ``kind`` | ``principal['user']`` shape | Source |
    |---|----------|------------------------------|--------|
    | 1 | ``user`` | ``User`` ORM row | Google OIDC ID token |
    | 2 | ``user`` | ``User`` ORM row | Google access token (Chrome ext) |
    | 3 | ``user`` | ``User`` ORM row | GitHub ``gho_`` token |
    | 4 | ``user_api_token`` | ``types.SimpleNamespace`` | Personal ``xbt_`` token |
    | 5 | ``user`` | ``User`` ORM row | Bridge JWT acting-user (LibreChat / OWUI) |
    | 6 | ``bridge`` | absent (``principal.get('user')`` is None) | Service JWT |

    Every ``kind='user*'`` variant carries ``principal['user'].id`` as a
    UUID. After Phase 10's identity merge, Google and GitHub sign-ins
    resolve to the SAME ``user.id`` so this helper does not need to
    branch on the auth source.

    Args:
        principal: dict returned by ``get_current_principal``.
        created_by: the row's ``created_by`` UUID, or ``None`` for entity
            types that have no author column.
        team_slug: validated team slug (typically from
            ``Depends(get_team_scope)``).
        session: the same ``AsyncSession`` the caller is using.

    Raises:
        HTTPException(403): if the principal is neither bridge, a per-team
            admin, an env-listed superadmin, nor the author of the row.
    """
    # 1) Bridge service JWTs — implicit admin, matches _is_admin() pattern.
    if principal.get("kind") == "bridge":
        return

    # 2) Global superadmin (ADMIN_USER_SUBS env list) — bypass cross-team.
    #    Cheap predicate; consult before the DB round-trip below.
    if _is_admin(principal):
        return

    user = principal.get("user")
    if user is None:
        # Defensive: a future auth path that returns kind != 'bridge' with
        # no user attached would silently pass otherwise. Fail closed.
        raise HTTPException(403, "No user identity on principal")

    # 3) Per-team admin check. NOTE: this is the per-team
    #    team_members.role='admin' membership — distinct from the global
    #    ADMIN_USER_SUBS list checked by _is_admin (which gates
    #    /v1/admin/* in plans 11-10/11-11).
    membership = await get_membership(session, user_id=user.id, team_slug=team_slug)
    if membership is not None and membership.role == "admin":
        return

    # 4) Author check — only valid if the view surfaced a non-NULL author.
    if created_by is not None and created_by == user.id:
        return

    raise HTTPException(
        403,
        "You can only edit items you created. Contact a team admin to modify "
        "items created by others.",
    )


# === Superadmin authorization (Phase 11 BMO-10 / BMO-11) ===


async def assert_is_superadmin(
    principal: dict[str, Any] = Depends(get_current_principal),
) -> None:
    """FastAPI dependency that raises 403 if principal is not a superadmin.

    Wraps the existing ``_is_admin()`` predicate at deps.py:322-333. Reuses
    ``ADMIN_USER_SUBS`` env var as the authoritative list of superadmin subs.
    Bridge JWTs (``kind='bridge'`` / ``kind='service'``) are also treated as
    superadmin — matches the existing ``_is_admin`` pattern.

    Returns None on success (FastAPI dependency contract); raises
    ``HTTPException(403)`` with a non-leaky detail message on failure.

    Lockdown behavior: if ``ADMIN_USER_SUBS`` is empty (env unset or set to
    ``''``), no real-user principal can be a superadmin — ``_is_admin()``
    returns False for ``kind='user'`` / ``kind='user_api_token'`` principals.
    Bridge JWTs still pass (service trust). Test in plan 11-10 Task 6 covers
    this case.

    Bridge JWTs (``kind='bridge'``) still pass — they inherit the trust model
    from ``_is_admin()``. When a bridge JWT calls a superadmin endpoint, the
    audit_log entry has ``actor_user_id=None`` and the bridge service identity
    is captured in ``audit_log.payload.actor_sub`` (the JWT ``sub`` claim).

    SECURITY NOTE: bridge JWTs accessing cross-team brain data is supported
    but the design intent is that bridges are emitted by internal trusted
    services (granola-sync, agent-runtime, librechat-bridge). If a bridge
    secret is compromised, an attacker could read cross-team brain content.
    Mitigation: rotate ``BRIDGE_SHARED_SECRET`` periodically and review
    ``audit_log`` entries where ``actor_user_id IS NULL``.
    """
    if not _is_admin(principal):
        raise HTTPException(
            status_code=403,
            detail="superadmin access required",
        )

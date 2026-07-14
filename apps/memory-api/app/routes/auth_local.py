"""POST /v1/auth/local/{register,login} — Phase 18 (LAUTH-01/02) native
email/password auth. The security surface of Phase 18.

Mints the SAME `xbt_` opaque token every other login mints (via
`app.services.api_tokens.mint_xbt_for_user` — the shared helper, NOT a
fork of `auth_github.py`'s private `_mint_xbt_for_user`), so a local-auth
principal is indistinguishable from a Google/GitHub-minted one at
`app.deps.get_current_principal` / `get_team_scope` (D-18-01, SC#3,
LAUTH-02). No branch is added to `deps.py`.

register() is ONE transaction, ONE commit (D-18-05, T-18-03-05):
  email-collision check (users.email, case-insensitive) -> get_or_create_user
  (source_user_id=f"email:{normalized}") -> local_credentials.create (bool)
  -> teams_repo.create_team DIRECT (never the solo-team-bootstrap HTTP route,
  which commits internally) -> mint_xbt_for_user -> ONE commit.
A concurrent-duplicate credential insert (create() returns False) and any
residual IntegrityError both map to 409, never a 500 (T-18-03-07).

login() returns a byte-identical generic 401 for an absent email, a wrong
password, AND a locked account (T-18-03-02/03/04) — verify_decoy() burns
comparable argon2 CPU time on every short-circuit branch so the response is
also timing-comparable. Never a distinct status/message for "locked"
(that would be a second enumeration oracle).

Does NOT touch app/deps.py or app/routes/auth_github.py (SC#4).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_session
from app.repos import local_credentials as local_credentials_repo
from app.repos import teams as teams_repo
from app.repos import users as users_repo
from app.services.api_tokens import mint_xbt_for_user
from app.services.password_hash import (
    hash_password,
    needs_rehash,
    verify_decoy,
    verify_password,
)
from app.services.rate_limit import enforce_rate_limit

router = APIRouter()
log = structlog.get_logger(__name__)

# Single literal message reused across absent/locked/wrong-password branches
# (login, below) — any divergence there is a user-enumeration oracle
# (T-18-03-02). Defined here so both routes stay in one shared vocabulary.
_EXISTING_EMAIL_ERROR = (
    "This email already has an account — sign in with your existing method, "
    "then add a password in settings."
)
_GENERIC_LOGIN_ERROR = "Invalid email or password."


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(
        min_length=settings.LOCAL_AUTH_MIN_PASSWORD_LENGTH, max_length=1024
    )


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class AuthOut(BaseModel):
    xbt_token: str
    user: dict[str, Any]
    team_scope: str | None = None


async def _rl_register(request: Request) -> None:
    await enforce_rate_limit(request, settings.LOCAL_AUTH_RATE_LIMIT, "auth_local_register")


async def _rl_login(request: Request) -> None:
    await enforce_rate_limit(request, settings.LOCAL_AUTH_RATE_LIMIT, "auth_local_login")


@router.post(
    "/auth/local/register",
    response_model=AuthOut,
    dependencies=[Depends(_rl_register)],
)
async def register(
    body: RegisterBody, session: AsyncSession = Depends(get_session)
) -> AuthOut:
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Invalid email address.")

    # Email-column collision check — NEVER a constructed source_user_id
    # (research Pitfalls 1+2). D-18-05: an existing email (GitHub, Google, or
    # local) gets 409, no access, no row.
    if await users_repo.get_user_by_email(session, email) is not None:
        raise HTTPException(409, _EXISTING_EMAIL_ERROR)

    try:
        user = await users_repo.get_or_create_user(
            session, source_user_id=f"email:{email}", email=email
        )
        created = await local_credentials_repo.create(
            session, user_id=user.id, password_hash=hash_password(body.password)
        )
        if not created:
            # Concurrent-duplicate credential collision (T-18-03-07) — 409, never a 500.
            await session.rollback()
            raise HTTPException(409, _EXISTING_EMAIL_ERROR)

        slug = f"solo-{user.id.hex[:16]}"
        await teams_repo.create_team(
            session,
            slug=slug,
            display_name="My Workspace",
            creator_user_id=user.id,
            visibility="closed",
            github_org=None,
        )

        xbt = await mint_xbt_for_user(session, user.id, name="local-register")

        # THE ONLY commit — matches auth_github signin's single-commit boundary.
        await session.commit()
    except IntegrityError:
        # Belt-and-suspenders: any residual race (source_user_id, etc.) -> 409.
        await session.rollback()
        raise HTTPException(409, _EXISTING_EMAIL_ERROR)

    log.info("auth.local.register.ok", user_id=str(user.id), team_scope=slug)
    return AuthOut(
        xbt_token=xbt,
        user={"id": str(user.id), "email": user.email},
        team_scope=slug,
    )


@router.post(
    "/auth/local/login",
    response_model=AuthOut,
    dependencies=[Depends(_rl_login)],
)
async def login(
    body: LoginBody, session: AsyncSession = Depends(get_session)
) -> AuthOut:
    email = body.email.strip().lower()
    row = await local_credentials_repo.get_by_email(session, email)

    if row is None:
        # Absent account — decoy verify equalizes timing, then the SAME
        # generic 401 as every other short-circuit branch below.
        verify_decoy(body.password)
        raise HTTPException(401, _GENERIC_LOGIN_ERROR)

    if row["locked_until"] is not None and row["locked_until"] > datetime.now(
        tz=timezone.utc
    ):
        # Locked — MUST return the identical generic 401, never a distinct
        # "locked" status code or message (that would be a second oracle).
        verify_decoy(body.password)
        raise HTTPException(401, _GENERIC_LOGIN_ERROR)

    if not verify_password(row["password_hash"], body.password):
        await local_credentials_repo.record_failure(session, row["user_id"])
        await session.commit()
        raise HTTPException(401, _GENERIC_LOGIN_ERROR)

    await local_credentials_repo.reset_failures(session, row["user_id"])
    if needs_rehash(row["password_hash"]):
        await local_credentials_repo.update_hash(
            session, row["user_id"], hash_password(body.password)
        )

    user = await users_repo.get_user_by_id(session, row["user_id"])
    xbt = await mint_xbt_for_user(session, row["user_id"], name="local-login")
    await session.commit()

    log.info("auth.local.login.ok", user_id=str(row["user_id"]))
    return AuthOut(xbt_token=xbt, user={"id": str(user.id), "email": user.email})

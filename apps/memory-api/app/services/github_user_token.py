"""Phase 12 — GitHub App user-to-server token refresh flow.

The function :func:`refresh_user_token_if_needed` is called from a FastAPI
dependency BEFORE any GitHub /user/* call. It:

  1. Checks if ``user.github_token_expires_at`` is within 5 minutes from now.
     If not (still fresh), decrypts and returns the current access_token.
  2. Otherwise acquires a per-user ``asyncio.Lock`` (RESEARCH Pitfall 6 race fix).
  3. Inside the lock, re-reads from DB (another coroutine may have refreshed).
  4. If still expiring: POST ``/login/oauth/access_token`` with
     ``grant_type=refresh_token``.
  5. SINGLE-USE: rotate both stored tokens atomically + update token_hash.
  6. Return the new access_token (plaintext).

Failure modes:
  - No refresh_token stored → raises :class:`GitHubReauthRequired` (caller
    surfaces 401).
  - Refresh token expired → raises :class:`GitHubReauthRequired`.
  - GitHub returns ``body['error']`` (even with HTTP 200) → raises
    :class:`GitHubReauthRequired`. Stored tokens AND hash are cleared so
    deps.py O(log n) index lookup cannot return a dead row.

References:
  - https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
  - RESEARCH §Q2, §Q15, §Pitfall 6
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services.token_crypto import (
    TokenCryptoInvalid,
    decrypt_token,
    encrypt_token,
    token_lookup_hash,
)

log = structlog.get_logger(__name__)


class GitHubReauthRequired(Exception):
    """Raised when no valid token chain remains — user must complete OAuth flow again."""


# Per-user locks to prevent the single-use-refresh-token race (Pitfall 6).
# In-process only — multi-instance deployments (Phase 13+) must migrate to a
# Postgres advisory lock keyed by user_id.
_REFRESH_LOCKS: dict[UUID, asyncio.Lock] = {}


def _reset_locks_for_tests() -> None:
    """Clear the per-user lock dict. Call from test fixtures between cases."""
    _REFRESH_LOCKS.clear()


def _get_lock(user_id: UUID) -> asyncio.Lock:
    lock = _REFRESH_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[user_id] = lock
    return lock


def _is_expired_or_close(
    expires_at: datetime | None,
    *,
    safety_window: timedelta = timedelta(minutes=5),
) -> bool:
    """True iff ``expires_at`` is unset OR within ``safety_window`` of now (UTC).

    Naive timestamps are interpreted as UTC — defensive against legacy rows
    inserted before Phase 12 standardised timezone-aware columns.
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < datetime.now(timezone.utc) + safety_window


async def refresh_user_token_if_needed(
    session: AsyncSession, user: User
) -> str:
    """Return a valid plaintext ghu_ access token, refreshing if needed.

    Atomicity guarantee: when a refresh occurs, the NEW access_token,
    refresh_token, expires_at, refresh_expires_at, AND token_hash are
    committed in a single transaction.
    """
    # Fast path — token still fresh.
    if not _is_expired_or_close(user.github_token_expires_at):
        try:
            cached = decrypt_token(user.github_access_token_enc)
        except TokenCryptoInvalid as exc:
            log.warning(
                "github_user_token.decrypt_failed",
                user_id=str(user.id),
            )
            raise GitHubReauthRequired(
                "Stored token cannot be decrypted"
            ) from exc
        if cached is not None:
            return cached

    if not user.github_refresh_token_enc:
        raise GitHubReauthRequired(
            "No refresh token stored — user must re-authorize"
        )

    lock = _get_lock(user.id)
    async with lock:
        # Re-read inside the lock — another coroutine may have refreshed.
        await session.refresh(user)
        if not _is_expired_or_close(user.github_token_expires_at):
            try:
                cached = decrypt_token(user.github_access_token_enc)
            except TokenCryptoInvalid as exc:
                raise GitHubReauthRequired(
                    "Stored token cannot be decrypted"
                ) from exc
            if cached is not None:
                return cached

        # Validate the refresh_token's own expiry (6-month TTL per GitHub).
        if user.github_refresh_expires_at is not None:
            if user.github_refresh_expires_at.tzinfo is None:
                user.github_refresh_expires_at = (
                    user.github_refresh_expires_at.replace(tzinfo=timezone.utc)
                )
            if user.github_refresh_expires_at < datetime.now(timezone.utc):
                raise GitHubReauthRequired(
                    "Refresh token expired — user must re-authorize"
                )

        try:
            refresh_plaintext = decrypt_token(user.github_refresh_token_enc)
        except TokenCryptoInvalid as exc:
            raise GitHubReauthRequired(
                "Refresh token corrupt — re-authorize required"
            ) from exc
        if not refresh_plaintext:
            raise GitHubReauthRequired("Refresh token absent after decrypt")

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.GITHUB_APP_CLIENT_ID,
                    "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_plaintext,
                },
            )

        if r.status_code != 200:
            log.warning(
                "github_user_token.refresh_http_error",
                status=r.status_code,
                user_id=str(user.id),
            )
            raise GitHubReauthRequired(
                f"Refresh failed with HTTP {r.status_code}"
            )

        body = r.json()
        if "error" in body:
            # GitHub returns 200 with body['error'] on logical failures.
            log.warning(
                "github_user_token.refresh_logical_error",
                user_id=str(user.id),
                error=body.get("error"),
                description=body.get("error_description"),
            )
            user.github_access_token_enc = None
            user.github_refresh_token_enc = None
            user.github_access_token_hash = None  # REVISION 2 (M-5) — clear stale hash
            user.github_token_expires_at = None
            user.github_refresh_expires_at = None
            await session.commit()
            raise GitHubReauthRequired(
                f"GitHub refresh error: {body.get('error')}"
            )

        now = datetime.now(timezone.utc)
        new_access = body["access_token"]
        new_refresh = body["refresh_token"]
        access_ttl_s = int(body.get("expires_in") or 28800)
        refresh_ttl_s = int(body.get("refresh_token_expires_in") or 15897600)

        user.github_access_token_enc = encrypt_token(new_access)
        user.github_refresh_token_enc = encrypt_token(new_refresh)
        # REVISION 2 (M-5) — rotate the indexed hash alongside the ciphertext.
        user.github_access_token_hash = token_lookup_hash(new_access)
        user.github_token_expires_at = now + timedelta(seconds=access_ttl_s)
        user.github_refresh_expires_at = now + timedelta(seconds=refresh_ttl_s)
        await session.commit()

        log.info(
            "github_user_token.refreshed",
            user_id=str(user.id),
            access_ttl_s=access_ttl_s,
            refresh_ttl_s=refresh_ttl_s,
        )
        return new_access


async def persist_tokens_on_signin(
    session: AsyncSession,
    user: User,
    *,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    refresh_token_expires_in: int,
) -> None:
    """Called from the signin route after a successful code exchange.

    Stores encrypted tokens + expiry timestamps + token_hash on the ``user``
    row. Does NOT commit — caller is in a transaction already (see
    ``auth_github.signin_github`` step 8).
    """
    now = datetime.now(timezone.utc)
    user.github_access_token_enc = encrypt_token(access_token)
    user.github_refresh_token_enc = encrypt_token(refresh_token)
    # REVISION 2 (M-5) — write the hash on every signin so deps.py lookup
    # works on the very first /v1/me call after sign-in.
    user.github_access_token_hash = token_lookup_hash(access_token)
    user.github_token_expires_at = now + timedelta(seconds=int(expires_in))
    user.github_refresh_expires_at = now + timedelta(
        seconds=int(refresh_token_expires_in)
    )

"""Phase 12 — GitHub App installation token management.

Two public helpers:

  - get_installation_token(installation_id) -> str   (cached 55 min, refresh on 401)
  - find_installation_for_org(session, org_login) -> int | None
        (hybrid: Postgres lookup first, fallback to GitHub API, backfill row)

And a convenience wrapper that combines both:

  - get_installation_token_for_org(session, org_login) -> str | None

The in-process cache is keyed by installation_id. For a single memory-api
instance (xbrain v1) this is sufficient; multi-instance deployment (Phase 13+)
should migrate to a Postgres-backed cache or use the existing token's
expires_at column to skip the cache entirely.

RESEARCH references:
  - §Q3 hybrid lookup strategy
  - §Q5 token discipline — this module returns 'ghs_...' installation tokens only
    (App JWTs and user-to-server tokens have their own modules)
  - §Pitfall 1 three-token taxonomy → this module returns 'ghs_...' only
  - §Pitfall 2 webhook delivery is best-effort → on-demand reconciliation
    (find_installation_for_org backfills the installations row if the webhook
    was missed but the App is actually installed)
  - §Pitfall 6 cache stampede → per-installation_id asyncio.Lock with
    double-checked locking inside the lock; in-process-only — multi-instance
    deployments must migrate to Postgres advisory lock or DB-cached tokens.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.installation import Installation
from app.services.github_app_jwt import mint_app_jwt

log = structlog.get_logger(__name__)

_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# In-process cache: installation_id → (expires_at_unix_ts, token_str)
_INSTALLATION_TOKEN_CACHE: dict[int, tuple[float, str]] = {}
# GitHub returns 1h tokens; cache 55 min, refresh 5 min early.
_INSTALLATION_TOKEN_TTL_S = 55 * 60

# Per-installation_id locks to prevent concurrent mint races (RESEARCH §Pitfall 6).
_INSTALLATION_TOKEN_LOCKS: dict[int, asyncio.Lock] = {}


def _reset_caches_for_tests() -> None:
    """Test helper — clear the cache + locks between cases."""
    _INSTALLATION_TOKEN_CACHE.clear()
    _INSTALLATION_TOKEN_LOCKS.clear()


def _get_lock(installation_id: int) -> asyncio.Lock:
    lock = _INSTALLATION_TOKEN_LOCKS.get(installation_id)
    if lock is None:
        lock = asyncio.Lock()
        _INSTALLATION_TOKEN_LOCKS[installation_id] = lock
    return lock


async def _mint_installation_token_raw(installation_id: int) -> tuple[str, datetime]:
    """Call GitHub: POST /app/installations/{id}/access_tokens with App JWT.

    Returns: (token_str, expires_at_utc)
    Raises:  httpx.HTTPStatusError on non-2xx (401 → caller will treat as
             auth-expired and force a refresh; 404 → installation not found,
             caller raises).
    """
    app_jwt = mint_app_jwt()
    headers = {"Authorization": f"Bearer {app_jwt}", **_GH_HEADERS}
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, headers=headers)
    r.raise_for_status()
    body = r.json()
    token: str = body["token"]
    # GitHub returns ISO 8601 like "2026-05-17T18:30:00Z"
    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    return token, expires_at


async def get_installation_token(
    installation_id: int,
    *,
    force_refresh: bool = False,
) -> str:
    """Return a valid installation access token ('ghs_...') for the given installation.

    Caches 55 minutes per installation_id. Concurrent requests for the same
    installation_id are serialized via a per-id asyncio.Lock so we only ever
    hit GitHub once per refresh.

    Args:
      installation_id: GitHub's numeric installation id (from installations table).
      force_refresh: if True, ignore the cache and re-mint. Used by retry-after-401
                     paths (REVISION 2, M-4 fix — see get_installation_token_for_org).

    Returns:
      token string suitable for `Authorization: Bearer ghs_...`.

    Raises:
      httpx.HTTPStatusError: if GitHub returns a non-2xx (404 = installation
        deleted; 401 = App credentials invalid; etc).

    Cache miss flow:
      1. Acquire per-installation lock.
      2. Re-check cache inside the lock (the other coroutine may have refreshed
         while we were waiting — double-checked locking).
      3. Mint App JWT → POST .../access_tokens → store ghs_ token + expires_at.
      4. Release lock.
    """
    now = time.time()
    if not force_refresh:
        cached = _INSTALLATION_TOKEN_CACHE.get(installation_id)
        if cached is not None and cached[0] > now:
            return cached[1]

    lock = _get_lock(installation_id)
    async with lock:
        # Double-check inside the lock — another coroutine may have refreshed.
        if not force_refresh:
            cached = _INSTALLATION_TOKEN_CACHE.get(installation_id)
            if cached is not None and cached[0] > time.time():
                return cached[1]

        token, expires_at = await _mint_installation_token_raw(installation_id)
        # Conservative TTL: use min(55min, github_expires_at - 60s) so we never
        # serve a near-expiry token even if GitHub returns a longer-lived one.
        gh_ttl_s = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        cache_until = time.time() + min(_INSTALLATION_TOKEN_TTL_S, gh_ttl_s - 60)
        _INSTALLATION_TOKEN_CACHE[installation_id] = (cache_until, token)
        log.info(
            "github_app.installation_token.minted",
            installation_id=installation_id,
            ttl_s=int(cache_until - time.time()),
        )
        return token


async def find_installation_for_org(
    session: AsyncSession,
    org_login: str,
) -> int | None:
    """Resolve installation_id for an org login (hybrid: DB then GitHub fallback).

    Lookup order (per RESEARCH §Q3):

      1. SELECT FROM installations WHERE github_org_login = $1 AND revoked_at IS NULL.
         If hit → return installation_id.
      2. (Webhook miss reconciliation, §Pitfall 2)
         GET https://api.github.com/orgs/{org}/installation with App JWT.
         - 200 → backfill the installations row (INSERT ... ON CONFLICT) and return id.
         - 404 → app is genuinely not installed on this org → return None.
         - other → raise httpx.HTTPStatusError.

    Args:
      session: AsyncSession (for the DB lookup + backfill).
      org_login: case-sensitive org login (GitHub treats login case-insensitively
                 but stores canonical case; we store what GitHub gives us).

    Returns:
      installation_id (int) if the app is installed on the org, else None.

    Note on transactions: this function calls `session.commit()` on the backfill
    path. Callers in 12-04 are read-only paths that don't expect a transaction
    boundary. A future caller mid-transaction should call this function outside
    its unit-of-work.
    """
    # Step 1 — DB lookup (active installs only).
    row = await session.execute(
        sa.select(Installation.installation_id).where(
            Installation.github_org_login == org_login,
            Installation.revoked_at.is_(None),
        ).limit(1)
    )
    installation_id = row.scalar_one_or_none()
    if installation_id is not None:
        return int(installation_id)

    # Step 2 — On-demand fallback (reconciliation for missed webhooks).
    log.info("github_app.installation.lookup.fallback", org_login=org_login)
    app_jwt = mint_app_jwt()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/orgs/{org_login}/installation",
            headers={"Authorization": f"Bearer {app_jwt}", **_GH_HEADERS},
        )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    payload = r.json()
    inst_id = int(payload["id"])
    account = payload.get("account") or {}
    account_type = account.get("type") or "Organization"
    permissions = payload.get("permissions") or {}
    # `created_by` is not in the /orgs/{org}/installation payload shape — GitHub
    # exposes the installer only on the install webhook (sender.id). Best-effort
    # only: prefer account.id if nothing else. The upsert's set_ deliberately
    # does NOT update installed_by_github_id on conflict, so a webhook-set value
    # is preserved.
    installed_by_id = (payload.get("created_by") or account or {}).get("id")
    # Backfill (upsert — handles race against a webhook landing concurrently).
    stmt = pg_insert(Installation).values(
        installation_id=inst_id,
        github_org_login=org_login,
        github_account_type=account_type,
        installed_by_github_id=installed_by_id,
        permissions=permissions,
        revoked_at=None,
        raw_payload=payload,
    ).on_conflict_do_update(
        index_elements=[Installation.installation_id],
        set_={
            "github_org_login": org_login,
            "github_account_type": account_type,
            "permissions": permissions,
            "raw_payload": payload,
            "updated_at": sa.func.now(),
            "revoked_at": None,
        },
    )
    await session.execute(stmt)
    await session.commit()
    log.info(
        "github_app.installation.backfilled",
        installation_id=inst_id,
        org_login=org_login,
    )
    return inst_id


async def get_installation_token_for_org(
    session: AsyncSession,
    org_login: str,
    *,
    force_refresh: bool = False,
) -> str | None:
    """Convenience: combine find_installation_for_org + get_installation_token.

    Returns None if the org has no active installation (caller should redirect
    the user to the install URL — Plan 12-06 UX).

    REVISION 2 (M-4 fix) — `force_refresh` proxies to `get_installation_token()`.
    Used by `app/auth.py:check_github_org_membership` (Plan 12-04 Task 1) when
    `/orgs/{org}/members/{username}` returns 401 (token revoked between mint
    and use). Caller passes `force_refresh=True` on the retry, bypassing the
    cache and minting a fresh installation token.

    Additionally, this function performs ONE internal retry if the mint call
    itself returns 401 — covering the case where the cached token (if any) is
    stale or the App-JWT minting flow needs a fresh attempt. The external
    caller may STILL pass force_refresh=True itself to force a bypass on the
    first call.
    """
    inst_id = await find_installation_for_org(session, org_login)
    if inst_id is None:
        return None
    try:
        return await get_installation_token(inst_id, force_refresh=force_refresh)
    except httpx.HTTPStatusError as exc:
        # 401 → cached token is no longer valid (revoked?). Force-refresh once.
        if exc.response.status_code == 401:
            log.warning(
                "github_app.installation_token.401_retry",
                installation_id=inst_id,
            )
            return await get_installation_token(inst_id, force_refresh=True)
        raise


async def find_user_installation(login: str) -> int | None:
    """Resolve the installation_id for a USER account (personal repos).

    ``find_installation_for_org`` only handles organizations
    (``GET /orgs/{org}/installation`` 404s for a personal account). This is the
    user-account sibling: ``GET /users/{login}/installation`` with an App JWT.

    Returns the installation_id, or None when the App is not installed on that
    user (404). Does NOT touch the installations table (User installs are not
    org-keyed) — callers use the id only to enumerate repos.
    """
    app_jwt = mint_app_jwt()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/users/{login}/installation",
            headers={"Authorization": f"Bearer {app_jwt}", **_GH_HEADERS},
        )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return int(r.json()["id"])

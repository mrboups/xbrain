"""Which key the agent falls back to when the subscription is not reachable.

THE MODEL. The subscription is the preferred path; a key is the FLOOR under it.
A team pays for API calls only in the moments no browser is holding their bridge.
Resolution order, most-specific first:

  1. the TEAM's own key for the provider, decrypted from `team_api_keys`
  2. the deployment-wide `settings.ANTHROPIC_API_KEY`
  3. neither — which is an unavailability, not an error

Everything below already existed except the last link: `team_api_keys`, its
encryption, its repo functions and its routes have been there since Phase 10, and
the agent never read any of it. It went straight to the deployment key.

TWO RULES THAT LOOK LIKE DETAILS AND ARE NOT.

A rejected team key does NOT fall through to the deployment key. A team that
supplied its own key expects its own billing, and quietly spending the
operator's instead is a surprise nobody asked for — and one they would discover
in an invoice. The caller says the team's key was refused and stops.

The key is never logged, never returned, never put in a message or an exception.
The only thing that leaves this module in the clear is the string itself, to the
one caller that hands it to a provider client. Every log line here names the
TIER and nothing else.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory

log = structlog.get_logger(__name__)

# The provider column already exists on team_api_keys, so the lookup is
# provider-aware rather than assuming one vendor — the schema anticipated more
# than one and the agent should not be the thing that forgets.
PROVIDER_ANTHROPIC = "anthropic"

TIER_TEAM = "team_key"
TIER_PLATFORM = "platform_key"
TIER_NONE = "none"

# Short. A decrypt per turn is cheap, but the team-key lookup is a round trip on
# the path of every agent mention, so it is worth not repeating within one burst
# of chat. Rotation does not wait for it to expire: PUT /api-keys invalidates
# the entry outright, because an operator who rotates a leaked key and finds it
# still working for ten minutes will not trust the feature again.
CACHE_TTL_S = 30.0

# (team_id, provider) -> (expires_at_monotonic, key_or_None)
_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}


@dataclass(frozen=True)
class FallbackKey:
    """A key and where it came from. `key` is None only when `tier` is none."""

    key: str | None
    tier: str

    @property
    def is_team_key(self) -> bool:
        return self.tier == TIER_TEAM


def _now() -> float:
    """Indirection so tests can drive the TTL without sleeping."""
    return time.monotonic()


def _fernet() -> Fernet | None:
    key = settings.FERNET_KEY or settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:  # noqa: BLE001 — a malformed key is a config problem, not a crash
        log.warning("team_keys.fernet_unusable")
        return None


def invalidate(team_id: UUID | str | None = None) -> None:
    """Drop cached keys — for one team, or all of them.

    Called by PUT /v1/teams/{id}/api-keys. Rotation has to bite immediately;
    waiting out a TTL is the difference between revoking a leaked key and
    hoping.
    """
    if team_id is None:
        _CACHE.clear()
        return
    wanted = str(team_id)
    for cached in [k for k in _CACHE if k[0] == wanted]:
        _CACHE.pop(cached, None)


async def _load_team_key(session: AsyncSession, team_id: UUID, provider: str) -> str | None:
    row = (
        await session.execute(
            text(
                """
                SELECT key_enc
                FROM team_api_keys
                WHERE team_id = :tid AND lower(provider) = lower(:provider)
                LIMIT 1
                """
            ),
            {"tid": str(team_id), "provider": provider},
        )
    ).fetchone()
    if row is None:
        return None
    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(row.key_enc.encode()).decode()
    except (InvalidToken, ValueError, AttributeError):
        # A key encrypted under a retired FERNET_KEY, or a corrupted row. Not
        # recoverable here and NOT worth logging the ciphertext over.
        log.warning("team_keys.decrypt_failed", team_id=str(team_id), provider=provider)
        return None


async def resolve_fallback_key(
    *,
    team_id: UUID,
    provider: str = PROVIDER_ANTHROPIC,
) -> FallbackKey:
    """Return the key the agent should fall back to, and which tier it came from.

    Never raises: a database that cannot be reached yields the platform tier if
    one is configured, and `none` otherwise. Falling back to unavailability is
    the safe direction — the alternative is an agent turn that dies on an
    exception nobody wrote a sentence for.
    """
    cache_key = (str(team_id), provider.lower())
    cached = _CACHE.get(cache_key)
    now = _now()
    if cached and cached[0] > now:
        team_key = cached[1]
    else:
        team_key = None
        try:
            async with async_session_factory() as session:
                team_key = await _load_team_key(session, team_id, provider)
        except Exception as e:  # noqa: BLE001
            log.warning("team_keys.lookup_failed", err=str(e))
        _CACHE[cache_key] = (now + CACHE_TTL_S, team_key)

    if team_key:
        return FallbackKey(key=team_key, tier=TIER_TEAM)

    platform_key = settings.ANTHROPIC_API_KEY or None
    if platform_key:
        return FallbackKey(key=platform_key, tier=TIER_PLATFORM)

    return FallbackKey(key=None, tier=TIER_NONE)


def _reset_cache_for_tests() -> None:
    _CACHE.clear()

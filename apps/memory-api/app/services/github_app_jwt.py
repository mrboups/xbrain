"""Phase 12 — GitHub App JWT signing (App JWT, 10-min, RS256).

This module mints the FIRST of three distinct GitHub App token kinds. Naming
discipline matters — do NOT shorten `mint_app_jwt` to `mint_jwt`. The three
kinds are (RESEARCH §Pitfall 1):

  1. App JWT          (this module)         — server proves "I am the App"
  2. Installation tok (Plan 12-03 service)  — server acts on behalf of an install
  3. User-to-server   (Plan 12-06 service)  — server acts on behalf of a user

The App JWT is used to authenticate the xbrain App itself to GitHub (NOT a
user, NOT an installation). It is short-lived (max 10 min per GitHub spec)
and is passed as `Authorization: Bearer <jwt>` to App-level endpoints:

  - POST /app/installations/{id}/access_tokens  — mint installation tokens (Plan 12-03)
  - GET  /orgs/{org}/installation               — discover installation for an org (Plan 12-04)
  - GET  /app                                   — sanity ping (used in verify-phase12.sh)

It MUST NOT be used to mint user-to-server tokens — that flow uses the
GITHUB_APP_CLIENT_SECRET via the OAuth user-code exchange (Plan 12-06).

DO NOT cache the minted JWT — minting is a single local RS256 sign with no
I/O (RESEARCH §Anti-Patterns). Caching adds a TTL check and a clock-skew
risk for zero gain.

The private key PEM is loaded from settings.GITHUB_APP_PRIVATE_KEY_B64
(base64-encoded single-line, decoded on first use and cached in-process via
lru_cache to avoid base64-decoding on every mint call). The cache is
process-local — restart memory-api to pick up a rotated key.

References:
  - https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
"""

from __future__ import annotations

import base64
import time
from functools import lru_cache

import jwt as pyjwt
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


class GitHubAppNotConfigured(Exception):
    """Raised when GITHUB_APP_PRIVATE_KEY_B64 or GITHUB_APP_CLIENT_ID is missing or malformed."""


@lru_cache(maxsize=1)
def _load_private_key_pem() -> str:
    """Decode the base64-encoded PEM once and cache.

    Returns the PEM as a UTF-8 string suitable for pyjwt.encode(...).
    Raises GitHubAppNotConfigured if the env var is empty, not valid base64,
    or does not decode to a PEM-shaped blob.
    """
    b64 = settings.GITHUB_APP_PRIVATE_KEY_B64
    if not b64:
        raise GitHubAppNotConfigured(
            "GITHUB_APP_PRIVATE_KEY_B64 is empty — register the GitHub App and "
            "set the base64-encoded PEM in .env (see 12-CONTEXT.md operator runbook)"
        )
    try:
        pem_bytes = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise GitHubAppNotConfigured(
            f"GITHUB_APP_PRIVATE_KEY_B64 is not valid base64: {exc}"
        ) from exc
    # Quick sanity check: PEM should start with -----BEGIN
    pem_str = pem_bytes.decode("utf-8")
    if not pem_str.lstrip().startswith("-----BEGIN"):
        raise GitHubAppNotConfigured(
            "Decoded GITHUB_APP_PRIVATE_KEY_B64 does not look like a PEM "
            "(expected '-----BEGIN ...' prefix). Re-encode with: openssl base64 -A -in xbrain.pem"
        )
    return pem_str


def _reset_private_key_cache_for_tests() -> None:
    """Test helper — clear the lru_cache so monkeypatched settings take effect."""
    _load_private_key_pem.cache_clear()


def mint_app_jwt(client_id: str | None = None) -> str:
    """Mint an App JWT signed RS256, 10-min lifetime.

    Used to mint installation tokens (Plan 12-03) and to discover/list
    installations (Plan 12-04). NOT used for user-to-server tokens — that
    flow uses the OAuth client_secret (see Plan 12-06).

    Args:
      client_id: GitHub App client_id (e.g. 'Iv23li...'). Defaults to
                 settings.GITHUB_APP_CLIENT_ID. GitHub also accepts the
                 numeric App ID for backwards compatibility (per
                 https://github.blog/changelog/2024-05-01) — both work as iss.

    Returns:
      A JWT string (header.payload.signature, dot-separated) to be passed as
      `Authorization: Bearer <jwt>` to GitHub App-level endpoints.

    Raises:
      GitHubAppNotConfigured: if private key or client_id is missing/malformed.

    Claims:
      iat: now - 60s  (clock-drift cushion, per GitHub docs)
      exp: now + 600s (10-minute max, GitHub rejects longer)
      iss: client_id

    Why no JWT cache: minting is a single local RS256 sign (no I/O). Caching
    the JWT just adds a TTL check and a clock-skew risk. Mint per call.
    """
    cid = client_id or settings.GITHUB_APP_CLIENT_ID
    if not cid:
        raise GitHubAppNotConfigured("GITHUB_APP_CLIENT_ID is empty")
    pem = _load_private_key_pem()
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": cid,
    }
    token = pyjwt.encode(payload, pem, algorithm="RS256")
    # pyjwt 2.x returns str; older 1.x returned bytes — defensive decode
    if isinstance(token, bytes):
        token = token.decode("ascii")
    log.debug("github_app.jwt.minted", client_id_prefix=cid[:8])
    return token

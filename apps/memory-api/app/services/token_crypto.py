"""Phase 12 — Fernet encryption + HMAC token-hash helpers for GitHub user tokens.

Reuses the existing FERNET_KEY env (Phase 4 drive-sync). The encrypted
ciphertext is stored in ``users.github_access_token_enc`` /
``users.github_refresh_token_enc`` as base64 text (Fernet's standard output).

The :func:`token_lookup_hash` helper is used by ``deps.py`` to do an O(log n)
lookup on ``users.github_access_token_hash`` (indexed) instead of scanning
all rows and decrypting one at a time. HMAC-SHA256 with FERNET_KEY as the
secret provides a deterministic hash that is unforgeable without the key —
even if an attacker reads the index, they cannot reconstruct the plaintext
token.

If FERNET_KEY is rotated, all stored tokens AND hashes become unreadable —
users must re-authorize via the OAuth flow. This is acceptable for a
1-user system in Phase 12 (rotation policy is operator-driven; documented
in 12-11 KB).

Falls back to ``OAUTH_CREDENTIALS_ENCRYPTION_KEY`` if ``FERNET_KEY`` is empty
— matches the same fallback rule used by Phase 7 Granola (see
``app/config.py`` Settings docstring).
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class TokenCryptoNotConfigured(Exception):
    """Raised when neither FERNET_KEY nor OAUTH_CREDENTIALS_ENCRYPTION_KEY is set."""


class TokenCryptoInvalid(Exception):
    """Raised on decrypt of a non-Fernet ciphertext or with the wrong key."""


def _resolve_key() -> str:
    """Return the active Fernet key (FERNET_KEY, else OAUTH_CREDENTIALS_ENCRYPTION_KEY).

    Both env vars are read at call time so test monkeypatching works.
    """
    key = getattr(settings, "FERNET_KEY", "") or getattr(
        settings, "OAUTH_CREDENTIALS_ENCRYPTION_KEY", ""
    )
    if not key:
        raise TokenCryptoNotConfigured(
            "FERNET_KEY (or OAUTH_CREDENTIALS_ENCRYPTION_KEY fallback) is empty — "
            "required for user GitHub token encryption. Generate with: "
            "python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        )
    return key


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = _resolve_key()
    return Fernet(key.encode() if isinstance(key, str) else key)


@lru_cache(maxsize=1)
def _hmac_key() -> bytes:
    key = _resolve_key()
    return key.encode() if isinstance(key, str) else key


def _reset_for_tests() -> None:
    """Clear cached Fernet+HMAC key holders. Call from test fixtures after
    monkeypatching ``settings.FERNET_KEY``."""
    _fernet.cache_clear()
    _hmac_key.cache_clear()


def encrypt_token(plaintext: str) -> str:
    """Encrypt a plaintext token (e.g. 'ghu_...') for at-rest storage."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str | None) -> str | None:
    """Decrypt a stored token. Returns ``None`` if input is ``None``.

    Raises :class:`TokenCryptoInvalid` on Fernet errors (corruption, key
    rotation, or tampering).
    """
    if ciphertext is None:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenCryptoInvalid(
            "Failed to decrypt token (key rotation or corruption?)"
        ) from exc


def token_lookup_hash(plaintext: str) -> str:
    """REVISION 2 (M-5) — deterministic HMAC-SHA256 of a plaintext token.

    Returns a 64-char hex string suitable for indexed equality lookup on
    ``users.github_access_token_hash``. The same plaintext always hashes to
    the same value (deterministic), but reversing requires the FERNET_KEY.

    Use this from ``deps.py`` to look up the user owning an incoming ghu_
    token in O(log n) instead of decrypting every row.
    """
    return hmac.new(
        _hmac_key(), plaintext.encode("utf-8"), hashlib.sha256
    ).hexdigest()

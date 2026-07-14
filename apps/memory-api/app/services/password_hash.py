"""argon2id password hashing (D-18-04) — the ONLY place a plaintext password may exist.

Wraps `argon2.PasswordHasher()` with NO constructor overrides: the shipped defaults
(RFC_9106_LOW_MEMORY profile — m=65536 KiB, t=3, p=4 — verified live against the
installed argon2-cffi 25.1.0) are the correct choice for this box (D-18-04's own
discretion note: "use argon2-cffi's sane defaults; do not hand-roll"). Do not add
`m=`/`t=`/`p=` kwargs here.

Also provides the decoy-hash timing equalizer (research Pattern 2 / SC#6): a fixed,
module-level argon2id hash computed once at import time. The login route (Plan 03)
calls `verify_decoy()` on every short-circuit branch (email not found, account
locked) so those branches burn comparable CPU to a real `verify_password()` call —
without this, an attacker can time the response and learn which emails have an
account (T-18-02-02).
"""

import contextlib

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# NO constructor args — RFC_9106_LOW_MEMORY is argon2-cffi's shipped default since 21.2.0.
ph = PasswordHasher()

# Fixed decoy hash, computed once at import. Any fixed string works — it is never
# compared against a real password, only used to burn comparable argon2 CPU time.
_DECOY_HASH = ph.hash("xbrain-local-auth-decoy-2026")


def hash_password(pw: str) -> str:
    """Return a self-describing argon2id encoded hash (`$argon2id$...`, carries salt+params)."""
    return ph.hash(pw)


def verify_password(encoded: str, pw: str) -> bool:
    """Return True/False for a correct/incorrect password against a REAL stored hash.

    Only `VerifyMismatchError` (wrong password) is swallowed into `False`.
    `VerificationError`/`InvalidHashError` (malformed or foreign hash) propagate —
    those indicate a corrupted/foreign row, not "wrong password", and should be a
    hard failure the caller notices rather than a silent False.
    """
    try:
        ph.verify(encoded, pw)
        return True
    except VerifyMismatchError:
        return False


def verify_decoy(pw: str) -> None:
    """Run a real argon2 verify against the fixed decoy hash. NEVER raises.

    Purely a timing equalizer for the login route's account-absent / account-locked
    branches (Pattern 2) — the return value carries no meaning and is discarded.
    """
    with contextlib.suppress(Exception):
        ph.verify(_DECOY_HASH, pw)


def needs_rehash(encoded: str) -> bool:
    """True if `encoded` was hashed with different (e.g. stale) params than current defaults."""
    return ph.check_needs_rehash(encoded)

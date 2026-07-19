"""Pure unit tests for the Phase 25 invite-code generator (Plan 25-01, JOINCODE-01).

No Docker, no Postgres. These lock the hash-at-rest CONTRACT (D-25-01) the mint
endpoint (25-02) and the real-Postgres security gate (25-03) build against: the
plaintext is a high-entropy ``xbi_`` bearer secret, only its sha256 hash ever reaches
storage, and the stored hash is NOT the secret (a DB leak yields no usable code).

The behavioral proof (mint→join→revoke, the racing double-spend, team isolation)
against a real Postgres is deferred to the gate in Plan 25-03.
"""
from __future__ import annotations

import hashlib

from app.repos.team_invite_codes import generate_code


def test_plaintext_is_xbi_prefixed_high_entropy():
    plaintext, _code_hash, _prefix = generate_code()
    assert plaintext.startswith("xbi_"), "the bearer secret must carry the xbi_ prefix"
    # "xbi_" + token_urlsafe(24) is comfortably longer than 30 chars (≈192-bit secret).
    assert len(plaintext) > 30


def test_code_hash_is_sha256_of_plaintext():
    plaintext, code_hash, _prefix = generate_code()
    assert code_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert len(code_hash) == 64, "sha256 hex is exactly 64 chars"
    # lowercase hex only — matches the oauth_tokens.hash_token contract.
    assert code_hash == code_hash.lower()
    assert all(c in "0123456789abcdef" for c in code_hash)


def test_stored_hash_is_not_the_secret():
    """The row stores the HASH, never the plaintext — a DB compromise leaks no code."""
    plaintext, code_hash, _prefix = generate_code()
    assert plaintext != code_hash
    # The secret must not be embedded in what we persist.
    assert code_hash not in plaintext
    assert plaintext not in code_hash


def test_code_prefix_is_non_secret_short_label():
    plaintext, code_hash, code_prefix = generate_code()
    assert code_prefix == plaintext[:12], "prefix is the first 12 chars (xbi_ + 8)"
    assert len(code_prefix) == 12
    assert code_prefix.startswith("xbi_")
    # The prefix is a display label, not the secret: it cannot contain the full hash.
    assert code_hash not in code_prefix


def test_two_calls_produce_distinct_codes():
    """High entropy — no collision / reuse between two mints."""
    p1, h1, _ = generate_code()
    p2, h2, _ = generate_code()
    assert p1 != p2, "two plaintexts must differ (CSPRNG, no reuse)"
    assert h1 != h2, "two hashes must differ"

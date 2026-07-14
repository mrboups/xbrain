"""Unit tests for app.services.password_hash — pure functions, NO Docker, NO DB.

Covers every <behavior> bullet in 18-02-PLAN.md Task 1.
"""

from app.services.password_hash import (
    hash_password,
    needs_rehash,
    verify_decoy,
    verify_password,
)


def test_hash_password_is_argon2id_encoded():
    encoded = hash_password("hunter2")
    assert encoded.startswith("$argon2id$")
    assert len(encoded) > 40


def test_verify_password_correct():
    encoded = hash_password("hunter2")
    assert verify_password(encoded, "hunter2") is True


def test_verify_password_wrong_returns_false_not_raise():
    encoded = hash_password("hunter2")
    assert verify_password(encoded, "wrong") is False


def test_verify_decoy_never_raises_and_returns_none():
    result = verify_decoy("anything")
    assert result is None


def test_needs_rehash_false_for_fresh_hash():
    encoded = hash_password("x")
    assert needs_rehash(encoded) is False


def test_hash_password_uses_random_salt():
    first = hash_password("hunter2")
    second = hash_password("hunter2")
    assert first != second

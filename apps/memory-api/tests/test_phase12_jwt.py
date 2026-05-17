"""Unit tests for app.services.github_app_jwt — RS256 minting + claim verification.

Phase 12 plan 12-02 task 3. Mocks the App private key per test via cryptography's
own rsa.generate_private_key() — the real prod key is never loaded from env in
unit tests (success-criteria invariant).
"""

from __future__ import annotations

import base64
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import github_app_jwt
from app.services.github_app_jwt import (
    GitHubAppNotConfigured,
    _reset_private_key_cache_for_tests,
    mint_app_jwt,
)


@pytest.fixture
def app_pem_b64() -> tuple[str, str]:
    """Generate a fresh RSA key per test. Returns (pem_b64, pub_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return base64.b64encode(pem).decode(), pub_pem


@pytest.fixture(autouse=True)
def _configure(monkeypatch, app_pem_b64):
    """Wire a fresh synthetic key + test client_id into settings for each test.

    Yields the public PEM so tests can verify the minted JWT signature
    end-to-end without re-deriving the public key.
    """
    pem_b64, pub_pem = app_pem_b64
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test")
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_PRIVATE_KEY_B64", pem_b64)
    _reset_private_key_cache_for_tests()
    yield pub_pem
    _reset_private_key_cache_for_tests()


def test_mint_jwt_has_three_segments(_configure):
    t = mint_app_jwt()
    assert t.count(".") == 2, "JWT must be header.payload.signature"


def test_mint_jwt_uses_rs256(_configure):
    t = mint_app_jwt()
    header = pyjwt.get_unverified_header(t)
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"


def test_mint_jwt_claims_iat_exp_iss(_configure):
    """Verify the three required claims against GitHub's App-JWT spec.

    GitHub's hard limit is `exp - server_now <= 600s` (10 min). The 60s
    past-iat is a client-side cushion against client/server clock drift —
    NOT part of the lifetime budget. So this helper produces:
      - iat = client_now - 60s     (cushion)
      - exp = client_now + 600s    (full 10-min lifetime from now)
      - exp - iat == 660s          (cushion + lifetime, never compared to a cap)
    """
    pub_pem = _configure
    t = mint_app_jwt()
    claims = pyjwt.decode(t, pub_pem, algorithms=["RS256"], options={"verify_signature": True})
    now = int(time.time())
    assert claims["iss"] == "Iv23li_test"
    # iat must sit ~60s in the past per GitHub clock-drift cushion
    assert -65 <= (claims["iat"] - now) <= -55, f"iat={claims['iat']}, now={now}"
    # exp must be 10 min in the future (small tolerance for slow CI runners)
    assert 595 <= (claims["exp"] - now) <= 605, f"exp={claims['exp']}, now={now}"
    # exp - iat == cushion (60s) + lifetime (600s)
    assert claims["exp"] - claims["iat"] == 660


def test_mint_jwt_iss_override(_configure):
    """Passing client_id arg overrides the settings default."""
    pub_pem = _configure
    t = mint_app_jwt(client_id="Iv23li_custom")
    claims = pyjwt.decode(t, pub_pem, algorithms=["RS256"], options={"verify_signature": True})
    assert claims["iss"] == "Iv23li_custom"


def test_missing_private_key_raises(monkeypatch):
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_PRIVATE_KEY_B64", "")
    _reset_private_key_cache_for_tests()
    with pytest.raises(GitHubAppNotConfigured, match="GITHUB_APP_PRIVATE_KEY_B64 is empty"):
        mint_app_jwt()


def test_invalid_base64_raises(monkeypatch):
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_PRIVATE_KEY_B64", "not-valid-base64-!!")
    _reset_private_key_cache_for_tests()
    with pytest.raises(GitHubAppNotConfigured, match="not valid base64"):
        mint_app_jwt()


def test_non_pem_decoded_raises(monkeypatch):
    monkeypatch.setattr(
        github_app_jwt.settings,
        "GITHUB_APP_PRIVATE_KEY_B64",
        base64.b64encode(b"this is not a PEM").decode(),
    )
    _reset_private_key_cache_for_tests()
    with pytest.raises(GitHubAppNotConfigured, match="does not look like a PEM"):
        mint_app_jwt()


def test_missing_client_id_raises(monkeypatch):
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_CLIENT_ID", "")
    with pytest.raises(GitHubAppNotConfigured, match="GITHUB_APP_CLIENT_ID is empty"):
        mint_app_jwt()

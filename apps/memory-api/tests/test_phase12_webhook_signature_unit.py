"""Unit tests for webhook HMAC verification (Plan 12-05 — security boundary).

These tests deliberately avoid the integration fixtures (`client`,
`session`) because the HMAC verifier is the security gate that runs
BEFORE any DB access — it must be verifiable on every host (CI without
Docker included). The integration tests in test_phase12_webhook.py
exercise the route end-to-end through httpx + testcontainers Postgres
and are SKIPPED automatically when Docker isn't available.

Why this file exists separately from test_phase12_webhook.py: without
runnable security-gate coverage on a Docker-less dev host, a future
refactor swapping `hmac.compare_digest` for `==` (timing attack —
RESEARCH §Pitfall 8) would only break in CI. Rule 2 deviation:
correctness of the auth boundary requires runnable-everywhere coverage.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.routes.webhooks_github import _verify_signature


KEY = "phase12-unit-test-secret-do-not-use-anywhere-else"


def _sign(body: bytes, secret: str = KEY) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    """Force a known webhook secret on the route's `settings` reference."""
    from app.routes import webhooks_github as route_mod

    monkeypatch.setattr(route_mod.settings, "GITHUB_APP_WEBHOOK_SECRET", KEY)
    yield


def test_verify_signature_accepts_correct_hmac():
    """Round-trip — sign body with the known secret, verify must NOT raise."""
    body = b'{"action":"created","installation":{"id":1}}'
    sig = _sign(body)
    # No exception means verification passed.
    _verify_signature(body, sig)


def test_verify_signature_rejects_wrong_secret():
    """A signature computed with a different secret must raise 401."""
    body = b'{"action":"created"}'
    wrong_sig = _sign(body, secret="not-the-real-secret")
    with pytest.raises(HTTPException) as exc:
        _verify_signature(body, wrong_sig)
    assert exc.value.status_code == 401


def test_verify_signature_rejects_tampered_body():
    """If body changes after signing, verification must fail."""
    body = b'{"action":"created"}'
    sig = _sign(body)
    tampered = b'{"action":"deleted"}'
    with pytest.raises(HTTPException) as exc:
        _verify_signature(tampered, sig)
    assert exc.value.status_code == 401


def test_verify_signature_rejects_missing_header():
    """Empty/None X-Hub-Signature-256 must raise 401."""
    with pytest.raises(HTTPException) as exc:
        _verify_signature(b'{"x":1}', None)
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc2:
        _verify_signature(b'{"x":1}', "")
    assert exc2.value.status_code == 401


def test_verify_signature_rejects_wrong_prefix():
    """A signature without the `sha256=` prefix must NOT pass."""
    body = b'{"x":1}'
    raw_hex = hmac.new(KEY.encode(), body, hashlib.sha256).hexdigest()
    # No prefix — should fail (constant-time compare against the prefixed expected).
    with pytest.raises(HTTPException) as exc:
        _verify_signature(body, raw_hex)
    assert exc.value.status_code == 401


def test_verify_signature_fails_closed_when_secret_unset(monkeypatch):
    """If GITHUB_APP_WEBHOOK_SECRET is empty, verifier returns 503 not 401.

    Misconfiguration must surface as a 5xx so the operator notices, not
    a silent 401 that looks like a normal signature failure.
    """
    from app.routes import webhooks_github as route_mod

    monkeypatch.setattr(route_mod.settings, "GITHUB_APP_WEBHOOK_SECRET", "")
    body = b'{"x":1}'
    with pytest.raises(HTTPException) as exc:
        _verify_signature(body, _sign(body))
    assert exc.value.status_code == 503


def test_verify_signature_uses_compare_digest():
    """Lock the timing-safe comparison — guards RESEARCH §Pitfall 8.

    We can't reliably benchmark timing differences in unit tests, but we
    CAN assert the function imports and uses `hmac.compare_digest`. If a
    refactor swaps it for `==`, this test fails (the source code grep
    is a strong, fragile-on-purpose invariant).
    """
    import inspect

    from app.routes import webhooks_github as route_mod

    src = inspect.getsource(route_mod._verify_signature)
    assert "hmac.compare_digest" in src, (
        "_verify_signature must use hmac.compare_digest for timing-safe "
        "comparison (RESEARCH §Pitfall 8). Found:\n" + src
    )
    # Defensive: also assert no naive `==` against the header value.
    assert "expected == signature_header" not in src, (
        "Direct `==` comparison reintroduces timing-attack surface"
    )

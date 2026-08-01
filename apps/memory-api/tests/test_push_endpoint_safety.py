"""Phase 27 — the stored-endpoint SSRF guard and the private-key containment scan.

Two things are asserted here, both cheap and both structural (no DB, no Docker, so they
can never rot behind a skip):

1. `_is_safe_push_endpoint` — a stored subscription endpoint is a URL this server will
   POST to on every mention, forever, unattended. An unvalidated one is not a bad input;
   it is a STORED SSRF primitive aimed at the metadata service, at Redis, at anything on
   the VM's private network (T-27-03-05). The table below is the accept/reject contract.

   Validation is purely LEXICAL — no DNS, no fetch — mirroring services/url_safety.py's
   ban. Resolving a hostname to decide whether it is "internal" would itself be the
   outbound request we are trying to prevent, and the answer would be stale by the time
   the send path used it. So a hostname that resolves privately is NOT rejected here;
   what is rejected is the shape that lets an attacker name an internal address
   directly (an IP literal, `localhost`, a non-https scheme, embedded userinfo).

2. The route module never so much as NAMES the VAPID private key (T-27-03-01). A
   source scan is a blunt instrument and that is exactly why it works: no future edit
   to app/routes/push.py can reach the private half of the keypair without this test
   going red, whatever shape the leak takes (a debug field, a log line, a dict spread).
"""
from __future__ import annotations

import pytest

# ── 1. The SSRF guard ─────────────────────────────────────────────────────────

# Real push-service endpoints (the shapes Chrome, Firefox and Edge actually hand back)
# plus benign variations that MUST keep working.
ACCEPT = [
    "https://fcm.googleapis.com/fcm/send/dGhpcy1pcy1hLWZha2UtdG9rZW4",
    "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABmZmZm",
    "https://wns2-par02p.notify.windows.com/w/?token=AwYAAAB",
    "https://push.example.com:8443/p/abc",          # explicit non-443 port is fine
    "HTTPS://Push.Example.com/p/abc",               # scheme/host case is not significant
    "https://8.8.8.8/p/abc",                        # a PUBLIC ip literal is not the threat
]

REJECT = [
    ("http://fcm.googleapis.com/fcm/send/x", "plain http — the payload keys would cross in clear"),
    ("//fcm.googleapis.com/fcm/send/x", "scheme-relative, no scheme at all"),
    ("javascript:alert(1)", "not a URL we would ever POST to"),
    ("file:///etc/passwd", "local file scheme"),
    ("ftp://push.example.com/x", "non-https scheme"),
    ("https://", "no host"),
    ("https://user:pw@push.example.com/x", "embedded userinfo — spoofs the real host"),
    ("https://push.example.com@evil.example/x", "userinfo spoof without a password"),
    ("https://localhost/p/abc", "loopback by name"),
    ("https://api.localhost/p/abc", "loopback by name, subdomain form"),
    ("https://127.0.0.1/p/abc", "IPv4 loopback"),
    ("https://10.0.0.5/p/abc", "RFC1918 private"),
    ("https://192.168.1.1/p/abc", "RFC1918 private"),
    ("https://172.16.0.1/p/abc", "RFC1918 private"),
    ("https://169.254.169.254/latest/meta-data/", "link-local — the cloud metadata service"),
    ("https://[::1]/p/abc", "IPv6 loopback"),
    ("https://[fe80::1]/p/abc", "IPv6 link-local"),
    ("https://[fc00::1]/p/abc", "IPv6 unique-local"),
    ("https://[::ffff:127.0.0.1]/p/abc", "IPv4-mapped IPv6 loopback — the classic bypass"),
    ("https://[::ffff:10.0.0.5]/p/abc", "IPv4-mapped IPv6 private"),
    ("https://0.0.0.0/p/abc", "unspecified address"),
    ("https://push.example.com/a b", "embedded whitespace"),
    ("https://push.example.com/\nx", "embedded newline — header-splitting shape"),
    ("", "empty"),
    ("   ", "whitespace only"),
]


@pytest.mark.parametrize("url", ACCEPT)
def test_accepts_real_push_service_endpoints(url: str) -> None:
    from app.routes.push import _is_safe_push_endpoint

    assert _is_safe_push_endpoint(url) is True, f"must accept: {url}"


@pytest.mark.parametrize("url,why", REJECT, ids=[u[:40] or "empty" for u, _ in REJECT])
def test_rejects_unsafe_endpoints(url: str, why: str) -> None:
    from app.routes.push import _is_safe_push_endpoint

    assert _is_safe_push_endpoint(url) is False, f"must reject ({why}): {url!r}"


def test_rejects_an_over_long_endpoint() -> None:
    """2048 is the ceiling; one char past it is rejected.

    Both bounds are asserted so the check can never silently become a no-op (a guard
    that only ever sees the reject case would still pass if it rejected everything).
    """
    from app.routes.push import _MAX_ENDPOINT_LEN, _is_safe_push_endpoint

    base = "https://push.example.com/p/"
    at_limit = base + "a" * (_MAX_ENDPOINT_LEN - len(base))
    assert len(at_limit) == _MAX_ENDPOINT_LEN
    assert _is_safe_push_endpoint(at_limit) is True
    assert _is_safe_push_endpoint(at_limit + "a") is False
    assert _is_safe_push_endpoint(base + "a" * 3000) is False


def test_rejects_non_string_input() -> None:
    """A non-str must be rejected, not raise — the guard runs before Pydantic in the
    prune path and must never turn a bad value into a 500."""
    from app.routes.push import _is_safe_push_endpoint

    for bad in (None, 123, b"https://push.example.com/x", ["https://x"]):
        assert _is_safe_push_endpoint(bad) is False  # type: ignore[arg-type]


def test_guard_performs_no_network_io() -> None:
    """The guard is lexical. If it ever imports a resolver or an HTTP client, the SSRF
    ban it exists to enforce is gone — so assert the module does not reference one."""
    from pathlib import Path

    import app.routes.push as push_mod

    source = Path(push_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("socket.", "gethostbyname", "getaddrinfo", "httpx.get", "requests.get"):
        assert forbidden not in source, (
            f"app/routes/push.py references {forbidden!r} — resolving or fetching the "
            "endpoint would BE the SSRF this module is supposed to prevent."
        )


# ── 2. Private-key containment ────────────────────────────────────────────────


def test_route_module_never_names_the_vapid_private_key() -> None:
    """T-27-03-01. The config endpoint hands the PUBLIC key to browsers; the private
    half must not be reachable from this module at all — not returned, not logged, not
    read. A source scan catches every shape of that mistake at once."""
    from pathlib import Path

    import app.routes.push as push_mod

    source = Path(push_mod.__file__).read_text(encoding="utf-8")
    secret_name = "VAPID_" + "PRIVATE_KEY"  # assembled so this test file is not its own hit
    assert secret_name not in source, (
        "app/routes/push.py references the VAPID private key. Only the public half may "
        "cross to a client — remove the reference."
    )


def test_config_settings_expose_a_private_key_field_that_defaults_empty() -> None:
    """The knob exists (so the send path in 27-04 has one) and a zero-key install is
    the DEFAULT, which is what lets an OSS install boot with push simply absent."""
    from app.config import Settings

    assert Settings.model_fields["VAPID_PRIVATE_KEY"].default == ""
    assert Settings.model_fields["VAPID_PUBLIC_KEY"].default == ""
    assert Settings.model_fields["PUSH_ENABLED"].default is True

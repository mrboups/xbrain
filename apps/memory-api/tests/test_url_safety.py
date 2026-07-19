"""Unit table for the pure nudge-URL safety guard (Phase 22 / D-22-03).

No DB, no Docker, no network — this is a purely lexical guard. It exists to keep
the nudge endpoint from ever publishing a `javascript:`/`data:`/`file:` URI, a
scheme-relative reference, a malformed string, or an over-long URL to a target's
Centrifugo channel. The endpoint calls this BEFORE scheduling any publish.
"""
from __future__ import annotations

import pytest

from app.services.url_safety import is_safe_nudge_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://x.io/a?b=1#c",
        "https://sub.host:8443/path",
        "HTTPS://x",  # scheme comparison is case-insensitive
        "http://example.com/" + "a" * 100,  # comfortably within default max_len
    ],
)
def test_accepts_wellformed_http_https(url: str) -> None:
    assert is_safe_nudge_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",   # D-22-03 script URI
        "data:text/html,x",      # D-22-03 data URI
        "file:///etc/passwd",    # D-22-03 local file
        "mailto:a@b.c",          # non-http scheme
        "ftp://h/x",             # non-http scheme
        "//example.com",         # scheme-relative (no scheme)
    ],
)
def test_rejects_bad_scheme(url: str) -> None:
    assert is_safe_nudge_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://",              # no host
        "https://",             # no host
        "not a url",            # whitespace + no scheme/host
        "http://exa mple.com",  # embedded space
    ],
)
def test_rejects_malformed(url: str) -> None:
    assert is_safe_nudge_url(url) is False


@pytest.mark.parametrize("bad", [None, 123, b"https://x", ["https://x"], {}])
def test_rejects_non_str(bad) -> None:
    assert is_safe_nudge_url(bad) is False  # type: ignore[arg-type]


def test_rejects_too_long() -> None:
    long_url = "https://example.com/" + "a" * 5000
    assert is_safe_nudge_url(long_url) is False
    # Same URL is accepted when max_len is raised above its length.
    assert is_safe_nudge_url(long_url, max_len=len(long_url) + 1) is True


def test_max_len_boundary() -> None:
    # A URL exactly at max_len is accepted; one char over is rejected.
    base = "https://example.com/"
    exact = base + "a" * (64 - len(base))
    assert len(exact) == 64
    assert is_safe_nudge_url(exact, max_len=64) is True
    assert is_safe_nudge_url(exact + "a", max_len=64) is False

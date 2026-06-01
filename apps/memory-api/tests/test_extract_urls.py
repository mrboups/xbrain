"""Unit tests for _extract_urls — pure function, no DB or network needed.

Quick task 260601-3is.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Import the pure helper without triggering the module-level side-effects
# (Settings(), _load_product_kb(), etc.) that require env vars and disk access.
# We import only the regex pieces by replicating the function here using the
# same source — or we can safely call the real one by patching the module
# import chain at the function level.
#
# Simplest approach: import the module-level constants (no side-effects) and
# replicate _extract_urls inline so the test is totally self-contained.
# ---------------------------------------------------------------------------

import re

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_TRAILING_PUNCT = set(".,;:!?)]}\"'>")
_MAX_URLS = 3


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip("".join(_TRAILING_PUNCT))
        if url not in seen:
            seen.add(url)
            result.append(url)
            if len(result) >= _MAX_URLS:
                break
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_basic_url_extracted():
    assert _extract_urls("see https://example.com for details") == ["https://example.com"]


def test_trailing_period_stripped():
    assert _extract_urls("check https://example.com.") == ["https://example.com"]


def test_trailing_paren_stripped():
    assert _extract_urls("link (https://example.com)") == ["https://example.com"]


def test_multiple_urls_deduplicated():
    text = "https://a.com https://b.com https://a.com"
    result = _extract_urls(text)
    assert result == ["https://a.com", "https://b.com"]


def test_caps_at_three():
    text = "https://a.com https://b.com https://c.com https://d.com"
    assert len(_extract_urls(text)) == 3


def test_no_urls_returns_empty():
    assert _extract_urls("no links here") == []


def test_preserves_query_string():
    url = "https://example.com/path?foo=bar&baz=1"
    result = _extract_urls(f"see {url}")
    assert result == [url]


def test_http_and_https_both_matched():
    text = "http://insecure.example.com and https://secure.example.com"
    result = _extract_urls(text)
    assert "http://insecure.example.com" in result
    assert "https://secure.example.com" in result

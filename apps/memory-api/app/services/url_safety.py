"""Pure, lexical URL-safety guard for the push-a-link nudge (Phase 22 / D-22-03).

The nudge endpoint publishes an ``open_url`` event to a target member's Centrifugo
channel. Before it does, it MUST prove the URL is a benign http/https destination
so a sender can never smuggle a ``javascript:`` / ``data:`` / ``file:`` URI (script
or local-file injection at the recipient) or a malformed/over-long string.

This function is deliberately PURE and lexical:
  - It does NO network I/O — no DNS resolution, no HTTP fetch, no shortener
    expansion. Server-side fetching would be an SSRF vector (D-22-03 forbids it);
    v1 shows the recipient the literal, un-expanded URL instead.
  - It only inspects the string with ``urllib.parse.urlsplit``.

Keep it that way: importing any network client here would break the SSRF ban that
the endpoint relies on (grep-asserted in the plan's acceptance criteria).
"""
from __future__ import annotations

from urllib.parse import urlsplit

# Only these two schemes may ever reach a recipient's browser via a nudge.
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def is_safe_nudge_url(url: str, *, max_len: int = 2048) -> bool:
    """Return True iff ``url`` is a well-formed http/https URL within ``max_len``.

    Rejects (returns False) when the value is:
      - not a ``str``;
      - empty or whitespace-only, or contains ANY internal whitespace;
      - longer than ``max_len`` characters;
      - not parseable, or has a scheme outside {http, https} (case-insensitive),
        or has an empty host (netloc).

    No network I/O of any kind is performed — validation is purely lexical.
    """
    if not isinstance(url, str):
        return False

    candidate = url.strip()
    if not candidate:
        return False

    # Length is checked against the caller-supplied value as received.
    if len(url) > max_len:
        return False

    # Any embedded whitespace (space, tab, newline) is malformed for our purposes
    # and also defuses parser quirks that strip control characters.
    if any(ch.isspace() for ch in candidate):
        return False

    try:
        parts = urlsplit(candidate)
    except (ValueError, TypeError):
        return False

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if not parts.netloc:  # host must be present (rejects "http://", scheme-relative)
        return False

    return True

"""Capability allow-list for narrowed API tokens.

A row in ``user_api_tokens`` with a non-NULL ``capability`` is a token that is
NARROWER THAN ITS OWNER: it authenticates as the person who minted it, but it
may only reach the handful of paths its capability names. Everywhere else it is
refused — not "returns nothing useful", refused.

The list below is the whole enforcement surface, and it is a DENY-list by
construction: a capability maps to an explicit frozenset of paths, an unknown
capability maps to nothing, and both mean "no". An endpoint shipped next year
is refused to every scoped token until someone deliberately widens this file —
which is the only ordering that survives a codebase growing faster than anyone
re-audits it.

Pure module: no FastAPI, no DB, no settings. ``app/deps.py`` calls
``is_path_allowed`` once per request, inside ``get_current_principal``, so no
route can forget to check.
"""
from __future__ import annotations

# The capability minted for the iOS Shortcut / share-sheet import path.
IMPORT = "import"

# The path the import endpoint is mounted at. Kept here rather than imported
# from the router so this module stays dependency-free and the allow-list is
# readable in one screen.
IMPORT_TRANSCRIPT_PATH = "/v1/import/transcript"

# Token prefixes, so an operator reading a log or a leaked file can tell at a
# glance that a credential is narrow. `xbt_` (unrestricted) is deliberately NOT
# in here — its absence of a capability is what makes it full-access.
TOKEN_PREFIX = {IMPORT: "xbi_"}

SCOPED_TOKEN_PREFIXES: tuple[str, ...] = tuple(sorted(TOKEN_PREFIX.values()))

ALLOWED_PATHS: dict[str, frozenset[str]] = {
    IMPORT: frozenset({IMPORT_TRANSCRIPT_PATH}),
}

KNOWN_CAPABILITIES: frozenset[str] = frozenset(ALLOWED_PATHS)


def _normalise(path: str) -> str:
    if not isinstance(path, str) or not path:
        return ""
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def is_path_allowed(capability: str | None, path: str | None) -> bool:
    """Return True iff a token carrying ``capability`` may reach ``path``.

    ``capability is None`` is an unrestricted token — always True; this
    function is not an authorisation check for ordinary tokens, only the
    narrowing gate for scoped ones.

    Everything else fails closed: an unknown capability, an empty path, a path
    outside the capability's set.
    """
    if capability is None:
        return True
    allowed = ALLOWED_PATHS.get(capability)
    if not allowed:
        return False
    return _normalise(path or "") in allowed


def capability_for_prefix(token: str) -> str | None:
    """Map a raw token to the capability its prefix advertises, if any.

    Advisory only — the DATABASE row is the authority on what a token may do.
    Used at mint time to pick the prefix, and by the auth path to decide which
    lookup branch a token belongs to.
    """
    if not isinstance(token, str):
        return None
    for capability, prefix in TOKEN_PREFIX.items():
        if token.startswith(prefix):
            return capability
    return None

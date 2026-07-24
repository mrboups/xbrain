"""Pure helpers for the collaborative board endpoints (Phase 26a BOARD-01, D-26-03).

This module carries NO ``router`` symbol on purpose — exactly like ``media_helpers.py``
and for the same reason: unit tests import it without triggering FastAPI route
registration (which requires python-multipart and other runtime deps that are not
installed outside Docker).

The board token is the media token's shape with ``board_id`` in place of ``item_id``,
signed with the SAME ``settings.BRIDGE_SHARED_SECRET`` (no new secret enters the .env).
Claim set, exact keys and order::

    {"scope": "board",
     "board_id":     "<uuid str>",   # MUST equal the Hocuspocus documentName
     "team_scope":   "<team slug>",  # the ONLY source of team scope on the socket
     "team_id":      "<uuid str>",
     "sub":          "<source_user_id>",
     "display_name": "<str>",        # "" when absent
     "read_only":    false,
     "iat": <unix>, "exp": <unix + BOARD_TOKEN_TTL_S>}

CROSS-PLAN CONTRACT: these SAME claims are re-verified by the Hocuspocus server in
``onAuthenticate`` (``apps/hocuspocus/src/auth.mjs``, plan 26-03), which additionally
asserts ``claims.board_id === documentName`` — the third and final team-scope gate.
Any change to the key names, the algorithm, or the signing secret here is a BREAKING
change to that file and to the SPA (plan 26-04) that carries the token.

Never hand-roll HMAC: authlib is the repo's existing JOSE primitive (ASVS V6).
"""
from __future__ import annotations

import time


def mint_board_token(
    board_id: str,
    team_scope: str,
    team_id: str,
    sub: str,
    display_name: str = "",
    read_only: bool = False,
    ttl: int | None = None,
) -> str:
    """Mint a short-lived HS256 JWT authorising ONE board on ONE team.

    The token is the only thing the Hocuspocus server ever learns about team scope —
    it holds no database credentials and derives no scope of its own. A token minted
    for team A's board is therefore useless against team B's document, because
    ``onAuthenticate`` matches ``board_id`` against the requested ``documentName``.

    Args:
        board_id:     UUID string of the board. MUST equal the Hocuspocus documentName.
        team_scope:   Team slug embedded in the token.
        team_id:      UUID string of the team owning the board.
        sub:          The caller's ``source_user_id``.
        display_name: Presence label shown to other collaborators ("" when absent).
        read_only:    True issues a view-only connection (``connection.readOnly``).
        ttl:          Token lifetime in seconds; defaults to settings.BOARD_TOKEN_TTL_S.

    Returns:
        URL-safe ASCII JWT string.
    """
    from authlib.jose import jwt as authlib_jwt  # lazy — not installed outside Docker

    from app.config import settings  # local import avoids circular at module level

    now = int(time.time())
    lifetime = settings.BOARD_TOKEN_TTL_S if ttl is None else ttl
    payload = {
        "scope": "board",
        "board_id": board_id,
        "team_scope": team_scope,
        "team_id": team_id,
        "sub": sub,
        "display_name": display_name,
        "read_only": read_only,
        "iat": now,
        "exp": now + lifetime,
    }
    token = authlib_jwt.encode({"alg": "HS256"}, payload, settings.BRIDGE_SHARED_SECRET)
    return token.decode("ascii") if isinstance(token, bytes) else token


def verify_board_token(token: str, board_id: str) -> dict:
    """Decode and validate a board token for ``board_id``; return its claims.

    Mirrors ``verify_media_token`` exactly: authlib decode (signature) +
    ``claims.validate()`` (exp/iat/nbf) BEFORE any claim is read, then three
    assertions. Every rejection is a 403 — never a 500 — so a malformed or hostile
    token can never be mistaken for a server fault.

    Raises:
        fastapi.HTTPException(403): missing, expired, malformed, wrong signature,
            wrong scope, ``board_id`` mismatch, or missing ``team_scope``.

    Returns:
        The validated claim set as a plain dict.
    """
    from fastapi import HTTPException  # local import — fastapi always present
    from authlib.jose import jwt as authlib_jwt  # lazy

    from app.config import settings

    try:
        claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
        claims.validate()  # validates exp, iat, nbf
    except Exception as exc:
        raise HTTPException(403, f"invalid or expired board token: {exc}") from exc

    if claims.get("scope") != "board":
        raise HTTPException(403, "board token has wrong scope")
    if claims.get("board_id") != board_id:
        raise HTTPException(403, "board token board_id mismatch")
    if not claims.get("team_scope"):
        raise HTTPException(403, "board token missing team_scope")
    return dict(claims)

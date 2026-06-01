"""Mint short-lived HS256 bridge JWTs for mcp-brain internal calls.

Payload shape mirrors apps/mcp-gateway/app/aggregate.py::_mint_bridge_jwt exactly
so memory-api's verify_bridge_jwt accepts them (same shared secret, same alg, same claims).
"""
import time

from authlib.jose import jwt as authlib_jwt


def mint_bridge_jwt(*, secret: str, team_scope: str, sub: str, ttl: int = 300) -> str:
    """Return a signed HS256 JWT string accepted by memory-api as a bridge-scope token.

    Args:
        secret:     BRIDGE_SHARED_SECRET shared between all internal services.
        team_scope: Team slug to embed in the JWT (also checked by X-Team-Scope header).
        sub:        Subject identifier — typically "email:<address>" for LibreChat users.
        ttl:        Token lifetime in seconds (default 300 = 5 minutes).

    Returns:
        JWT string (three dot-separated base64url segments).
    """
    now = int(time.time())
    payload = {
        "iss": "mcp-brain",
        "sub": sub,
        "scope": "bridge",
        "team_scope": team_scope,
        "iat": now,
        "exp": now + ttl,
    }
    token = authlib_jwt.encode({"alg": "HS256"}, payload, secret)
    return token.decode("ascii") if isinstance(token, bytes) else token

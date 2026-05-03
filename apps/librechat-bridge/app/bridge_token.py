"""Generate short-lived service JWTs that memory-api accepts as bridge-scope."""

import time

from authlib.jose import jwt

from app.config import settings


def make_bridge_jwt(sub: str, team_scope: str, ttl_seconds: int = 300) -> str:
    """Sign a HS256 JWT memory-api will verify with the same shared secret.

    Claims:
      iss = librechat-bridge
      sub = the OIDC sub of the user the bridge is acting on behalf of
      team_scope = team to write into
      scope = "bridge" (required by memory-api auth check)
    """
    now = int(time.time())
    header = {"alg": settings.JWT_ALGORITHM}
    payload = {
        "iss": "librechat-bridge",
        "sub": sub,
        "team_scope": team_scope,
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(header, payload, settings.BRIDGE_SHARED_SECRET).decode("ascii")

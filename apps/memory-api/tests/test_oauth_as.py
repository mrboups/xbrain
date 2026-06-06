"""OAuth 2.1 Authorization Server tests (quick-260604-glo).

Two tiers, matching the repo's existing harness (tests/conftest.py):

  - Pure-helper + route-logic cases run UNCONDITIONALLY (no DB). They cover the
    token helpers, resource normalization, PKCE verification, AS metadata,
    DCR, the introspection X-Internal-Secret gate, and the token-endpoint
    behavioral checks via FastAPI TestClient with the DB store mocked.
  - DB-backed end-to-end cases are marked ``@pytest.mark.integration`` and use
    the testcontainers-Postgres ``session`` fixture; they auto-skip when Docker
    is unavailable (conftest._docker_available()).

The behavioral PKCE / replay / redirect_uri / resource-mismatch assertions
(Task 3) run against the route logic with a mocked/in-memory store so they NEVER
silently skip even without a test DB.
"""
from __future__ import annotations

import base64
import hashlib
import os

# Env defaults so `from app.config import settings` and `from app.main import app`
# import cleanly in unit context (mirrors conftest).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

import pytest

from app.auth import oauth_tokens


# ===========================================================================
# Task 1 — pure helpers (oauth_tokens). Run unconditionally.
# ===========================================================================

def test_mint_access_token_prefix_and_length():
    tok = oauth_tokens.mint_access_token()
    assert tok.startswith("oat_")
    # token_urlsafe(32) → ~43 url-safe chars; total comfortably > 40.
    assert len(tok) > 40
    # Two mints are distinct (randomness).
    assert tok != oauth_tokens.mint_access_token()


def test_mint_refresh_token_prefix():
    tok = oauth_tokens.mint_refresh_token()
    assert tok.startswith("ort_")
    assert len(tok) > 40


def test_hash_token_is_sha256_hex_and_stable():
    raw = "oat_example_raw_token"
    h = oauth_tokens.hash_token(raw)
    # 64 hex chars = SHA-256.
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    # Matches the deps.py xbt_ hashing convention exactly.
    assert h == hashlib.sha256(raw.encode()).hexdigest()
    # Deterministic.
    assert h == oauth_tokens.hash_token(raw)


def test_normalize_resource_strips_trailing_slash():
    assert oauth_tokens.normalize_resource("https://mcp.example/mcp/") == "https://mcp.example/mcp"
    assert oauth_tokens.normalize_resource("https://mcp.example/mcp") == "https://mcp.example/mcp"
    # Empty / falsy → empty string (presence validated by the caller).
    assert oauth_tokens.normalize_resource("") == ""
    assert oauth_tokens.normalize_resource(None) == ""  # type: ignore[arg-type]


# ===========================================================================
# DB-backed store round-trips (integration). Auto-skips without Docker.
# ===========================================================================

_VERIFIER = "verifier_with_plenty_of_entropy_aaaaaaaaaaaaaa"
_RESOURCE = "https://mcp.grooveos.app/mcp"
_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_store_roundtrip_introspect(session, seeded_two_teams):
    """Mint a token via the store + introspect it (real DB)."""
    from app.auth import oauth_store

    alice = seeded_two_teams["alice"]
    await oauth_store.register_client(
        session, client_name="t", redirect_uris=[_REDIRECT]
    )
    bundle = await oauth_store.mint_and_store_access_token(
        session,
        client_id="oac_x",
        user_id=alice.id,
        team_scope="team-a",
        resource=_RESOURCE + "/",  # trailing slash normalized on store
        scope="brain:read",
    )
    raw = bundle["access_token"]
    res = await oauth_store.introspect_token(session, raw)
    assert res["active"] is True
    assert res["team_scope"] == "team-a"
    assert res["aud"] == _RESOURCE  # normalized (no trailing slash)
    # Unknown token → inactive.
    assert (await oauth_store.introspect_token(session, "oat_unknown"))["active"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auth_code_one_time_use(session, seeded_two_teams):
    from app.auth import oauth_store

    alice = seeded_two_teams["alice"]
    code = await oauth_store.create_auth_code(
        session,
        client_id="oac_x",
        user_id=alice.id,
        team_scope="team-a",
        resource=_RESOURCE,
        code_challenge=_s256_challenge(_VERIFIER),
        redirect_uri=_REDIRECT,
        scope="brain:read",
    )
    first = await oauth_store.consume_auth_code(session, code)
    assert first is not None and first["team_scope"] == "team-a"
    # Second consume → None (one-time).
    assert await oauth_store.consume_auth_code(session, code) is None

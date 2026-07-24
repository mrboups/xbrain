"""Board token wire contract — Phase 26a BOARD-01 (plan 26-02, Task 2).

A PURE UNIT test: no DB, no Docker, and deliberately no integration marker. It must run
everywhere, because what it pins is a CROSS-PLAN CONTRACT — plan 26-03's Node
`onAuthenticate` verifier decodes exactly these claims with exactly this secret and
algorithm, and plan 26-04's SPA carries the token. A silent change to a key name here
would break the socket at runtime with nothing failing in CI.

The rejection cases are the load-bearing half: every one asserts `status_code == 403`
so no branch can quietly degrade into a 500 (a 500 on a hostile token is an outage,
and it hides the rejection).
"""

import time

import pytest
from authlib.jose import jwt as authlib_jwt
from fastapi import HTTPException

from app.config import settings
from app.routes.board_helpers import mint_board_token, verify_board_token
from app.routes.media_helpers import mint_media_token, verify_media_token

BOARD_ID = "11111111-1111-4111-8111-111111111111"
OTHER_BOARD_ID = "22222222-2222-4222-8222-222222222222"
TEAM_ID = "33333333-3333-4333-8333-333333333333"


def _mint(**overrides) -> str:
    kwargs = {
        "board_id": BOARD_ID,
        "team_scope": "team-a",
        "team_id": TEAM_ID,
        "sub": "alice-sub",
        "display_name": "Alice",
    }
    kwargs.update(overrides)
    return mint_board_token(**kwargs)


# ── the claim set (what 26-03's Node verifier reads) ─────────────────────────


def test_minted_token_carries_the_exact_claim_set():
    """Every key in the documented claim set, with the expected value."""
    token = _mint()
    claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)

    assert claims["scope"] == "board"
    assert claims["board_id"] == BOARD_ID
    assert claims["team_scope"] == "team-a"
    assert claims["team_id"] == TEAM_ID
    assert claims["sub"] == "alice-sub"
    assert claims["display_name"] == "Alice"
    assert claims["read_only"] is False
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    # No stray claims: the Node verifier is written against exactly this set.
    assert set(claims.keys()) == {
        "scope",
        "board_id",
        "team_scope",
        "team_id",
        "sub",
        "display_name",
        "read_only",
        "iat",
        "exp",
    }


def test_token_is_hs256_over_the_bridge_shared_secret():
    """HS256, and the SAME secret mint_media_token uses — no new secret in the .env."""
    token = _mint()
    header_claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert header_claims.header["alg"] == "HS256"


def test_defaults_are_empty_display_name_and_not_read_only():
    token = mint_board_token(
        board_id=BOARD_ID, team_scope="team-a", team_id=TEAM_ID, sub="bob-sub"
    )
    claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["display_name"] == ""
    assert claims["read_only"] is False


def test_ttl_defaults_to_the_configured_board_token_ttl():
    token = _mint()
    claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["exp"] - claims["iat"] == settings.BOARD_TOKEN_TTL_S


def test_read_only_claim_round_trips():
    token = _mint(read_only=True)
    claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["read_only"] is True


# ── verify: the happy path ───────────────────────────────────────────────────


def test_verify_returns_claims_for_the_matching_board():
    token = _mint()
    claims = verify_board_token(token, BOARD_ID)
    assert claims["team_scope"] == "team-a"
    assert claims["board_id"] == BOARD_ID
    assert claims["sub"] == "alice-sub"


# ── verify: the rejections (all 403, never 500) ──────────────────────────────


def test_verify_rejects_wrong_board():
    """A token minted for board A must not open board B — the cross-board /
    cross-team rejection, expressed at the Python level. 26-03 re-asserts the same
    thing against the real `documentName` on the socket."""
    token = _mint()
    with pytest.raises(HTTPException) as exc:
        verify_board_token(token, OTHER_BOARD_ID)
    assert exc.value.status_code == 403
    # codex P2: all rejections share ONE generic message (no scope/board/team oracle).
    assert "invalid or expired board token" in str(exc.value.detail)


def test_verify_rejects_wrong_secret():
    """A self-signed token — the forged-token case (T-26-05)."""
    now = int(time.time())
    forged = authlib_jwt.encode(
        {"alg": "HS256"},
        {
            "scope": "board",
            "board_id": BOARD_ID,
            "team_scope": "team-a",
            "team_id": TEAM_ID,
            "sub": "mallory-sub",
            "display_name": "Mallory",
            "read_only": False,
            "iat": now,
            "exp": now + 3600,
        },
        "not-the-bridge-shared-secret",
    )
    with pytest.raises(HTTPException) as exc:
        verify_board_token(forged, BOARD_ID)
    assert exc.value.status_code == 403


def test_verify_rejects_expired_token():
    token = _mint(ttl=-1)
    with pytest.raises(HTTPException) as exc:
        verify_board_token(token, BOARD_ID)
    assert exc.value.status_code == 403


def test_verify_rejects_token_without_exp():
    """codex P2: a token with NO exp claim (crafted by any other holder of
    BRIDGE_SHARED_SECRET) must be rejected — exp is essential, not merely validated when
    present. Signed with the REAL secret, so only the missing-exp check can reject it."""
    now = int(time.time())
    no_exp = authlib_jwt.encode(
        {"alg": "HS256"},
        {
            "scope": "board",
            "board_id": BOARD_ID,
            "team_scope": "team-a",
            "team_id": TEAM_ID,
            "sub": "no-exp-sub",
            "display_name": "No Exp",
            "read_only": False,
            "iat": now,
            # deliberately NO "exp"
        },
        settings.BRIDGE_SHARED_SECRET,
    )
    with pytest.raises(HTTPException) as exc:
        verify_board_token(no_exp, BOARD_ID)
    assert exc.value.status_code == 403
    assert "invalid or expired board token" in str(exc.value.detail)


def test_verify_rejects_a_media_token():
    """The two token families are NOT interchangeable: a media token carries
    scope="media" and must never open a board."""
    media_token = mint_media_token(BOARD_ID, "team-a")
    with pytest.raises(HTTPException) as exc:
        verify_board_token(media_token, BOARD_ID)
    assert exc.value.status_code == 403
    assert "invalid or expired board token" in str(exc.value.detail)


def test_media_verifier_rejects_a_board_token():
    """...and the reverse direction, so neither family can borrow the other's gate."""
    token = _mint()
    with pytest.raises(HTTPException) as exc:
        verify_media_token(token, BOARD_ID)
    assert exc.value.status_code == 403


def test_verify_rejects_a_token_without_team_scope():
    """team_scope is the ONLY source of team scope on the socket — an empty one is
    a rejection, never a silent default."""
    now = int(time.time())
    scopeless = authlib_jwt.encode(
        {"alg": "HS256"},
        {
            "scope": "board",
            "board_id": BOARD_ID,
            "team_scope": "",
            "team_id": TEAM_ID,
            "sub": "alice-sub",
            "display_name": "Alice",
            "read_only": False,
            "iat": now,
            "exp": now + 3600,
        },
        settings.BRIDGE_SHARED_SECRET,
    )
    with pytest.raises(HTTPException) as exc:
        verify_board_token(scopeless, BOARD_ID)
    assert exc.value.status_code == 403
    assert "invalid or expired board token" in str(exc.value.detail)


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b.c", "Bearer x.y.z"])
def test_verify_rejects_garbage_with_403_not_500(garbage):
    with pytest.raises(HTTPException) as exc:
        verify_board_token(garbage, BOARD_ID)
    assert exc.value.status_code == 403

"""Bridge JWT token round-trip + claim verification."""

import time

import pytest
from authlib.jose import JoseError, jwt

from app.bridge_token import make_bridge_jwt
from app.config import settings


def test_make_bridge_jwt_returns_decodable_token():
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-a")
    assert isinstance(token, str)
    assert len(token) > 50


def test_bridge_jwt_has_iss_librechat_bridge():
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-a")
    claims = jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["iss"] == "librechat-bridge"


def test_bridge_jwt_default_ttl_is_5min():
    before = int(time.time())
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-a")
    claims = jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["exp"] >= before + 290  # ~300s
    assert claims["exp"] <= before + 310


def test_bridge_jwt_includes_team_scope_claim():
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-xyz")
    claims = jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["team_scope"] == "team-xyz"
    assert claims["sub"] == "alice-sub"
    assert claims["scope"] == "bridge"


def test_bridge_jwt_round_trip_decode_validate():
    """Same secret → claims.validate() passes."""
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-a", ttl_seconds=60)
    claims = jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    claims.validate()  # would raise if exp/iat invalid


def test_bridge_jwt_with_wrong_secret_decodes_fail():
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-a")
    with pytest.raises(JoseError):
        jwt.decode(token, "different-secret")


def test_bridge_jwt_short_ttl_expires_quickly():
    token = make_bridge_jwt(sub="alice-sub", team_scope="team-a", ttl_seconds=-1)
    claims = jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    with pytest.raises(JoseError):
        claims.validate()

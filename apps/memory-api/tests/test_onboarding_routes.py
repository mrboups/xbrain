"""Integration tests for onboarding endpoints."""

import os
import time
import pytest
from authlib.jose import jwt

pytestmark = pytest.mark.integration

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("OAUTH_CREDENTIALS_ENCRYPTION_KEY", "")
os.environ.setdefault("FERNET_KEY", "")


def make_onboarding_jwt(email: str, secret: str = "test-bridge-secret-do-not-use-in-prod") -> str:
    now = int(time.time())
    payload = {
        "iss": "librechat-onboarding",
        "sub": "mongo-id-placeholder",
        "email": email,
        "scope": "bridge",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")


@pytest.mark.asyncio
async def test_my_team_returns_204_when_no_team(client):
    token = make_onboarding_jwt("newuser@test.local")
    resp = await client.get("/v1/teams/my-team", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_my_team_returns_200_when_has_team(client, session):
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    user = await users_repo.get_or_create_user(
        session, source_user_id="email:member@test.local", email="member@test.local"
    )
    await teams_repo.create_team(
        session, slug="existing-team-x", display_name="Existing", creator_user_id=user.id
    )
    await session.commit()

    token = make_onboarding_jwt("member@test.local")
    resp = await client.get("/v1/teams/my-team", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["slug"] == "existing-team-x"


@pytest.mark.asyncio
async def test_search_teams(client, session):
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="creator-sub", email="creator@test.local"
    )
    await teams_repo.create_team(
        session, slug="searchable-team", display_name="Searchable Team", creator_user_id=creator.id
    )
    await session.commit()

    token = make_onboarding_jwt("searcher@test.local")
    resp = await client.get(
        "/v1/teams/search?name=searchable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    results = resp.json()
    assert any(t["slug"] == "searchable-team" for t in results)


@pytest.mark.asyncio
async def test_self_create_team(client):
    token = make_onboarding_jwt("founder@test.local")
    resp = await client.post(
        "/v1/teams/self",
        json={"slug": "new-team-phase8", "display_name": "New Team Phase8", "visibility": "open"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "new-team-phase8"


@pytest.mark.asyncio
async def test_join_open_team(client, session):
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    owner = await users_repo.get_or_create_user(
        session, source_user_id="owner-sub", email="owner@test.local"
    )
    team = await teams_repo.create_team(
        session, slug="open-team-x", display_name="Open Team", creator_user_id=owner.id,
        visibility="open",
    )
    await session.commit()

    token = make_onboarding_jwt("joiner@test.local")
    resp = await client.post(
        f"/v1/teams/{team.id}/join",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_join_closed_team_returns_403(client, session):
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    owner = await users_repo.get_or_create_user(
        session, source_user_id="owner2-sub", email="owner2@test.local"
    )
    team = await teams_repo.create_team(
        session, slug="closed-team-x", display_name="Closed Team", creator_user_id=owner.id,
        visibility="closed",
    )
    await session.commit()

    token = make_onboarding_jwt("joiner2@test.local")
    resp = await client.post(
        f"/v1/teams/{team.id}/join",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

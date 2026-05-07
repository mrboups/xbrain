"""Integration tests for new team repo functions (requires Docker for testcontainers)."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_search_teams_by_name(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="s1", email="u1@test.local"
    )
    await repo.create_team(
        session, slug="acme-labs", display_name="Acme Labs", creator_user_id=creator.id
    )
    await session.flush()

    results = await repo.search_teams(session, query="acme")
    assert any(t.slug == "acme-labs" for t in results)


@pytest.mark.asyncio
async def test_get_first_team_for_user_returns_none_when_no_team(session):
    from app.repos import users as users_repo

    user = await users_repo.get_or_create_user(
        session, source_user_id="s2", email="u2@test.local"
    )
    from app.repos import teams as repo
    result = await repo.get_first_team_for_user(session, user_id=user.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_first_team_for_user_returns_team_when_member(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    user = await users_repo.get_or_create_user(
        session, source_user_id="s3", email="u3@test.local"
    )
    await repo.create_team(
        session, slug="my-team-x", display_name="My Team X", creator_user_id=user.id
    )
    await session.flush()

    result = await repo.get_first_team_for_user(session, user_id=user.id)
    assert result is not None
    assert result.slug == "my-team-x"


@pytest.mark.asyncio
async def test_create_join_request_is_idempotent(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    alice = await users_repo.get_or_create_user(
        session, source_user_id="s4", email="alice@test.local"
    )
    bob = await users_repo.get_or_create_user(
        session, source_user_id="s5", email="bob@test.local"
    )
    team = await repo.create_team(
        session, slug="closed-team", display_name="Closed", creator_user_id=alice.id
    )
    await session.flush()

    r1 = await repo.create_join_request(session, team_id=team.id, user_id=bob.id)
    r2 = await repo.create_join_request(session, team_id=team.id, user_id=bob.id)
    assert r1.id == r2.id  # idempotent
    assert r1.status == "pending"


@pytest.mark.asyncio
async def test_get_teams_with_github_org(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="s6", email="u6@test.local"
    )
    team_with = await repo.create_team(
        session, slug="github-team", display_name="GitHub Team", creator_user_id=creator.id
    )
    team_with.github_org = "your-github-org"
    await repo.create_team(
        session, slug="no-github-team", display_name="No GitHub", creator_user_id=creator.id
    )
    await session.flush()

    results = await repo.get_teams_with_github_org(session)
    slugs = [t.slug for t in results]
    assert "github-team" in slugs
    assert "no-github-team" not in slugs


@pytest.mark.asyncio
async def test_upsert_team_api_key_insert_and_update(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="s7", email="u7@test.local"
    )
    team = await repo.create_team(
        session, slug="apikey-team", display_name="API Key Team", creator_user_id=creator.id
    )
    await session.flush()

    # Insert
    k1 = await repo.upsert_team_api_key(
        session, team_id=team.id, provider="anthropic", key_enc="enc-v1"
    )
    assert k1.key_enc == "enc-v1"

    # Update (upsert same provider)
    k2 = await repo.upsert_team_api_key(
        session, team_id=team.id, provider="anthropic", key_enc="enc-v2"
    )
    assert k2.id == k1.id  # same row
    assert k2.key_enc == "enc-v2"


@pytest.mark.asyncio
async def test_list_team_api_keys(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="s8", email="u8@test.local"
    )
    team = await repo.create_team(
        session, slug="list-keys-team", display_name="List Keys Team", creator_user_id=creator.id
    )
    await session.flush()

    await repo.upsert_team_api_key(session, team_id=team.id, provider="anthropic", key_enc="enc-a")
    await repo.upsert_team_api_key(session, team_id=team.id, provider="openai", key_enc="enc-o")

    keys = await repo.list_team_api_keys(session, team_id=team.id)
    providers = {k.provider for k in keys}
    assert providers == {"anthropic", "openai"}

"""The avatar: an id on the row, a fresh signed URL on every read.

The avatar deliberately owns no storage of its own. A client uploads through the
media path that already exists (POST /v1/media/upload → MinIO + a media
memory_item + a signed URL) and then points its profile at the returned item id.
What these tests lock down is the seam:

  - **the URL is never persisted.** Only the id and the team scope it lives in
    are stored; the signed token expires in an hour, so a saved URL is a link
    that works until it silently stops. Two reads must produce two tokens.
  - **the token is minted from the STORED scope, not the reader's.** A person
    belongs to several teams and their avatar lives in one of them. The read
    endpoint takes no team scope at all, and the picture still loads.
  - **a blocked member gains no new surface.** PUT resolves its scope through
    `get_team_scope`, the same gate `team_chat.py::_resolve_team_and_check_membership`
    matches, which refuses a member whose `blocked_at` is set. Asserted both
    structurally (the dependency is in the route's chain) and behaviourally.
  - **DELETE is NOT team-scoped**, on purpose: taking your own photo down must
    not require still being in good standing in the team that happens to hold it.

No Docker: the media provider and the team-scope gate are dependency-overridden,
and the routes are the real ones over ASGI.
"""
from __future__ import annotations

import types
import uuid

import httpx
import pytest
import pytest_asyncio

BOB_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
IMAGE_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
DOCUMENT_ID = uuid.UUID("99999999-8888-4777-8666-555555555555")
OTHER_TEAM_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


class _StubProvider:
    """A team-scoped media store. `get` returns None outside the item's own team.

    That mirrors the real providers (native_stub, NativeProvider and Mem0Provider
    all compare `item.team_scope` before returning), which is what makes "an id
    from another team is a 404" a property of the route rather than of this stub.
    """

    def __init__(self):
        self._items = {
            (str(IMAGE_ID), "team-a"): {"media": {"key": "media/team-a/x.png", "mime": "image/png"}},
            (str(DOCUMENT_ID), "team-a"): {"media": {"key": "media/team-a/x.pdf", "mime": "application/pdf"}},
            (str(OTHER_TEAM_ID), "team-b"): {"media": {"key": "media/team-b/y.png", "mime": "image/png"}},
        }

    async def get(self, item_id: str, *, team_scope: str):
        meta = self._items.get((item_id, team_scope))
        if meta is None:
            return None
        return types.SimpleNamespace(id=item_id, team_scope=team_scope, metadata=meta)


class _RecordingSession:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    async def get(self, _model, pk):
        return self._rows.get(pk)

    async def commit(self):
        self.commits += 1


def _make_bob():
    from app.models.user import User

    return User(
        id=BOB_ID,
        source_user_id="bob-sub",
        email="bob@test.local",
        display_name="Bob",
        preferred_name=None,
        bio=None,
        avatar_media_id=None,
        avatar_media_team_scope=None,
    )


@pytest.fixture
def bob():
    return _make_bob()


@pytest_asyncio.fixture
async def avatar_client(bob):
    """(client, set_scope) — set_scope(None) makes the team-scope gate refuse."""
    from app.deps import (
        get_current_principal,
        get_memory_provider,
        get_session,
        get_team_scope,
    )
    from app.main import create_app
    from fastapi import HTTPException

    app = create_app("oss")
    session = _RecordingSession({BOB_ID: bob})
    state = {"scope": "team-a"}

    async def _override_session():
        return session

    async def _override_principal():
        return {"kind": "user", "user": bob, "sub": "bob-sub", "github_is_org_member": None}

    async def _override_team_scope():
        if state["scope"] is None:
            # What deps.get_team_scope raises for a blocked member (Phase 10
            # GHA-03) and for a non-member alike.
            raise HTTPException(403, "Member blocked from team team-a")
        return state["scope"]

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_principal] = _override_principal
    app.dependency_overrides[get_team_scope] = _override_team_scope
    app.dependency_overrides[get_memory_provider] = lambda: _StubProvider()

    def set_scope(scope):
        state["scope"] = scope

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, set_scope


# ── Setting it ──────────────────────────────────────────────────────────────


async def test_setting_an_avatar_stores_the_id_and_the_scope_it_lives_in(
    avatar_client, bob
):
    client, _ = avatar_client

    r = await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})
    assert r.status_code == 200, r.text

    assert bob.avatar_media_id == IMAGE_ID
    assert bob.avatar_media_team_scope == "team-a", (
        "the scope the avatar lives in must be recorded at write time — without "
        "it a reader in another team cannot be handed a mintable URL"
    )
    body = r.json()
    assert body["avatar_media_id"] == str(IMAGE_ID)
    assert body["avatar_url"].startswith(f"/v1/media/{IMAGE_ID}/img?t=")


async def test_the_stored_value_is_an_id_and_never_a_url(avatar_client, bob):
    """A signed URL expires. Persisting one ships a link that silently stops."""
    client, _ = avatar_client
    await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})

    for column, value in vars(bob).items():
        if column.startswith("_"):
            continue
        assert "/v1/media/" not in str(value), (
            f"users.{column} holds a media URL ({value!r}) — only the id and its "
            f"team scope may be stored; the URL is minted per read"
        )
        assert "?t=" not in str(value)


async def test_a_media_item_from_another_team_is_not_found(avatar_client, bob):
    """The provider read is team-scoped, so an id from team-b is a 404 in team-a."""
    client, _ = avatar_client

    r = await client.put(
        "/v1/me/profile/avatar", json={"media_item_id": str(OTHER_TEAM_ID)}
    )
    assert r.status_code == 404, r.text
    assert bob.avatar_media_id is None


async def test_an_unknown_id_is_not_found_and_gives_nothing_away(avatar_client, bob):
    client, _ = avatar_client

    unknown = await client.put(
        "/v1/me/profile/avatar",
        json={"media_item_id": "deadbeef-dead-4bee-8bee-deadbeefdead"},
    )
    other_team = await client.put(
        "/v1/me/profile/avatar", json={"media_item_id": str(OTHER_TEAM_ID)}
    )
    assert unknown.status_code == other_team.status_code == 404
    assert unknown.json()["detail"] == other_team.json()["detail"], (
        "an id that exists in another team must be indistinguishable from one "
        "that exists nowhere — otherwise this endpoint is an id oracle"
    )
    assert bob.avatar_media_id is None


async def test_a_non_image_is_refused(avatar_client, bob):
    client, _ = avatar_client

    r = await client.put(
        "/v1/me/profile/avatar", json={"media_item_id": str(DOCUMENT_ID)}
    )
    assert r.status_code == 422, r.text
    assert "image" in r.json()["detail"].lower()
    assert bob.avatar_media_id is None


async def test_the_body_accepts_nothing_but_the_media_id(avatar_client, bob):
    client, _ = avatar_client

    for hostile in (
        {"media_item_id": str(IMAGE_ID), "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        {"media_item_id": str(IMAGE_ID), "team_scope": "team-b"},
        {"media_item_id": str(IMAGE_ID), "avatar_url": "https://evil.example/x.png"},
        {"avatar_url": "https://evil.example/x.png"},
        {"media_item_id": "not-a-uuid"},
        {},
    ):
        r = await client.put("/v1/me/profile/avatar", json=hostile)
        assert r.status_code == 422, f"{hostile} was accepted: {r.text}"
    assert bob.avatar_media_id is None


# ── Blocked members gain no surface ─────────────────────────────────────────


def test_the_put_resolves_its_scope_through_the_membership_gate():
    """Structural: `get_team_scope` is in the PUT's dependency chain.

    That dependency is where membership and `team_members.blocked_at` are
    enforced (deps.py, Phase 10 GHA-03). A future edit that takes the scope from
    a body field or a header directly would drop the block check silently — this
    project has already shipped that bypass once.
    """
    from app.deps import get_team_scope
    from app.main import create_app

    def _all_deps(dependant):
        for sub in dependant.dependencies:
            yield sub.call
            yield from _all_deps(sub)

    app = create_app("oss")
    put_routes = [
        r
        for r in app.routes
        if getattr(r, "path", "") == "/v1/me/profile/avatar" and "PUT" in getattr(r, "methods", set())
    ]
    assert put_routes, "PUT /v1/me/profile/avatar must be mounted"
    assert get_team_scope in set(_all_deps(put_routes[0].dependant))


async def test_a_blocked_member_cannot_set_an_avatar(avatar_client, bob):
    client, set_scope = avatar_client
    set_scope(None)  # get_team_scope refuses, as it does for blocked_at

    r = await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})
    assert r.status_code == 403, r.text
    assert bob.avatar_media_id is None


# ── Removing it ─────────────────────────────────────────────────────────────


async def test_removing_an_avatar_clears_both_columns(avatar_client, bob):
    client, _ = avatar_client
    await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})

    r = await client.delete("/v1/me/profile/avatar")
    assert r.status_code == 200, r.text
    assert bob.avatar_media_id is None
    assert bob.avatar_media_team_scope is None, (
        "leaving a stale scope behind would mint a URL for an avatar that is gone"
    )
    assert r.json()["avatar_url"] is None


async def test_removing_an_avatar_is_idempotent(avatar_client, bob):
    client, _ = avatar_client

    first = await client.delete("/v1/me/profile/avatar")
    second = await client.delete("/v1/me/profile/avatar")
    assert first.status_code == second.status_code == 200
    assert second.json()["avatar_url"] is None


async def test_a_blocked_member_can_still_take_their_own_photo_down(avatar_client, bob):
    """DELETE is not team-scoped, and this is why.

    Someone blocked from — or no longer in — the team that happens to store their
    avatar must still be able to remove it. Requiring the scope here would leave
    their picture up with no way for them to take it down.
    """
    client, set_scope = avatar_client
    await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})
    set_scope(None)

    r = await client.delete("/v1/me/profile/avatar")
    assert r.status_code == 200, r.text
    assert bob.avatar_media_id is None


async def test_delete_needs_no_team_scope_header():
    """No X-Team-Scope on the request at all, and it still works."""
    from app.deps import get_current_principal, get_session
    from app.main import create_app

    bob = _make_bob()
    bob.avatar_media_id = IMAGE_ID
    bob.avatar_media_team_scope = "team-a"

    app = create_app("oss")
    session = _RecordingSession({BOB_ID: bob})
    app.dependency_overrides[get_session] = lambda: session

    async def _principal():
        return {"kind": "user", "user": bob, "sub": "bob-sub", "github_is_org_member": None}

    app.dependency_overrides[get_current_principal] = _principal

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.delete("/v1/me/profile/avatar")
    assert r.status_code == 200, r.text
    assert bob.avatar_media_id is None


# ── Reading it back ─────────────────────────────────────────────────────────


async def test_every_read_mints_a_fresh_url(avatar_client):
    import time

    client, _ = avatar_client
    await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})

    first = (await client.get("/v1/me/profile")).json()["avatar_url"]
    time.sleep(1.05)  # the JWT's iat has one-second resolution
    second = (await client.get("/v1/me/profile")).json()["avatar_url"]
    assert first != second, "the token must be minted per read, never stored"


async def test_the_minted_token_verifies_against_the_real_serve_endpoint(avatar_client):
    """Not a shape assertion: the REAL verifier accepts it, bound to this item."""
    from app.routes.media_helpers import verify_media_token

    client, _ = avatar_client
    await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})

    url = (await client.get("/v1/me/profile")).json()["avatar_url"]
    token = url.split("?t=", 1)[1]
    assert verify_media_token(token, str(IMAGE_ID)) == "team-a"

    with pytest.raises(Exception):
        verify_media_token(token, str(OTHER_TEAM_ID))


async def test_reading_the_profile_takes_no_team_scope_at_all(avatar_client):
    """A person in several teams loads their own avatar from any of them.

    GET /v1/me/profile has no scope input — no header, no query. The scope in the
    minted token comes from the row, so the picture resolves the same way whether
    the caller is currently looking at team-a or team-b, and the token's scope is
    never widened to make that work.
    """
    from app.routes.media_helpers import verify_media_token

    client, set_scope = avatar_client
    await client.put("/v1/me/profile/avatar", json={"media_item_id": str(IMAGE_ID)})

    # The team-scope gate now refuses everything; the profile read is unaffected.
    set_scope(None)
    r = await client.get("/v1/me/profile", headers={"X-Team-Scope": "team-b"})
    assert r.status_code == 200, r.text

    token = r.json()["avatar_url"].split("?t=", 1)[1]
    assert verify_media_token(token, str(IMAGE_ID)) == "team-a", (
        "the token carries the scope the avatar LIVES in, not the one the reader "
        "sent — anything else would either break the read or widen the token"
    )

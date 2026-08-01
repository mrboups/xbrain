"""A person reads and writes ONLY their own profile.

This is the test that matters. Everything else about a profile is cosmetic; this
is the part that, if it breaks, leaks one person's account into another's screen.

It is asserted two ways on purpose:

  **Structurally** — the route surface has nowhere to put someone else's id. No
  path parameter, no query parameter, no body field (`extra="forbid"`). An
  authorisation check that is merely correct today can be edited by someone who
  did not know why it was there; a route that cannot express the question cannot
  answer it wrongly. `test_no_profile_route_can_name_another_user` is that gate.

  **Behaviourally** — with two real accounts loaded, Bob's request reads Bob's
  row and writes Bob's row, and Alice's row is untouched no matter what Bob adds
  to the URL or the body.

These run WITHOUT Docker. The routes only ever call `session.get(User, pk)` and
`session.commit()`, so a recording stub stands in for Postgres and the REAL route
functions, the REAL pydantic bodies and the REAL app wiring are exercised end to
end over ASGI. The stub also records every primary key the routes ask for, which
is what turns "Bob saw the right data" into the stronger "Alice's row was never
even loaded".

Imports of `app.main` / `app.config` are lazy (inside fixtures and tests), matching
the convention documented in tests/test_edition_gating.py: a top-level import here
freezes the settings/engine singletons during collection and breaks every
Postgres-backed test that runs later in the same session.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

ALICE_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
BOB_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class _RecordingSession:
    """Stands in for AsyncSession: serves rows by primary key, records the asks.

    `get(model, pk)` is the ONLY read the profile routes perform, which is what
    makes this stub honest rather than a mock that agrees with whatever the code
    does — there is no filter expression to fake, and `requested_pks` is a
    complete record of every row the routes touched.
    """

    def __init__(self, rows: dict[uuid.UUID, object]):
        self._rows = rows
        self.requested_pks: list[object] = []
        self.commits = 0

    async def get(self, _model, pk):
        self.requested_pks.append(pk)
        return self._rows.get(pk)

    async def commit(self):
        self.commits += 1

    async def rollback(self):  # pragma: no cover - never reached in these tests
        pass


def _make_user(user_id: uuid.UUID, **kw):
    """A REAL User ORM instance (unattached). Real columns, real attribute names."""
    from app.models.user import User

    defaults = {
        "id": user_id,
        "source_user_id": f"sub-{user_id}",
        "email": f"{user_id.hex[:5]}@test.local",
        "display_name": None,
        "preferred_name": None,
        "bio": None,
        "avatar_media_id": None,
        "avatar_media_team_scope": None,
    }
    defaults.update(kw)
    return User(**defaults)


@pytest.fixture
def rows():
    """Two accounts with values that are trivially distinguishable in a payload."""
    alice = _make_user(
        ALICE_ID,
        email="alice@test.local",
        display_name="Alice",
        preferred_name="Alice The Admin",
        bio="Alice's private bio",
    )
    bob = _make_user(
        BOB_ID,
        email="bob@test.local",
        display_name="Bob",
        preferred_name="Bobby",
        bio="Bob's private bio",
    )
    return {ALICE_ID: alice, BOB_ID: bob}


@pytest.fixture
def stub_session(rows):
    return _RecordingSession(rows)


@pytest_asyncio.fixture
async def profile_client(stub_session, rows):
    """(client, as_user) — an ASGI client over the real app, on the stub session.

    `as_user(user_id, kind=...)` swaps which principal the next request carries,
    so one test can be Bob and then Alice without rebuilding the app.
    """
    from app.deps import get_current_principal, get_session
    from app.main import create_app

    app = create_app("oss")
    state: dict[str, object] = {"user_id": BOB_ID, "kind": "user"}

    async def _override_session():
        return stub_session

    async def _override_principal():
        kind = state["kind"]
        if kind == "bridge":
            return {"kind": "bridge", "sub": "svc", "team_scope": "team-a", "claims": {}}
        return {
            "kind": kind,
            # The principal carries the object authentication resolved, which is
            # not necessarily what the store still holds — that gap is real (an
            # account can be deleted mid-session) and one test exercises it.
            "user": state.get("principal_user") or rows[state["user_id"]],
            "sub": f"sub-{state['user_id']}",
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_principal] = _override_principal

    def as_user(user_id, kind: str = "user", principal_user=None):
        state["user_id"] = user_id
        state["kind"] = kind
        state["principal_user"] = principal_user

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, as_user


# ── The structural gate ─────────────────────────────────────────────────────


def test_no_profile_route_can_name_another_user():
    """No profile route has a parameter through which another user could be asked for.

    Not "the check rejects it" — there is nothing to check, because the question
    is unaskable. A path parameter (`/v1/users/{id}/profile`) or a query
    parameter (`?user_id=`) added later fails here, before it can ship.
    """
    import inspect

    from app.main import create_app

    app = create_app("oss")
    profile_routes = [
        r for r in app.routes if getattr(r, "path", "").startswith("/v1/me/profile")
    ]
    assert profile_routes, "the profile routes must be mounted"

    # Dependency-injected parameters are resolved from the caller's own token or
    # from the request; they cannot carry another user's identity. Anything else
    # in a handler signature is client-controlled input.
    allowed = {"body", "principal", "session", "team_scope", "provider"}
    for route in profile_routes:
        assert "{" not in route.path, (
            f"{route.path} has a path parameter — a profile route must not be "
            f"addressable by anything but the caller's own token"
        )
        params = set(inspect.signature(route.endpoint).parameters)
        assert params <= allowed, (
            f"{route.path} accepts {sorted(params - allowed)}, which is "
            f"client-controlled input on a route that must read only the caller's "
            f"own row"
        )


def test_the_profile_router_exposes_nothing_but_me_paths():
    from app.main import create_app
    from app.routes import me_profile

    app = create_app("oss")
    mounted = {
        r.path
        for r in app.routes
        if getattr(r, "endpoint", None)
        in {getattr(rt, "endpoint", None) for rt in me_profile.router.routes}
    }
    assert mounted, "the profile router must be mounted"
    for path in mounted:
        assert path.startswith("/v1/me/"), (
            f"{path} is served by the profile router but is not under /v1/me/ — "
            f"the profile surface is first-person only"
        )


def test_the_profile_is_core_in_every_edition():
    """A profile is not a paid feature."""
    from app.main import create_app

    for edition in ("oss", "saas"):
        paths = {r.path for r in create_app(edition).routes}
        assert "/v1/me/profile" in paths, f"missing under EDITION={edition}"


# ── The behavioural gate ────────────────────────────────────────────────────


async def test_a_person_reads_their_own_profile(profile_client, stub_session):
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.get("/v1/me/profile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(BOB_ID)
    assert body["email"] == "bob@test.local"
    assert body["preferred_name"] == "Bobby"
    assert body["bio"] == "Bob's private bio"
    assert body["label"] == "Bobby"

    # Alice's row was never even loaded.
    assert stub_session.requested_pks == [BOB_ID]


async def test_a_query_parameter_naming_another_user_changes_nothing(
    profile_client, stub_session
):
    """`?user_id=<alice>` is not a way in. The row loaded is still the caller's."""
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.get(
        f"/v1/me/profile?user_id={ALICE_ID}&id={ALICE_ID}&sub=sub-{ALICE_ID}"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(BOB_ID)
    assert "alice" not in r.text.lower(), (
        f"a query parameter must not reach another account: {r.text}"
    )
    assert stub_session.requested_pks == [BOB_ID]


async def test_a_body_field_naming_another_user_is_refused(profile_client, rows):
    """`extra="forbid"` turns "silently ignored" into a visible 422."""
    client, as_user = profile_client
    as_user(BOB_ID)

    for hostile in (
        {"user_id": str(ALICE_ID), "preferred_name": "Owned"},
        {"id": str(ALICE_ID), "bio": "Owned"},
        {"email": "attacker@test.local"},
        {"preferred_name": "Fine", "display_name": "Provider Override"},
    ):
        r = await client.patch("/v1/me/profile", json=hostile)
        assert r.status_code == 422, f"{hostile} should be refused, got {r.status_code}"

    assert rows[ALICE_ID].preferred_name == "Alice The Admin"
    assert rows[ALICE_ID].bio == "Alice's private bio"
    assert rows[BOB_ID].preferred_name == "Bobby"


async def test_a_patch_writes_only_to_the_callers_row(
    profile_client, rows, stub_session
):
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.patch(
        "/v1/me/profile", json={"preferred_name": "Bob Renamed", "bio": "New bio"}
    )
    assert r.status_code == 200, r.text
    assert rows[BOB_ID].preferred_name == "Bob Renamed"
    assert rows[BOB_ID].bio == "New bio"

    # Alice is exactly as she was, and her row was never loaded.
    assert rows[ALICE_ID].preferred_name == "Alice The Admin"
    assert rows[ALICE_ID].bio == "Alice's private bio"
    assert stub_session.requested_pks == [BOB_ID]
    assert stub_session.commits == 1


async def test_two_people_in_one_process_never_cross(profile_client, rows):
    """Alice and Bob edit in turn; neither sees or overwrites the other."""
    client, as_user = profile_client

    as_user(BOB_ID)
    assert (await client.patch("/v1/me/profile", json={"bio": "bob only"})).status_code == 200

    as_user(ALICE_ID)
    r = await client.get("/v1/me/profile")
    assert r.json()["email"] == "alice@test.local"
    assert r.json()["bio"] == "Alice's private bio", "Bob's write must not reach Alice"

    assert (await client.patch("/v1/me/profile", json={"bio": "alice only"})).status_code == 200

    as_user(BOB_ID)
    assert (await client.get("/v1/me/profile")).json()["bio"] == "bob only"
    assert rows[ALICE_ID].bio == "alice only"


async def test_a_service_principal_has_no_profile(profile_client, stub_session):
    """A bridge JWT is a service, not a person. 403, and no row is loaded."""
    client, as_user = profile_client
    as_user(BOB_ID, kind="bridge")

    assert (await client.get("/v1/me/profile")).status_code == 403
    assert (
        await client.patch("/v1/me/profile", json={"bio": "x"})
    ).status_code == 403
    assert stub_session.requested_pks == []


async def test_a_personal_api_token_reads_its_own_account(profile_client):
    """An xbt_ token is the same person, exactly as it is for /v1/me/link-github."""
    client, as_user = profile_client
    as_user(BOB_ID, kind="user_api_token")

    r = await client.get("/v1/me/profile")
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == str(BOB_ID)


async def test_an_account_deleted_mid_session_is_a_404_not_a_500(profile_client, rows):
    client, as_user = profile_client
    # The token still authenticates (the principal holds the account it resolved),
    # but the row is gone from the database by the time the route reads it.
    ghost = _make_user(uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"))
    as_user(ghost.id, principal_user=ghost)
    assert ghost.id not in rows

    assert (await client.get("/v1/me/profile")).status_code == 404
    assert (await client.patch("/v1/me/profile", json={"bio": "x"})).status_code == 404


# ── The edit semantics, through the real route ──────────────────────────────


async def test_empty_string_clears_and_the_provider_name_comes_back(
    profile_client, rows
):
    """The undo path, end to end through HTTP."""
    client, as_user = profile_client
    as_user(BOB_ID)

    renamed = await client.patch("/v1/me/profile", json={"preferred_name": "Bobby B"})
    assert renamed.json()["label"] == "Bobby B"

    cleared = await client.patch("/v1/me/profile", json={"preferred_name": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["preferred_name"] is None
    assert cleared.json()["label"] == "Bob", (
        "clearing a chosen name must hand the label back to the provider's — "
        "that is what makes a rename undoable without asking anyone"
    )
    assert rows[BOB_ID].preferred_name is None, "the column must be NULL, not ''"


async def test_explicit_null_clears_too(profile_client, rows):
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.patch("/v1/me/profile", json={"preferred_name": None})
    assert r.status_code == 200, r.text
    assert rows[BOB_ID].preferred_name is None


async def test_clearing_with_no_provider_name_falls_to_the_email(profile_client, rows):
    client, as_user = profile_client
    rows[BOB_ID].display_name = None
    as_user(BOB_ID)

    r = await client.patch("/v1/me/profile", json={"preferred_name": ""})
    assert r.json()["label"] == "bob"


async def test_an_omitted_field_is_left_alone(profile_client, rows):
    """A client editing only the bio cannot blank the name by forgetting to send it."""
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.patch("/v1/me/profile", json={"bio": "just the bio"})
    assert r.status_code == 200, r.text
    assert rows[BOB_ID].preferred_name == "Bobby", "an absent key must not clear"
    assert rows[BOB_ID].bio == "just the bio"


async def test_an_empty_patch_is_a_no_op_that_still_returns_the_profile(
    profile_client, rows
):
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.patch("/v1/me/profile", json={})
    assert r.status_code == 200, r.text
    assert r.json()["preferred_name"] == "Bobby"
    assert rows[BOB_ID].bio == "Bob's private bio"


async def test_hostile_text_is_refused_at_the_route_not_just_in_the_helper(
    profile_client, rows
):
    """Server-side validation. The client is not part of this trust chain."""
    client, as_user = profile_client
    as_user(BOB_ID)

    for payload in (
        {"preferred_name": "Bob\nAdmin"},
        {"preferred_name": "Bob\u202enimdA"},
        {"preferred_name": "B" * 300},
        {"bio": "B" * 5000},
        {"preferred_name": "Bob\x00"},
        {"preferred_name": 12345},
    ):
        r = await client.patch("/v1/me/profile", json=payload)
        assert r.status_code == 422, f"{payload!r} was accepted: {r.text}"

    assert rows[BOB_ID].preferred_name == "Bobby", "a refused patch must write nothing"


async def test_the_422_names_the_field_so_a_form_can_place_the_message(profile_client):
    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.patch("/v1/me/profile", json={"preferred_name": "Bob\nAdmin"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("preferred_name" in entry["loc"] for entry in detail), detail
    assert any("control" in entry["msg"].lower() for entry in detail), detail


async def test_the_response_carries_the_label_chat_will_render(profile_client):
    """The profile screen and the chat bubble resolve through the same ladder."""
    from app.services.user_label import resolve_user_label

    client, as_user = profile_client
    as_user(BOB_ID)

    r = await client.patch("/v1/me/profile", json={"preferred_name": "  Bobby B  "})
    assert r.json()["preferred_name"] == "Bobby B", "surrounding whitespace is stripped"
    assert r.json()["label"] == "Bobby B"

    from app.models.user import User

    assert r.json()["label"] == resolve_user_label(
        User(id=BOB_ID, source_user_id="s", email="bob@test.local", preferred_name="Bobby B")
    )


async def test_a_refused_patch_does_not_commit(profile_client, stub_session):
    client, as_user = profile_client
    as_user(BOB_ID)

    await client.patch("/v1/me/profile", json={"preferred_name": "Bob\nAdmin"})
    assert stub_session.commits == 0, (
        "validation runs before the row is touched; a rejected body must not "
        "reach the database at all"
    )

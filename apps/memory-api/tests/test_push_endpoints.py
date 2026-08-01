"""Phase 27 — /v1/push/{config,subscribe,unsubscribe} behaviour (PUSH-01, D-27-05).

Two tiers, deliberately:

  * The ORDERING and ISOLATION tests run with NO database. They drive the REAL routes
    over the real ASGI app and replace only the repo's terminal writes with RECORDERS,
    so every gate (principal kind, endpoint safety, rate limit) executes for real and a
    rejection can be proven to have written NOTHING. Keeping them DB-free is the point:
    the ordering guarantee is the one a refactor breaks silently, so it must not be
    provable only where Docker happens to be running.

  * The STORAGE test — the shared-device ownership transfer — needs the real
    `ON CONFLICT (endpoint)` semantics, so it is `integration`-marked and runs against
    testcontainers-Postgres, matching test_nudge_open_gate.py. A clean SKIP is
    legitimate ONLY when Docker is genuinely absent (the conftest gate); under Docker
    this test MUST run green.

The thing worth stating plainly, because it is the phase's sharpest edge: on a SHARED
browser the second person to enable notifications receives the SAME endpoint the first
person's subscription used. The correct end state is ONE row owned by the SECOND user —
not two rows, and never a surviving first row that would keep delivering the newcomer's
notifications to the previous occupant.
"""
from __future__ import annotations

import types
from uuid import uuid4

import pytest

FCM = "https://fcm.googleapis.com/fcm/send/dGhpcy1pcy1hLWZha2UtdG9rZW4"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABmZmZm"


# ── DB-free harness ───────────────────────────────────────────────────────────


class _StubSession:
    """Stands in for AsyncSession. Records commits; never touches a database."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _install(app, *, kind: str = "user", user=None):
    """Override the principal + session dependencies on the real app."""
    from app.deps import get_current_principal, get_session

    user = user or types.SimpleNamespace(
        id=uuid4(), source_user_id="alice-sub", display_name="Alice", email="a@t.local"
    )
    stub = _StubSession()

    async def _principal():
        return {"kind": kind, "user": user if kind != "bridge" else None, "sub": "alice-sub"}

    async def _session():
        yield stub

    app.dependency_overrides[get_current_principal] = _principal
    app.dependency_overrides[get_session] = _session
    return user, stub


def _clear(app) -> None:
    app.dependency_overrides.clear()


async def _client(app):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def app():
    from app.main import app as real_app

    yield real_app
    real_app.dependency_overrides.clear()


@pytest.fixture
def writes(monkeypatch):
    """Replace the repo's two write paths with recorders. Every gate before them still
    runs for real, so an empty recorder proves a rejection stored nothing."""
    upserts: list[dict] = []
    deletes: list[dict] = []

    async def _upsert(session, **kw):
        upserts.append(kw)
        return types.SimpleNamespace(id=uuid4(), **kw)

    async def _delete_for_user(session, **kw):
        deletes.append(kw)
        return 0

    monkeypatch.setattr("app.repos.push_subscriptions.upsert", _upsert)
    monkeypatch.setattr("app.repos.push_subscriptions.delete_for_user", _delete_for_user)
    return types.SimpleNamespace(upserts=upserts, deletes=deletes)


# ── GET /v1/push/config ───────────────────────────────────────────────────────


async def test_config_reports_disabled_when_no_key_is_configured(app, monkeypatch):
    """A zero-key OSS install must answer a well-formed "off", not an error. The client
    reads this and never asks the browser for notification permission (D-27-05)."""
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    _install(app)
    async with await _client(app) as c:
        r = await c.get("/v1/push/config")
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": False, "vapid_public_key": ""}


async def test_config_reports_enabled_only_when_both_halves_are_present(app, monkeypatch):
    """A public key with no private key cannot SEND anything, so advertising `enabled`
    would make the client subscribe into a void. Both halves, or off."""
    monkeypatch.setattr("app.config.settings.PUSH_ENABLED", True)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "BPublicKeyValue")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    _install(app)
    async with await _client(app) as c:
        assert (await c.get("/v1/push/config")).json()["enabled"] is False

        monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "sekrit")
        body = (await c.get("/v1/push/config")).json()
        assert body == {"enabled": True, "vapid_public_key": "BPublicKeyValue"}


async def test_config_kill_switch_forces_disabled(app, monkeypatch):
    monkeypatch.setattr("app.config.settings.PUSH_ENABLED", False)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "BPublicKeyValue")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "sekrit")
    _install(app)
    async with await _client(app) as c:
        assert (await c.get("/v1/push/config")).json()["enabled"] is False


async def test_config_never_leaks_the_private_key_at_any_depth(app, monkeypatch):
    """T-27-03-01, asserted on the WIRE rather than on the source: the sentinel must not
    appear anywhere in the serialized response — not as a value, not nested, not as a
    substring of some debug field."""
    sentinel = "PRIVATE-SENTINEL-b3f9c1e7-do-not-serialize"
    monkeypatch.setattr("app.config.settings.PUSH_ENABLED", True)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "BPublicKeyValue")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", sentinel)
    _install(app)
    async with await _client(app) as c:
        r = await c.get("/v1/push/config")
    assert sentinel not in r.text, "the VAPID private key crossed to a client"
    assert r.json()["vapid_public_key"] == "BPublicKeyValue"


# ── POST /v1/push/subscribe ───────────────────────────────────────────────────


async def test_subscribe_rejects_a_bridge_principal(app, writes):
    """User-only. A bridge token is service-to-service and has no device to notify;
    letting one subscribe would attach a mailbox to nobody."""
    _install(app, kind="bridge")
    async with await _client(app) as c:
        r = await c.post(
            "/v1/push/subscribe",
            json={"endpoint": FCM, "keys": {"p256dh": "k", "auth": "a"}},
        )
    assert r.status_code == 403, r.text
    assert writes.upserts == [], "a rejected principal must store nothing"


async def test_subscribe_stores_the_subscription_for_the_calling_user(app, writes):
    """The owner is taken from the verified principal, never from the body — a caller
    cannot subscribe a device on someone else's behalf."""
    user, stub = _install(app)
    async with await _client(app) as c:
        r = await c.post(
            "/v1/push/subscribe",
            json={
                "endpoint": FCM,
                "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
                "user_agent": "Mozilla/5.0",
            },
        )
    assert r.status_code == 201, r.text
    assert r.json() == {"status": "subscribed"}
    assert len(writes.upserts) == 1
    stored = writes.upserts[0]
    assert stored["user_id"] == user.id
    assert stored["endpoint"] == FCM
    assert stored["p256dh"] == "p256dh-value"
    assert stored["auth"] == "auth-value"
    assert stored["user_agent"] == "Mozilla/5.0"
    assert stub.commits == 1, "a stored subscription must be committed"


@pytest.mark.parametrize(
    "endpoint,why",
    [
        ("http://fcm.googleapis.com/fcm/send/x", "not https"),
        ("https://127.0.0.1/p/x", "loopback"),
        ("https://169.254.169.254/latest/meta-data/", "cloud metadata service"),
        ("https://[::1]/p/x", "IPv6 loopback"),
        ("https://user:pw@push.example/x", "embedded userinfo"),
    ],
)
async def test_subscribe_rejects_an_unsafe_endpoint_before_writing(app, writes, endpoint, why):
    """422 and — the load-bearing half — NOTHING stored. The endpoint is a URL this
    server will POST to unattended forever; a rejected one must never reach the table."""
    _install(app)
    async with await _client(app) as c:
        r = await c.post(
            "/v1/push/subscribe",
            json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}},
        )
    assert r.status_code == 422, f"{why}: {r.status_code} {r.text}"
    assert writes.upserts == [], f"rejected endpoint ({why}) must store nothing"


async def test_subscribe_rejects_an_over_long_endpoint(app, writes):
    _install(app)
    async with await _client(app) as c:
        r = await c.post(
            "/v1/push/subscribe",
            json={
                "endpoint": "https://push.example.com/p/" + "a" * 3000,
                "keys": {"p256dh": "k", "auth": "a"},
            },
        )
    assert r.status_code == 422, r.text
    assert writes.upserts == []


async def test_subscribe_rejects_unknown_body_fields(app, writes):
    """extra="forbid" — an unexpected field is a client/server disagreement, and
    silently dropping it is how a "user_id" in the body gets assumed to work."""
    _install(app)
    async with await _client(app) as c:
        r = await c.post(
            "/v1/push/subscribe",
            json={
                "endpoint": FCM,
                "keys": {"p256dh": "k", "auth": "a"},
                "user_id": str(uuid4()),
            },
        )
    assert r.status_code == 422, r.text
    assert writes.upserts == []


async def test_subscribe_rate_limit_returns_429_and_stores_nothing(app, writes, monkeypatch):
    """T-27-03-06, keyed per USER (not per IP), and applied BEFORE the write."""
    from app.services import rate_limit

    rate_limit._storage.reset()
    monkeypatch.setattr("app.config.settings.PUSH_SUBSCRIBE_RATE_LIMIT", "1/minute")
    _install(app)
    async with await _client(app) as c:
        body = {"endpoint": FCM, "keys": {"p256dh": "k", "auth": "a"}}
        assert (await c.post("/v1/push/subscribe", json=body)).status_code == 201
        assert len(writes.upserts) == 1
        r = await c.post("/v1/push/subscribe", json=body)
    assert r.status_code == 429, r.text
    assert len(writes.upserts) == 1, "the rate-limited request must add NO write"


# ── POST /v1/push/unsubscribe ─────────────────────────────────────────────────


async def test_unsubscribe_scopes_the_delete_to_the_caller(app, writes):
    """T-27-03-03. The delete MUST carry the caller's user_id as well as the endpoint,
    or any signed-in caller who learned an endpoint string could silence that device."""
    user, stub = _install(app)
    async with await _client(app) as c:
        r = await c.post("/v1/push/unsubscribe", json={"endpoint": FCM})
    assert r.status_code == 204, r.text
    assert len(writes.deletes) == 1
    assert writes.deletes[0] == {"user_id": user.id, "endpoint": FCM}
    assert stub.commits == 1


async def test_unsubscribe_returns_204_even_when_nothing_matched(app, writes):
    """T-27-03-04. The recorder returns 0 rows deleted (somebody else's endpoint, or
    none at all) and the answer is STILL 204 — a 404 here would confirm whether an
    endpoint belongs to another account."""
    _install(app)
    async with await _client(app) as c:
        r = await c.post("/v1/push/unsubscribe", json={"endpoint": MOZ})
    assert r.status_code == 204, r.text
    assert r.content in (b"", None)


async def test_unsubscribe_rejects_a_bridge_principal(app, writes):
    _install(app, kind="bridge")
    async with await _client(app) as c:
        r = await c.post("/v1/push/unsubscribe", json={"endpoint": FCM})
    assert r.status_code == 403, r.text
    assert writes.deletes == []


# ── Structural: the transfer is in the STATEMENT, and the prune path is unreachable ──


def test_upsert_statement_transfers_ownership_on_endpoint_conflict():
    """The shared-device guarantee is a property of the SQL, so assert the SQL.

    Reading it off the source (rather than only observing it against a live Postgres)
    means the guarantee is checked on every run, including where Docker is absent.
    The behavioural proof against real `ON CONFLICT` semantics is the integration test
    below.
    """
    from pathlib import Path

    import app.repos.push_subscriptions as repo

    sql = Path(repo.__file__).read_text(encoding="utf-8")
    assert "ON CONFLICT (endpoint) DO UPDATE SET" in sql
    assert "user_id      = EXCLUDED.user_id" in sql, (
        "the conflict path must REASSIGN user_id — without it a shared device leaves "
        "the previous occupant subscribed to the newcomer's notifications"
    )
    assert "last_used_at = NULL" in sql, "a transferred row must not inherit delivery history"
    # The conflict target must be the endpoint alone. A composite target would silently
    # restore the two-row shared-device bug.
    assert "ON CONFLICT (endpoint, " not in sql
    assert "ON CONFLICT (user_id" not in sql


def test_no_http_route_can_delete_by_endpoint_alone():
    """`delete_by_endpoint` has no user predicate, so it is a prune-path-only helper
    (plan 27-04). If a route ever calls it, any caller could delete any device."""
    from pathlib import Path

    import app.routes.push as push_mod

    source = Path(push_mod.__file__).read_text(encoding="utf-8")
    assert "delete_by_endpoint" not in source, (
        "app/routes/push.py reaches the unscoped prune helper — an HTTP caller must "
        "never be able to delete a subscription without proving they own it."
    )


# ── Storage semantics against a REAL Postgres ─────────────────────────────────


@pytest.mark.integration
async def test_shared_device_transfers_ownership_and_isolates_users(session, seeded_two_teams):
    """THE shared-device gate (T-27-03-02), against real `ON CONFLICT (endpoint)`.

    Alice enables notifications on a browser. Bob then signs in on the SAME browser and
    enables them, and the browser hands back the SAME endpoint. Afterwards exactly ONE
    row must exist, owned by Bob — and Alice must neither see it nor be able to delete
    it.
    """
    from sqlalchemy import text

    from app.repos import push_subscriptions as repo

    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]

    await repo.upsert(
        session, user_id=alice.id, endpoint=FCM, p256dh="alice-p", auth="alice-a",
        user_agent="Shared Browser",
    )
    await session.commit()
    assert len(await repo.list_for_user(session, user_id=alice.id)) == 1

    # Bob signs in on the same browser: SAME endpoint, different account.
    await repo.upsert(
        session, user_id=bob.id, endpoint=FCM, p256dh="bob-p", auth="bob-a",
        user_agent="Shared Browser",
    )
    await session.commit()

    total = (
        await session.execute(
            text("SELECT count(*) FROM push_subscriptions WHERE endpoint = :e"), {"e": FCM}
        )
    ).scalar()
    assert total == 1, f"expected ONE row per endpoint, found {total} — the mailbox was duplicated"

    assert await repo.list_for_user(session, user_id=alice.id) == [], (
        "Alice still owns the endpoint after Bob took the device over — she would keep "
        "receiving Bob's notifications"
    )
    bobs = await repo.list_for_user(session, user_id=bob.id)
    assert len(bobs) == 1 and bobs[0].p256dh == "bob-p"

    # Alice cannot delete a device she no longer owns; Bob can.
    assert await repo.delete_for_user(session, user_id=alice.id, endpoint=FCM) == 0
    assert await repo.delete_for_user(session, user_id=bob.id, endpoint=FCM) == 1
    await session.commit()


@pytest.mark.integration
async def test_multiple_devices_per_user_revoke_independently(session, seeded_two_teams):
    """D-27-05: per-device revocation. Two devices, delete one, the other survives."""
    from app.repos import push_subscriptions as repo

    alice = seeded_two_teams["alice"]
    await repo.upsert(session, user_id=alice.id, endpoint=FCM, p256dh="p1", auth="a1",
                      user_agent="Laptop")
    await repo.upsert(session, user_id=alice.id, endpoint=MOZ, p256dh="p2", auth="a2",
                      user_agent="Phone")
    await session.commit()
    assert len(await repo.list_for_user(session, user_id=alice.id)) == 2

    assert await repo.delete_for_user(session, user_id=alice.id, endpoint=FCM) == 1
    await session.commit()
    left = await repo.list_for_user(session, user_id=alice.id)
    assert [s.endpoint for s in left] == [MOZ], "revoking one device silenced the others"


@pytest.mark.integration
async def test_prune_and_touch_operate_on_a_dead_endpoint(session, seeded_two_teams):
    """The 404/410 prune path (D-27-05) and the delivery stamp, against real SQL."""
    from app.repos import push_subscriptions as repo

    alice = seeded_two_teams["alice"]
    await repo.upsert(session, user_id=alice.id, endpoint=MOZ, p256dh="p", auth="a",
                      user_agent=None)
    await session.commit()

    await repo.touch(session, endpoint=MOZ)
    await session.commit()
    assert (await repo.list_for_user(session, user_id=alice.id))[0].last_used_at is not None

    assert await repo.delete_by_endpoint(session, endpoint=MOZ) == 1
    await session.commit()
    assert await repo.list_for_user(session, user_id=alice.id) == []

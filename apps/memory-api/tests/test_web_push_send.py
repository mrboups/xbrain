"""Phase 27 — the SEND half of web push: payload shape, off-loop delivery, prune (PUSH-01).

Two properties carry the whole plan, and both are asserted here without a network, a
key, or a database:

  * **The prune matrix.** 404/410 means the push service is telling us the subscription
    is GONE — the browser revoked or replaced it. Deleting the row is the only correct
    response: a retry can never succeed, and repeating it on every future message costs
    a request per message forever against a third party that already refused. Every
    OTHER failure (500, a rate limit, a dropped connection) is transient, and deleting on
    those would silently unsubscribe a live device that happened to be sent to during an
    outage. So the matrix is asserted in both directions — 404/410 delete, everything
    else keeps.

  * **The payload is content that LEAVES our trust boundary.** It travels through
    FCM/Mozilla and lands on a lock screen. It therefore carries a title, a capped
    preview, a first-party URL and a tag — and nothing that could authenticate anyone
    (D-27-06).

Everything below is DB-free and network-free on purpose: these are the guarantees a
later refactor breaks silently, so they must not be provable only where Docker happens
to be running. `_send_one` (the one function that actually talks to pywebpush) is the
single seam the tests replace.
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path
from uuid import uuid4

import pytest

FCM = "https://fcm.googleapis.com/fcm/send/dGhpcy1pcy1hLWZha2UtdG9rZW4"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABmZmZm"
APPLE = "https://web.push.apple.com/QAsomethingsomething"


# ── Harness ───────────────────────────────────────────────────────────────────


class _StubSession:
    """Stands in for AsyncSession. Records commits; never touches a database."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _sub(endpoint: str):
    """A stand-in for a PushSubscription row (only the three fields the sender reads)."""
    return types.SimpleNamespace(endpoint=endpoint, p256dh="p256dh-" + endpoint[-4:], auth="auth-x")


def _response(status: int):
    """A stand-in for the requests.Response pywebpush attaches to its exception."""
    return types.SimpleNamespace(status_code=status, text=f"stub {status}")


@pytest.fixture
def repo(monkeypatch):
    """Replace the push repo's three touch points with recorders.

    The sender reaches the repo as a MODULE attribute, so patching the attribute
    intercepts it while every line of the sender's own logic still runs for real.
    """
    state = types.SimpleNamespace(subs=[], deleted=[], touched=[])

    async def _list_for_user(session, *, user_id):
        return list(state.subs)

    async def _delete_by_endpoint(session, *, endpoint):
        state.deleted.append(endpoint)
        return 1

    async def _touch(session, *, endpoint):
        state.touched.append(endpoint)

    monkeypatch.setattr("app.repos.push_subscriptions.list_for_user", _list_for_user)
    monkeypatch.setattr("app.repos.push_subscriptions.delete_by_endpoint", _delete_by_endpoint)
    monkeypatch.setattr("app.repos.push_subscriptions.touch", _touch)
    return state


@pytest.fixture
def configured(monkeypatch):
    """A fully configured push install (both key halves + the kill-switch on)."""
    monkeypatch.setattr("app.config.settings.PUSH_ENABLED", True)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "BPublicKeyValue")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "a-private-scalar")
    monkeypatch.setattr("app.config.settings.PUSH_PREVIEW_CHARS", 120)


def _install_sender(monkeypatch, behaviour):
    """Replace the ONE function that talks to pywebpush.

    `behaviour(endpoint)` returns None to deliver, or raises. Returns the list of
    endpoints the sender actually attempted, in order.
    """
    from pywebpush import WebPushException  # noqa: F401  (imported for the callers' use)

    attempted: list[str] = []

    def _fake_send_one(sub_info, data):
        attempted.append(sub_info["endpoint"])
        behaviour(sub_info["endpoint"])

    monkeypatch.setattr("app.services.web_push._send_one", _fake_send_one)
    return attempted


# ── Payload builders (pure, no I/O) ───────────────────────────────────────────


def test_mention_payload_caps_the_preview_and_marks_the_cut(configured, monkeypatch):
    """D-27-06: a SHORT preview, never the whole message. The body lands on a lock
    screen a shoulder-surfer can read, so the cap is a privacy control, not cosmetics."""
    from app.services import web_push

    monkeypatch.setattr("app.config.settings.PUSH_PREVIEW_CHARS", 20)
    payload = web_push.build_mention_payload(
        team_slug="team-a",
        author_label="Alice",
        content="x" * 500,
        url="https://app.example/app/",
    )
    body = payload["body"]
    assert body.endswith("…"), f"a truncated preview must show it was cut: {body!r}"
    assert len(body) <= 21, f"preview exceeded the cap: {len(body)}"
    assert payload["title"] == "Alice mentioned you"
    assert payload["kind"] == "mention"
    assert payload["url"] == "https://app.example/app/"
    assert payload["tag"] == "mention:team-a"


def test_mention_payload_leaves_a_short_message_intact(configured):
    """Under the cap there is no ellipsis — a two-word message must not look truncated."""
    from app.services import web_push

    payload = web_push.build_mention_payload(
        team_slug="team-a", author_label="Alice", content="ship it", url="/app/"
    )
    assert payload["body"] == "ship it"


def test_payloads_carry_no_credential_material(configured):
    """T-27-04-01. The payload crosses FCM/Mozilla and is decrypted on a device we do
    not control; anything authenticating in it is a credential we handed to a third
    party. Asserted on the SHAPE the builders emit, with benign inputs."""
    from app.services import web_push

    payloads = [
        web_push.build_mention_payload(
            team_slug="team-a", author_label="Alice", content="ping", url="/app/"
        ),
        web_push.build_nudge_payload(
            sender_label="Alice", target_url="https://example.com/doc", app_url="/app/"
        ),
    ]
    for payload in payloads:
        blob = json.dumps(payload).lower()
        for forbidden in ("token", "bearer", "p256dh", "jwt", "secret", "password", "api_key"):
            assert forbidden not in blob, f"{forbidden!r} appears in a push payload: {payload!r}"


def test_nudge_payload_points_at_the_app_not_the_supplied_url(configured):
    """T-27-04-02 / D-22-02. The link is shown as the BODY so the recipient sees where
    they would be going, but the notification's own `url` — what the OS opens on a tap —
    is the app. Tapping must never move a browser to a teammate-supplied destination
    without passing the in-app consent gate."""
    from app.services import web_push

    payload = web_push.build_nudge_payload(
        sender_label="Alice",
        target_url="https://evil.example/drive-by",
        app_url="https://app.example/app/",
    )
    assert payload["url"] == "https://app.example/app/"
    assert payload["url"] != "https://evil.example/drive-by"
    assert payload["kind"] == "nudge"
    assert payload["tag"] == "nudge"


# ── The configuration gate ────────────────────────────────────────────────────


async def test_send_does_nothing_when_the_kill_switch_is_off(repo, configured, monkeypatch):
    """PUSH_ENABLED=False must stop the send BEFORE the subscription list is read —
    an operator flipping the switch expects silence, not a quieter fan-out."""
    from app.services import web_push

    monkeypatch.setattr("app.config.settings.PUSH_ENABLED", False)
    repo.subs = [_sub(FCM)]
    attempted = _install_sender(monkeypatch, lambda e: None)
    session = _StubSession()

    result = await web_push.send_to_user(session, user_id=uuid4(), payload={"kind": "mention"})

    assert result == {"sent": 0, "pruned": 0, "skipped": True}
    assert attempted == []
    assert session.commits == 0


async def test_send_does_nothing_without_a_signing_key(repo, configured, monkeypatch):
    """A zero-key OSS install: the public half may even be present, but with nothing to
    sign with every request would be rejected. Skip, do not attempt."""
    from app.services import web_push

    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    repo.subs = [_sub(FCM)]
    attempted = _install_sender(monkeypatch, lambda e: None)

    result = await web_push.send_to_user(_StubSession(), user_id=uuid4(), payload={})

    assert result["skipped"] is True
    assert result["sent"] == 0
    assert attempted == []


# ── The prune matrix ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [404, 410])
async def test_a_gone_subscription_is_deleted(repo, configured, monkeypatch, status):
    """T-27-04-03. 404/410 = "this mailbox no longer exists". Retrying is guaranteed to
    fail forever, so the row goes."""
    from pywebpush import WebPushException

    from app.services import web_push

    repo.subs = [_sub(FCM)]

    def _gone(endpoint):
        raise WebPushException("gone", response=_response(status))

    _install_sender(monkeypatch, _gone)
    session = _StubSession()

    result = await web_push.send_to_user(session, user_id=uuid4(), payload={"kind": "mention"})

    assert repo.deleted == [FCM], f"a {status} endpoint must be pruned"
    assert result == {"sent": 0, "pruned": 1}
    assert repo.touched == [], "a failed delivery must not stamp last_used_at"
    assert session.commits == 1, "the prune must be committed"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_transient_failure_keeps_the_subscription(repo, configured, monkeypatch, status):
    """The inverse half of the matrix, and the one that costs users if it is wrong:
    deleting on a 500 or a 429 silently unsubscribes a LIVE device because the push
    service had a bad minute."""
    from pywebpush import WebPushException

    from app.services import web_push

    repo.subs = [_sub(FCM)]

    def _boom(endpoint):
        raise WebPushException("upstream trouble", response=_response(status))

    _install_sender(monkeypatch, _boom)

    result = await web_push.send_to_user(_StubSession(), user_id=uuid4(), payload={})

    assert repo.deleted == [], f"status {status} is transient — the row must survive"
    assert result == {"sent": 0, "pruned": 0}


async def test_a_transport_error_keeps_the_subscription(repo, configured, monkeypatch):
    """`exc.response is None` — DNS failure, TLS reset, timeout. We never learned the
    push service's verdict, so we have no evidence the subscription is dead."""
    from pywebpush import WebPushException

    from app.services import web_push

    repo.subs = [_sub(MOZ)]

    def _no_response(endpoint):
        raise WebPushException("connection reset", response=None)

    _install_sender(monkeypatch, _no_response)

    result = await web_push.send_to_user(_StubSession(), user_id=uuid4(), payload={})

    assert repo.deleted == [], "an unanswered request is not proof the mailbox is gone"
    assert result == {"sent": 0, "pruned": 0}


async def test_an_unexpected_exception_keeps_the_subscription(repo, configured, monkeypatch):
    """Not every failure arrives as a WebPushException (a serialization bug, a library
    change). Anything unrecognised is transient by default — deletion needs evidence."""
    from app.services import web_push

    repo.subs = [_sub(MOZ)]

    def _weird(endpoint):
        raise RuntimeError("something else entirely")

    _install_sender(monkeypatch, _weird)

    result = await web_push.send_to_user(_StubSession(), user_id=uuid4(), payload={})

    assert repo.deleted == []
    assert result == {"sent": 0, "pruned": 0}


# ── Fan-out ───────────────────────────────────────────────────────────────────


async def test_one_dead_device_does_not_silence_the_others(repo, configured, monkeypatch):
    """Three devices, the middle one gone: the third must still be attempted. A sender
    that aborts on the first failure means one stale laptop mutes a person's phone."""
    from pywebpush import WebPushException

    from app.services import web_push

    repo.subs = [_sub(FCM), _sub(MOZ), _sub(APPLE)]

    def _middle_is_gone(endpoint):
        if endpoint == MOZ:
            raise WebPushException("gone", response=_response(410))

    attempted = _install_sender(monkeypatch, _middle_is_gone)
    session = _StubSession()

    result = await web_push.send_to_user(session, user_id=uuid4(), payload={"kind": "mention"})

    assert attempted == [FCM, MOZ, APPLE], "the sender stopped early"
    assert result == {"sent": 2, "pruned": 1}
    assert repo.deleted == [MOZ]
    assert sorted(repo.touched) == sorted([FCM, APPLE]), "delivered devices must be stamped"
    assert session.commits == 1


async def test_no_subscriptions_is_a_quiet_no_op(repo, configured, monkeypatch):
    from app.services import web_push

    repo.subs = []
    attempted = _install_sender(monkeypatch, lambda e: None)
    session = _StubSession()

    result = await web_push.send_to_user(session, user_id=uuid4(), payload={})

    assert result == {"sent": 0, "pruned": 0}
    assert attempted == []
    assert session.commits == 0, "nothing happened — nothing to commit"


async def test_the_sender_runs_off_the_event_loop(repo, configured, monkeypatch):
    """pywebpush is synchronous (requests). Encryption plus a round-trip to FCM on the
    event loop would stall every other request in the process for its duration, so the
    call must go through a worker thread."""
    import threading

    from app.services import web_push

    repo.subs = [_sub(FCM)]
    seen: list[int] = []

    def _record_thread(sub_info, data):
        seen.append(threading.get_ident())

    monkeypatch.setattr("app.services.web_push._send_one", _record_thread)
    await web_push.send_to_user(_StubSession(), user_id=uuid4(), payload={})

    assert len(seen) == 1
    assert seen[0] != threading.get_ident(), "the blocking send ran on the event loop thread"


# ── The background entrypoint ─────────────────────────────────────────────────


async def test_send_to_user_bg_never_raises(configured, monkeypatch):
    """It is the `asyncio.create_task` target. A raise here surfaces as an unretrieved
    task exception and, worse, can take down the caller's task group — so the message a
    user just posted would fail because a notification did."""
    from app.services import web_push

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _StubSession()

        async def __aexit__(self, *exc):
            return False

    async def _explode(session, *, user_id, payload):
        raise RuntimeError("the sender blew up")

    monkeypatch.setattr("app.services.web_push.async_session_factory", _Factory())
    monkeypatch.setattr("app.services.web_push.send_to_user", _explode)

    assert await web_push.send_to_user_bg(user_id=uuid4(), payload={"kind": "mention"}) is None


async def test_send_to_user_bg_survives_a_broken_session_factory(configured, monkeypatch):
    """The database being unreachable must not turn a notification into a crash."""
    from app.services import web_push

    def _factory():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr("app.services.web_push.async_session_factory", _factory)

    assert await web_push.send_to_user_bg(user_id=uuid4(), payload={}) is None


async def test_send_to_user_bg_opens_no_session_when_push_is_off(monkeypatch):
    """With push disabled, every message posted would otherwise still check out a DB
    connection to discover there is nothing to do."""
    from app.services import web_push

    monkeypatch.setattr("app.config.settings.PUSH_ENABLED", False)

    def _factory():
        raise AssertionError("a disabled push path opened a database session")

    monkeypatch.setattr("app.services.web_push.async_session_factory", _factory)

    assert await web_push.send_to_user_bg(user_id=uuid4(), payload={}) is None


# ── Structural guarantees ─────────────────────────────────────────────────────


def test_prune_statuses_are_exactly_404_and_410():
    """Pinned deliberately. Widening this set starts deleting live devices on transient
    failures; narrowing it re-introduces the forever-retry. Either edit must fail here
    rather than change behaviour quietly."""
    from app.services import web_push

    assert web_push.PRUNE_STATUSES == frozenset({404, 410})


def test_the_sender_never_logs_subscription_secrets():
    """T-27-04-07. An operator needs to tell FCM from Mozilla in the logs; nobody needs
    the endpoint PATH — anyone holding it can push to that device, so it is a capability
    — or the encryption keys, sitting in a log aggregator.

    Asserted structurally rather than by substring: every `log.*()` call in the module is
    parsed and each argument inspected, so the check cannot be defeated by reformatting
    and cannot false-positive on the repo calls that legitimately pass the endpoint.
    """
    import ast

    from app.services import web_push

    source = Path(web_push.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    log_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "log"
    ]
    assert log_calls, "expected the sender to log at all"

    for call in log_calls:
        # Erase the ONE sanctioned wrapper — `_host(...)` reduces an endpoint to its
        # hostname — then anything still naming a secret is an unwrapped leak.
        rendered = re.sub(r"_host\([^()]*\)", "HOST", ast.unparse(call))
        for secret in ("sub.endpoint", "sub.p256dh", "sub.auth", "VAPID_PRIVATE_KEY"):
            assert secret not in rendered, f"a log line leaks {secret}: {rendered}"

    assert "_host(" in source, "the sender must log the endpoint HOST, not the endpoint"

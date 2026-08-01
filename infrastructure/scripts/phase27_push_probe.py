"""phase27_push_probe.py — Phase 27 SC#4: a REAL encrypted push, and the exact prune matrix.

Fed to the running API container by verify-phase27.sh check (g):

    docker compose exec -T memory-api python - < infrastructure/scripts/phase27_push_probe.py

It runs INSIDE the container on purpose. The claim being gated is "this deployment can
actually deliver a notification", and every part of that claim lives in the container:
the installed pywebpush, the VAPID keypair in its environment, and the Postgres its
sessions talk to. A copy of this logic running on a laptop with a pip-installed
pywebpush would prove something about the laptop.

NOTHING IN THE SEND PATH IS STUBBED — no test double, no import interception, no fake
transport, and the gate greps this file to keep it that way.
`app.services.web_push.send_to_user` is called as the message routes call it; pywebpush
performs real aes128gcm encryption against a real P-256 subscription key; the request
leaves over a real socket to a throwaway HTTP server this program stands up, which
answers with the status the assertion needs. What the server records — the headers and
the body length it actually received — is the evidence, not a claim in a comment.

WHY THE ENDPOINT IS `http://127.0.0.1:<port>` WHEN `_is_safe_push_endpoint` DEMANDS https.
The rows are written through `app.repos.push_subscriptions.upsert`, i.e. through the REPO,
never through `POST /v1/push/subscribe`. That is deliberate and it is not a hole being
worked around: the route-level guard exists to stop an OUTSIDE caller storing a loopback
or private-range endpoint as a lasting SSRF primitive, and it has its own test
(apps/memory-api/tests/test_push_endpoint_safety.py) which this probe neither touches nor
weakens. What is under test here is the SEND and PRUNE behaviour, and that needs a socket
whose response status this program controls. A public https push service cannot be made
to answer 410 on demand. The rows exist for the duration of one send each and are removed
in a `finally`; anything that somehow survives points at a dead loopback port and is
pruned by the next send's own 404 handling (T-27-08-05, T-27-08-06).

Exit code: 0 when every assertion passed, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

# ── The real code under test — imported, never re-implemented ────────────────
from app.config import settings
from app.db.session import async_session_factory
from app.models.user import User
from app.repos import push_subscriptions as push_repo
from app.services import web_push

# The status each path answers with, and what that status MUST mean for the row.
# This table IS the assertion: 404 and 410 are the only two statuses that may ever
# delete a subscription, and 500 must not, because deleting on a transient failure
# silently unsubscribes a live device that nobody told the user about.
CASES = [
    ("ok", 201, "kept"),
    ("gone", 410, "deleted"),
    ("nf", 404, "deleted"),
    ("boom", 500, "kept"),
]
STATUS_BY_PATH = {f"/{name}": status for name, status, _ in CASES}

PASSED = 0
FAILED = 0

# What the stand-in push service actually received, per path. Written from the server
# thread, read from the event loop after the send has completed (the send is awaited,
# so the write happens-before the read).
RECEIVED: dict[str, dict] = {}
RECEIVED_LOCK = threading.Lock()


def ok(msg: str) -> None:
    global PASSED
    print(f"  PASS: {msg}")
    PASSED += 1


def ko(msg: str) -> None:
    global FAILED
    print(f"  FAIL: {msg}")
    FAILED += 1


# ── The stand-in push service ────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    """Answers the status the path asks for, after recording what arrived.

    The body is read BEFORE the response is written. A server that answers and closes
    while the client is still sending gets the client a connection reset instead of the
    status the assertion is about, which would make a correct prune look like a
    transport error.
    """

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's naming
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with RECEIVED_LOCK:
            RECEIVED[self.path] = {
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body_len": len(body),
                "method": "POST",
            }
        status = STATUS_BY_PATH.get(self.path, 404)
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        """Silence the default stderr access log — this program prints its own evidence."""


def start_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="phase27-push-stub")
    thread.start()
    # Prove the listener is actually accepting before any subscription points at it: a
    # connection refused would be reported by pywebpush as a transport error, which the
    # send path treats as transient — the /boom case would then "pass" for the wrong reason.
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        pass
    return server, port


# ── Real subscription keys ───────────────────────────────────────────────────


def make_keys() -> tuple[str, str]:
    """A genuine P-256 public point and a 16-byte auth secret, base64url, unpadded.

    Real keys, not filler. pywebpush derives the content-encryption key from these via
    ECDH + HKDF; a malformed point raises before a single byte is encrypted, so a probe
    with dummy strings would prove nothing about encryption at all — it would only prove
    that an exception was raised early.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    point = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    p256dh = base64.urlsafe_b64encode(point).rstrip(b"=").decode()
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return p256dh, auth


def redact(headers: dict) -> dict:
    """Header view safe to print: credential-bearing values become scheme + length.

    `Authorization` carries the VAPID JWT this server just signed, and `Crypto-Key` /
    `Encryption` carry key material for this one message. None of them is the private
    key, but a gate log is an artefact that gets pasted into issues, and the scheme name
    plus a length is all the evidence the assertion needs (T-27-08-02).
    """
    out = {}
    for key, value in sorted(headers.items()):
        if key in ("authorization", "crypto-key", "encryption"):
            scheme = value.split(" ", 1)[0] if " " in value else value.split("=", 1)[0]
            out[key] = f"{scheme} <redacted, {len(value)} chars>"
        else:
            out[key] = value
    return out


# ── The proof ────────────────────────────────────────────────────────────────


async def run_case(session, *, user_id: UUID, port: int, name: str, expect: str) -> None:
    """One row, one real send, one verdict on whether the row survived."""
    endpoint = f"http://127.0.0.1:{port}/{name}"
    p256dh, auth = make_keys()
    await push_repo.upsert(
        session,
        user_id=user_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent="phase27-push-probe",
    )
    await session.commit()

    result = await web_push.send_to_user(
        session,
        user_id=user_id,
        payload={
            "kind": "mention",
            "title": "phase27 push probe",
            "body": f"probe case {name}",
            "url": "/app/",
            "tag": f"phase27-{name}",
        },
    )

    with RECEIVED_LOCK:
        got = RECEIVED.get(f"/{name}")

    status = STATUS_BY_PATH[f"/{name}"]
    if got is None:
        ko(f"/{name} ({status}) — the stand-in push service received NO request; nothing was sent")
    else:
        ok(f"/{name} ({status}) — a real request reached the stand-in push service")

    rows = await push_repo.list_for_user(session, user_id=user_id)
    present = any(r.endpoint == endpoint for r in rows)

    if expect == "deleted":
        if present:
            ko(f"/{name} ({status}) — the subscription row SURVIVED; {status} must prune it")
        else:
            ok(f"/{name} ({status}) — the subscription row was DELETED, as the prune matrix requires")
        if result.get("pruned") != 1:
            ko(f"/{name} ({status}) — send_to_user reported pruned={result.get('pruned')}, expected 1")
        else:
            ok(f"/{name} ({status}) — send_to_user reported exactly one prune")
    else:
        if not present:
            ko(
                f"/{name} ({status}) — the subscription row was DELETED; only 404/410 may delete, "
                "and deleting on anything else silently unsubscribes a live device"
            )
        else:
            ok(f"/{name} ({status}) — the subscription row was KEPT, as the prune matrix requires")
        if result.get("pruned") != 0:
            ko(f"/{name} ({status}) — send_to_user reported pruned={result.get('pruned')}, expected 0")
        else:
            ok(f"/{name} ({status}) — send_to_user pruned nothing")

    if name == "ok":
        if result.get("sent") != 1:
            ko(f"/ok — send_to_user reported sent={result.get('sent')}, expected 1")
        else:
            ok("/ok — send_to_user reported exactly one successful delivery")
        assert_encryption(got)


def assert_encryption(got: dict | None) -> None:
    """The /ok request must be a genuinely VAPID-signed, aes128gcm-encrypted POST."""
    if got is None:
        ko("/ok — no request to inspect, so nothing can be said about encryption")
        return

    headers = got["headers"]
    print("    observed request headers for /ok (credential values redacted):")
    for key, value in redact(headers).items():
        print(f"      {key}: {value}")
    print(f"      <body>: {got['body_len']} bytes")

    authz = headers.get("authorization", "")
    # pywebpush 2.x emits the draft-02 form (`vapid t=...,k=...`) for aes128gcm; older
    # builds emit the draft-01 `WebPush <jwt>` with the key in Crypto-Key. Accept what
    # it actually sends and record which, rather than asserting a version we assumed.
    scheme = authz.split(" ", 1)[0].lower() if authz else ""
    if scheme in ("vapid", "webpush"):
        ok(f"/ok — the request carried a VAPID Authorization header (scheme: {scheme})")
    else:
        ko(f"/ok — no recognised VAPID Authorization header (got scheme: {scheme!r})")

    encoding = headers.get("content-encoding", "")
    if encoding == "aes128gcm":
        ok("/ok — Content-Encoding: aes128gcm — the payload was really encrypted")
    else:
        ko(f"/ok — Content-Encoding was {encoding!r}, expected aes128gcm")

    if headers.get("ttl"):
        ok(f"/ok — TTL header present ({headers['ttl']})")
    else:
        ko("/ok — no TTL header; push services require one")

    if got["body_len"] > 0:
        ok(f"/ok — the encrypted body was non-empty ({got['body_len']} bytes)")
    else:
        ko("/ok — the body was EMPTY; nothing was encrypted into the request")


async def main() -> int:
    print("=== Phase 27 push probe (SC#4: real encrypted send + the 404/410/500 prune matrix) ===")

    if not web_push.push_is_configured():
        ko(
            "push is not configured in THIS container — send_to_user would return "
            "{'skipped': True} and prove nothing. Set PUSH_ENABLED=true, VAPID_PUBLIC_KEY "
            "and VAPID_PRIVATE_KEY in the deployment's .env and restart memory-api."
        )
        print(f"\n=== Summary ===\nPASS: {PASSED}\nFAIL: {FAILED}")
        return 1
    ok("push is configured in this container (kill-switch on, both key halves present)")

    server, port = start_server()
    print(f"  stand-in push service listening on 127.0.0.1:{port}")

    created_user_id: UUID | None = None
    endpoints = [f"http://127.0.0.1:{port}/{name}" for name, _, _ in CASES]

    try:
        async with async_session_factory() as session:
            supplied = (os.environ.get("VERIFY_USER_ID") or "").strip()
            if supplied:
                user_id = UUID(supplied)
                print(f"  using the supplied VERIFY_USER_ID {user_id}")
            else:
                user = User(
                    source_user_id=f"phase27-push-probe-{uuid4()}",
                    email=f"phase27-push-probe-{uuid4().hex[:8]}@probe.invalid",
                    display_name="phase27 push probe",
                )
                session.add(user)
                await session.commit()
                created_user_id = user.id
                user_id = user.id
                print(f"  created a throwaway user {user_id}")

            for name, _status, expect in CASES:
                await run_case(session, user_id=user_id, port=port, name=name, expect=expect)

    except Exception as exc:  # noqa: BLE001 — any failure here is a gate failure
        ko(f"the probe raised: {type(exc).__name__}: {exc}")
    finally:
        # Every row and every user this program created goes, whatever happened above.
        # A leftover row is a live delivery target pointing at a port that no longer
        # exists (T-27-08-05).
        try:
            async with async_session_factory() as cleanup_session:
                for endpoint in endpoints:
                    await push_repo.delete_by_endpoint(cleanup_session, endpoint=endpoint)
                if created_user_id is not None:
                    victim = await cleanup_session.get(User, created_user_id)
                    if victim is not None:
                        await cleanup_session.delete(victim)
                await cleanup_session.commit()
            print("  cleanup: probe subscriptions and the throwaway user removed")
        except Exception as exc:  # noqa: BLE001
            ko(f"cleanup failed — rows may be left behind: {type(exc).__name__}: {exc}")
        server.shutdown()
        server.server_close()

    print(f"\n=== Summary ===\nPASS: {PASSED}\nFAIL: {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

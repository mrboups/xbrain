"""/v1/push/{config,subscribe,unsubscribe} — browser push subscriptions (Phase 27 PUSH-01).

The storage + enrolment half of web push. The sending half is plan 27-04.

  GET  /v1/push/config       -> whether push is configured, plus the PUBLIC VAPID key
  POST /v1/push/subscribe    -> store (or take over) a browser's push mailbox
  POST /v1/push/unsubscribe  -> forget one of the caller's own devices

Auth model: all three are user-only (kind=user or user_api_token). A bridge principal
is service-to-service and has no device to notify.

Three disciplines this module owns:

  * The PUBLIC half of the VAPID keypair crosses to a browser; the private half is not
    read here, not logged here, and not named here. tests/test_push_endpoint_safety.py
    scans this file's source and fails the build if that ever changes (T-27-03-01).

  * A subscription endpoint is a URL this server will POST to on every mention, forever,
    unattended. It is validated BEFORE it is stored, or it is a stored SSRF primitive
    (T-27-03-05). Validation is lexical only — no DNS, no fetch; see
    `_is_safe_push_endpoint`.

  * Unsubscribe always answers 204 and always scopes its delete to the caller. Neither
    is cosmetic: the fixed status code denies an existence oracle (T-27-03-04) and the
    scoped delete denies a "silence someone else's phone" primitive (T-27-03-03).
"""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_principal, get_session
from app.repos import push_subscriptions as push_repo
from app.services import rate_limit

log = structlog.get_logger(__name__)
router = APIRouter()

# Hard ceiling on a stored endpoint. Real push endpoints are a few hundred chars;
# 2048 matches NUDGE_MAX_URL_LENGTH's default so the two URL guards agree.
_MAX_ENDPOINT_LEN = 2048

# Only https may ever be stored: the endpoint carries the encrypted payload, and a
# plaintext hop would expose the message preview and the subscription identity.
_ALLOWED_SCHEMES = frozenset({"https"})


# ── helpers ──────────────────────────────────────────────────────────────────


def _require_user_principal(principal: dict[str, Any]):
    kind = principal.get("kind")
    if kind not in ("user", "user_api_token"):
        raise HTTPException(403, "user-only endpoint")
    user = principal.get("user")
    if user is None:
        raise HTTPException(403, "user principal missing user object")
    return user


def _is_safe_push_endpoint(url: object) -> bool:
    """Return True iff ``url`` is an endpoint this server may safely POST to later.

    Requires: exactly the ``https`` scheme; a present host; NO embedded userinfo; no
    whitespace; at most ``_MAX_ENDPOINT_LEN`` characters; and, when the host is an IP
    LITERAL, a genuinely external address (loopback, private, link-local, unique-local,
    reserved and unspecified ranges are all refused, including their IPv4-mapped-IPv6
    spellings such as ``::ffff:127.0.0.1``). The name ``localhost`` and anything under
    ``.localhost`` are refused too.

    Purely LEXICAL — no DNS resolution, no HTTP fetch, mirroring
    services/url_safety.py's ban. Resolving the hostname to decide whether it points
    somewhere internal would itself be the outbound request this guard exists to
    prevent, and the answer would be stale by the time the send path used it. The
    consequence is deliberate and worth stating: a HOSTNAME that resolves to a private
    address is not rejected here. What is rejected is every shape that lets a caller
    NAME an internal address directly, which is the reachable half of the threat — the
    other half needs a DNS-rebinding attacker who also controls a public zone, and the
    defence for that belongs in the send path's egress rules, not in a string check.

    Never raises: a malformed or non-string value returns False, so this can be called
    from any context without turning bad input into a 500.
    """
    if not isinstance(url, str):
        return False
    if not url.strip():
        return False
    if len(url) > _MAX_ENDPOINT_LEN:
        return False
    # Any embedded whitespace is malformed here, and also defuses parser quirks that
    # silently strip control characters (a request-splitting shape).
    if any(ch.isspace() for ch in url):
        return False

    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return False

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if not parts.netloc:
        return False
    # Userinfo spoof: "https://push.example.com@evil.example/x" reads as if it targets
    # push.example.com but resolves to evil.example.
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False

    try:
        host = parts.hostname
    except ValueError:  # malformed IPv6 literal, e.g. "https://[::1/x"
        return False
    if not host:
        return False

    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a hostname, not an IP literal — accepted (see docstring)

    # Unwrap IPv4-mapped IPv6 ("::ffff:127.0.0.1"), the classic guard bypass, so the
    # classification below judges the address that traffic would actually reach.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


# ── GET /v1/push/config ──────────────────────────────────────────────────────


@router.get("/push/config")
async def push_config(
    principal: dict[str, Any] = Depends(get_current_principal),
) -> dict[str, Any]:
    """Public VAPID key + whether push is configured at all.

    An endpoint rather than a build-time constant so rotating the keypair needs no
    client rebuild (27-CONTEXT, Claude's Discretion). It returns the PUBLIC key ONLY —
    the private half of the keypair is never read in this module, which is what
    tests/test_push_endpoint_safety.py asserts by scanning this file's source.

    `enabled` requires BOTH halves of the keypair plus the kill-switch: a public key
    with nothing to sign with cannot deliver anything, and advertising `enabled` would
    make the client raise a permission prompt that buys the user nothing. With the
    keypair empty this answers {"enabled": false} — which is exactly how a zero-key OSS
    install is meant to behave, not an error.
    """
    _require_user_principal(principal)
    configured = bool(
        settings.PUSH_ENABLED and settings.VAPID_PUBLIC_KEY and settings.vapid_is_signable
    )
    return {"enabled": configured, "vapid_public_key": settings.VAPID_PUBLIC_KEY}


# ── POST /v1/push/subscribe ──────────────────────────────────────────────────


class SubscribeKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(..., min_length=1, max_length=256)
    auth: str = Field(..., min_length=1, max_length=256)


class SubscribeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Field bounds reject the absurdly large early; the real gate is
    # _is_safe_push_endpoint, which also enforces _MAX_ENDPOINT_LEN.
    endpoint: str = Field(..., min_length=1, max_length=4096)
    keys: SubscribeKeys
    user_agent: str | None = Field(default=None, max_length=256)


@router.post("/push/subscribe", status_code=201)
async def push_subscribe(
    body: SubscribeBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Store the calling user's browser push subscription, or take over an existing one.

    The owner is the VERIFIED principal — the body never names a user, so a caller
    cannot enrol a device on someone else's behalf.

    Ordering is load-bearing — every rejection returns BEFORE the write, so a rejected
    request provably stores nothing:
      1. caller must be a user principal            -> 403
      2. endpoint must be a safe https URL          -> 422 (T-27-03-05)
      3. per-user rate limit must not be exhausted  -> 429 (T-27-03-06)
      4. THEN upsert + commit                       -> 201

    Re-subscribing an endpoint that is already on file TRANSFERS it to this user rather
    than adding a row (see the repo and migration docstrings): on a shared browser the
    previous occupant must stop being a delivery target the moment somebody else enables
    notifications there.
    """
    user = _require_user_principal(principal)

    # 2. SSRF guard — lexical, before anything is persisted.
    if not _is_safe_push_endpoint(body.endpoint):
        raise HTTPException(422, "endpoint must be a well-formed public https URL")

    # 3. Per-user budget (NOT per-IP: enforce_rate_limit keys on the client IP and must
    #    not be used here — a team behind one NAT would share a bucket).
    if not rate_limit.check_rate(
        settings.PUSH_SUBSCRIBE_RATE_LIMIT, "push-subscribe", user.source_user_id
    ):
        raise HTTPException(429, "too many subscription updates — try again shortly")

    # 4. Write.
    await push_repo.upsert(
        session,
        user_id=user.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=body.user_agent,
    )
    await session.commit()
    # The endpoint is a bearer-ish capability for delivering to that browser: log that a
    # subscription changed, never the endpoint or the key material itself.
    log.info("push.subscribe.stored", user_sub=user.source_user_id)
    return {"status": "subscribed"}


# ── POST /v1/push/unsubscribe ────────────────────────────────────────────────


class UnsubscribeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(..., min_length=1, max_length=4096)


@router.post("/push/unsubscribe", status_code=204)
async def push_unsubscribe(
    body: UnsubscribeBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Forget ONE of the caller's own devices. Always 204.

    The delete is scoped to `user_id` AND `endpoint`. Scoping to the endpoint alone
    would let any signed-in caller who learned an endpoint string silence that person's
    device (T-27-03-03); the unscoped helper exists only for the 404/410 prune path in
    plan 27-04 and is deliberately not reachable from here.

    The status code is 204 whether or not a row matched. Answering 404 for "no such
    subscription of yours" would confirm, to anyone who asks, whether a given endpoint
    belongs to somebody else — an existence oracle over other people's devices
    (T-27-03-04). Deleting a device you already deleted is also simply not an error.
    """
    user = _require_user_principal(principal)

    deleted = await push_repo.delete_for_user(
        session, user_id=user.id, endpoint=body.endpoint
    )
    await session.commit()
    log.info("push.unsubscribe.done", user_sub=user.source_user_id, deleted=deleted)
    return Response(status_code=204)

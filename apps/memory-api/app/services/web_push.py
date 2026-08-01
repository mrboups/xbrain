"""Web push sender — VAPID-signed delivery, fire-and-forget, prune on gone (Phase 27, PUSH-01).

A stored subscription is inert until something sends to it. This module is that
something, and it owns three disciplines:

**What is allowed to trigger a send (D-27-06).** Exactly two events: a message that
@mentions you, and a Phase-22 nudge. The wiring lives in `app/routes/team_chat.py`, but
the reasoning belongs next to the payload builders: a group chat that pushes on every
message trains people to disable notifications system-wide, and then the two that
actually mattered are lost along with the noise. Restraint here is what keeps the
channel worth anything.

**What a payload may carry.** The payload is encrypted end-to-end, but it is decrypted
ON A DEVICE WE DO NOT CONTROL and rendered on a lock screen anyone holding the phone can
read. Treat it as content that has left our trust boundary: a title, a
`PUSH_PREVIEW_CHARS`-capped preview, a first-party app URL and a tag. Never a token,
never a full message body, never anything that would authenticate the holder.

**When a failure means "delete".** Only 404/410 — see `PRUNE_STATUSES`.

The private half of the VAPID keypair is read HERE and nowhere else outside
`app/config.py`: this is the only module that signs. It is never returned to a caller,
never serialized, and never logged.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit
from uuid import UUID

import structlog
from pywebpush import WebPushException, webpush
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.repos import push_subscriptions as push_repo

log = structlog.get_logger(__name__)


# The push service answering 404 or 410 is it telling us the mailbox is GONE — the
# browser revoked the subscription, cleared its storage, or the endpoint was replaced.
# No retry can ever succeed, so keeping the row costs one doomed HTTPS request per
# notification forever, against a third party that has already refused. Deleting it is
# the only correct response (T-27-04-03).
#
# The inverse matters just as much: every OTHER status (429, 5xx) and every transport
# error is TRANSIENT. Deleting on those would silently unsubscribe a live device because
# FCM had a bad minute, and the user would have no way to know their notifications had
# stopped. Widening this set is therefore never a safe "cleanup" — it is data loss.
PRUNE_STATUSES = frozenset({404, 410})


# ── Payload builders (pure — no I/O, no DB, no keys) ─────────────────────────


def _preview(text: str, limit: int) -> str:
    """The first `limit` characters, with a single ellipsis when the text was cut.

    The cap is a privacy control, not cosmetics: this string is displayed on a lock
    screen. The ellipsis is deliberate — a silently truncated sentence reads as if the
    author wrote it that way.
    """
    cleaned = (text or "").strip()
    if limit <= 0:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def build_mention_payload(*, team_slug: str, author_label: str, content: str, url: str) -> dict:
    """The notification for "someone @mentioned you in team chat".

    `author_label` comes from the authenticated sender's own User row, never from the
    request body — a forged label on a lock screen is a phishing surface (T-27-04-06).
    `url` is a first-party path on the app origin; it never carries a credential,
    because this whole structure passes through FCM/Mozilla to reach the device.
    """
    return {
        "kind": "mention",
        "title": f"{author_label} mentioned you",
        "body": _preview(content, settings.PUSH_PREVIEW_CHARS),
        "url": url,
        "tag": f"mention:{team_slug}",
    }


def build_nudge_payload(*, sender_label: str, target_url: str, app_url: str) -> dict:
    """The notification for a Phase-22 nudge ("open this link").

    The link is the BODY — the recipient must see where they would be going — but the
    notification's `url`, which is what the OS opens on a tap, is `app_url`. Never
    `target_url`. A notification that navigated straight to a teammate-supplied
    destination would bypass the consent gate D-22-02 exists to enforce: a message from
    another user never moves the recipient's browser without their click on a surface
    that showed them the destination first (T-27-04-02).
    """
    return {
        "kind": "nudge",
        "title": f"{sender_label} wants to open a link",
        "body": _preview(target_url, settings.PUSH_PREVIEW_CHARS),
        "url": app_url,
        "tag": "nudge",
    }


# ── Send ─────────────────────────────────────────────────────────────────────


def push_is_configured() -> bool:
    """True when this install can actually deliver a push.

    Read at CALL time (not import time) so an operator's `.env` change takes effect on
    restart without any import-order subtlety, and so a test can flip it. Both key
    halves are required: a public key with nothing to sign with produces a subscription
    that can never receive anything. `vapid_is_signable` is the boolean accessor from
    27-03, so callers can ask the question without naming the secret.
    """
    return bool(settings.PUSH_ENABLED and settings.VAPID_PUBLIC_KEY and settings.vapid_is_signable)


def _host(endpoint: str) -> str:
    """The endpoint's HOST, which is all a log line ever gets.

    An operator needs to tell FCM from Mozilla from Apple when deliveries fail. Nobody
    needs the path: the full endpoint URL is a capability — anyone holding it can push
    to that device — and a log aggregator is the wrong place to store capabilities
    (T-27-04-07).
    """
    try:
        return urlsplit(endpoint).hostname or "?"
    except ValueError:
        return "?"


def _send_one(sub_info: dict, data: str) -> None:
    """Encrypt and POST one notification. SYNCHRONOUS — always call via a worker thread.

    Raises `WebPushException` on an HTTP-level refusal (with `.response` carrying the
    push service's status) and other exceptions on transport failures.

    `vapid_claims` is rebuilt on every call on purpose: pywebpush MUTATES the dict it is
    given, stamping `aud` (derived from the endpoint's origin) and `exp` into it. A
    shared dict would carry the first push service's audience into the second's request
    and get every subsequent delivery rejected.
    """
    webpush(
        subscription_info=sub_info,
        data=data,
        vapid_private_key=settings.VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.VAPID_SUBJECT},
        ttl=settings.PUSH_TTL_S,
    )


async def send_to_user(session: AsyncSession, *, user_id: UUID, payload: dict) -> dict:
    """Deliver `payload` to every device `user_id` has subscribed. Returns a count dict.

    One device's failure never stops the others — a stale laptop must not be able to
    mute someone's phone. Successful deliveries stamp `last_used_at`; endpoints the push
    service reports as gone are collected and deleted in one pass at the end, so the
    whole fan-out costs a single commit.

    The caller's session is used as-is (the background entrypoint below opens its own);
    this function commits because the prune and the stamps are its own writes, not the
    caller's.
    """
    if not push_is_configured():
        return {"sent": 0, "pruned": 0, "skipped": True}

    subs = await push_repo.list_for_user(session, user_id=user_id)
    if not subs:
        return {"sent": 0, "pruned": 0}

    data = json.dumps(payload, separators=(",", ":"))
    kind = payload.get("kind") if isinstance(payload, dict) else None
    dead: list[str] = []
    sent = 0

    for sub in subs:
        sub_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            await asyncio.to_thread(_send_one, sub_info, data)
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in PRUNE_STATUSES:
                dead.append(sub.endpoint)
                continue
            # Transient by default. `status=None` means we never got a verdict at all
            # (DNS, TLS, timeout) — even less evidence that the mailbox is gone.
            log.warning(
                "web_push.send_failed", status=status, host=_host(sub.endpoint), kind=kind
            )
            continue
        except Exception as exc:  # noqa: BLE001
            # Not every failure arrives as a WebPushException (a serialization bug, a
            # library change). Unrecognised means transient: deletion needs evidence.
            log.warning("web_push.send_error", err=str(exc), host=_host(sub.endpoint), kind=kind)
            continue
        sent += 1
        await push_repo.touch(session, endpoint=sub.endpoint)

    for endpoint in dead:
        await push_repo.delete_by_endpoint(session, endpoint=endpoint)

    if sent or dead:
        await session.commit()

    if sent:
        log.info("web_push.sent", user_id=str(user_id), count=sent, kind=kind)
    if dead:
        log.info(
            "web_push.pruned",
            user_id=str(user_id),
            count=len(dead),
            hosts=sorted({_host(e) for e in dead}),
        )
    return {"sent": sent, "pruned": len(dead)}


async def send_to_user_bg(*, user_id: UUID, payload: dict) -> None:
    """`asyncio.create_task` entrypoint — opens its own session and NEVER raises.

    Two reasons this shape is not optional:

      * The request-scoped session from `Depends(get_session)` is already CLOSED by the
        time a create_task body runs, so a background sender must open its own
        (the pattern `team_chat_agent.handle_claude_mention` established).
      * A raise here surfaces as an unretrieved-task exception and can propagate into
        the caller's task group. A notification failing must never fail the message that
        triggered it.
    """
    if not push_is_configured():
        return
    kind = payload.get("kind") if isinstance(payload, dict) else None
    try:
        async with async_session_factory() as session:
            await send_to_user(session, user_id=user_id, payload=payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("web_push.background_error", err=str(exc), kind=kind, user_id=str(user_id))

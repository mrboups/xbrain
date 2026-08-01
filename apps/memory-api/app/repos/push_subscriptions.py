"""Push-subscription repo — per-user AND per-device mailboxes (Phase 27 PUSH-01, D-27-05).

Conventions mirror repos/team_invite_codes.py: async functions, ``session`` first,
keyword-only args, no commit inside — the caller (the route) owns the commit.

Two disciplines this module owns, both security-critical:

  * ``upsert`` TRANSFERS ownership of an endpoint instead of duplicating it. The table
    is unique on ``endpoint`` ALONE, so a shared browser that a second person signs
    into cannot leave the first person's row alive and still receiving the newcomer's
    notifications (T-27-03-02; the migration docstring carries the long reasoning).

  * ``delete_for_user`` filters on ``user_id`` **and** ``endpoint``, always. Deleting by
    endpoint alone is reachable ONLY from the prune path (``delete_by_endpoint``, plan
    27-04, when the push service answers 404/410). If a user-facing route could delete
    by endpoint alone, any signed-in caller who learned an endpoint string could
    silence another person's device (T-27-03-03).

UUID parameters are bound as ``uuid.UUID`` objects, never as strings. A ``sa.text()``
statement carries no type information, so SQLAlchemy hands the values straight to
asyncpg, and asyncpg type-checks strictly: a ``str`` where the server expects ``uuid``
raises at execute time. ``team_invite_codes.redeem_atomic`` binds the same way.
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push import PushSubscription

# Columns every write hands back, in one place so the two statements cannot drift.
_RETURNING = "id, user_id, endpoint, p256dh, auth, user_agent, created_at, last_used_at"


async def upsert(
    session: AsyncSession,
    *,
    user_id: UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None,
) -> sa.Row:
    """Store (or take over) the subscription for ``endpoint`` and return the stored row.

    An endpoint already on file is UPDATED IN PLACE, including its ``user_id`` — an
    ownership TRANSFER, never a second row. This is the whole point of the unique index
    on ``endpoint``: on a shared device the browser re-issues the same endpoint to the
    next person who signs in, and the previous occupant must stop being a delivery
    target the moment that happens.

    ``created_at`` is reset on takeover (the subscription is new *for this user*) and
    ``last_used_at`` is cleared, so a transferred row never carries the previous
    occupant's delivery history. ``id`` is left to the DDL's ``gen_random_uuid()``.
    Returns a Row (mirrors ``team_invite_codes.redeem_atomic``); the repo does not commit.
    """
    result = await session.execute(
        sa.text(f"""
            INSERT INTO push_subscriptions
                (user_id, endpoint, p256dh, auth, user_agent)
            VALUES
                (:user_id, :endpoint, :p256dh, :auth, :user_agent)
            ON CONFLICT (endpoint) DO UPDATE SET
                user_id      = EXCLUDED.user_id,
                p256dh       = EXCLUDED.p256dh,
                auth         = EXCLUDED.auth,
                user_agent   = EXCLUDED.user_agent,
                created_at   = now(),
                last_used_at = NULL
            RETURNING {_RETURNING}
        """),
        {
            "user_id": user_id,
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": user_agent,
        },
    )
    row = result.first()
    assert row is not None  # INSERT ... DO UPDATE always yields exactly one row
    return row


async def list_for_user(session: AsyncSession, *, user_id: UUID) -> list[PushSubscription]:
    """Every device this user has subscribed, newest first.

    The fan-out source for the send path (plan 27-04) and for any "your devices" view.
    Scoped to one ``user_id`` — there is deliberately no "list all" helper.
    """
    result = await session.execute(
        select(PushSubscription)
        .where(PushSubscription.user_id == user_id)
        .order_by(PushSubscription.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_for_user(session: AsyncSession, *, user_id: UUID, endpoint: str) -> int:
    """Remove ONE of this user's devices. Returns the number of rows deleted (0 or 1).

    BOTH predicates, always. ``user_id`` is what stops a signed-in caller who learned
    (or guessed) another person's endpoint string from silencing their device; matching
    on ``endpoint`` alone here would be exactly that hole (T-27-03-03). The route
    reports 204 whichever count comes back, so the return value is never an existence
    oracle for the caller — it exists for logging and tests.
    """
    result = await session.execute(
        delete(PushSubscription).where(
            (PushSubscription.user_id == user_id) & (PushSubscription.endpoint == endpoint)
        )
    )
    return result.rowcount or 0


async def delete_by_endpoint(session: AsyncSession, *, endpoint: str) -> int:
    """PRUNE path ONLY — drop a dead mailbox regardless of who owns it.

    Called when the push service answers 404/410 ("subscription gone", D-27-05): the
    browser has revoked or replaced it, so retrying forever is waste. NO HTTP route may
    reach this function — it has no user predicate, so exposing it would let any caller
    delete any device. Its only caller is the send path in plan 27-04.
    """
    result = await session.execute(
        delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )
    return result.rowcount or 0


async def touch(session: AsyncSession, *, endpoint: str) -> None:
    """Stamp ``last_used_at`` after a successful delivery, so a stale device is visible.

    Best-effort bookkeeping: a missing endpoint updates nothing and is not an error.
    """
    await session.execute(
        sa.update(PushSubscription)
        .where(PushSubscription.endpoint == endpoint)
        .values(last_used_at=func.now())
    )

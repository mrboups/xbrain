"""push_subscriptions — one browser push mailbox per device (Phase 27 PUSH-01, D-27-05).

Stores the three values a browser hands back from `PushManager.subscribe()` — the
`endpoint` URL the push service will accept messages on, and the `p256dh` / `auth`
keys that encrypt the payload — bound to the user who enabled notifications.

Why the unique index is on `endpoint` ALONE, not on `(user_id, endpoint)`
-------------------------------------------------------------------------
This is the decision a later reader will question, so it is written down. A push
endpoint is a mailbox for ONE BROWSER INSTANCE, not for one account. On a shared
device — a family laptop, a hot-desk machine, a demo browser — a second person signs
in and enables notifications, and the browser hands back the SAME endpoint it gave the
first person. Under a composite `(user_id, endpoint)` key both rows would coexist:
the first account's row stays alive and keeps receiving the NEW occupant's
notifications. That is a cross-user disclosure, and it is invisible in testing because
a private device never produces the collision.

Unique-on-endpoint makes that state unrepresentable. It forces the subscribe path to
TRANSFER ownership instead of accumulating rows:

    INSERT ... ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id, ...

so at most one account is ever reachable at a given endpoint — the one that most
recently proved it is sitting at that browser. This is what makes "a subscription is
never readable by another user" true on a shared device and not merely on a private
one (T-27-03-02).

Why the user FK cascades
------------------------
Deleting an account must not leave a live delivery channel behind: a row whose owner
no longer exists would still be a valid target for the send path. The FK below removes
the subscription with the user, in the same statement, with no cleanup job to forget.

Per-device revocation (D-27-05) needs no extra column: the row IS the device. One row
per endpoint, N rows per user, so deleting one row silences exactly one browser and
leaves the person's other devices subscribed. `last_used_at` is written by the send
path (plan 27-04) and `user_agent` is a human label for a "your devices" list — neither
is load-bearing for delivery.

Additive, forward-only (Phase-17 pattern): CREATE TABLE / CREATE INDEX with IF NOT
EXISTS, no data backfill, and no branch on the install flavour — the table is created
unconditionally so the released schema is identical everywhere (the migration test suite
asserts that no migration reads the install-flavour flag at all). Raw SQL +
`gen_random_uuid()` (pgcrypto is preloaded in the containers), mirroring 0028_boards. The
`downgrade()` is present for symmetry only; release validation never invokes it.

Revision ID: 0029_push_subscriptions
Revises: 0028_boards
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0029_push_subscriptions"
down_revision: Union[str, None] = "0028_boards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint     TEXT NOT NULL,          -- the push service URL; the mailbox identity
            p256dh       TEXT NOT NULL,          -- client public key (payload encryption)
            auth         TEXT NOT NULL,          -- client auth secret (payload encryption)
            user_agent   TEXT,                   -- human label for a "your devices" list
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ             -- stamped by the send path (plan 27-04)
        )
    """)
    # THE load-bearing constraint (see module docstring): unique on `endpoint` ALONE, so a
    # second person on a shared browser TRANSFERS the mailbox rather than adding a row
    # beside the previous occupant's.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_push_subscriptions_endpoint "
        "ON push_subscriptions (endpoint)"
    )
    # Fan-out lookup: the send path resolves "every device this user has" per notification.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_push_subscriptions_user_id "
        "ON push_subscriptions (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_push_subscriptions_user_id")
    op.execute("DROP INDEX IF EXISTS ux_push_subscriptions_endpoint")
    op.execute("DROP TABLE IF EXISTS push_subscriptions")

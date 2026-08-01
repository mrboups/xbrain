"""users profile columns — the name a person chooses, plus bio and avatar.

Adds four nullable columns to `users`. All four are additive and nullable, so an
existing row is a valid profile the moment this runs: nobody has a preferred name
or a bio yet, and the label ladder in app/services/user_label.py falls through to
the values already present (display_name, then the email local part).

Why `preferred_name` is a NEW column and not a rewrite of `display_name`
-----------------------------------------------------------------------
`display_name` is the IDENTITY PROVIDER's value — what Google handed us at
sign-in. Letting a user edit that column in place would destroy the provider
value with no way back: the next sign-in would either overwrite the person's
chosen name or, if we suppressed that write, silently freeze a stale copy of the
provider's. Two columns make the precedence explicit and reversible — clearing
`preferred_name` (PATCH with "") restores the provider name with no support
ticket and no backfill.

Why `avatar_media_id` carries NO foreign key
--------------------------------------------
An avatar is an ordinary media item created through POST /v1/media/upload, which
writes through the pluggable MemoryProvider (app/deps.py::_build_provider). Only
the `native` backend lands that item in Postgres `memory_items`; the `mem0` and
`stub` backends never do, and `stub` is the code default. A REFERENCES clause to
`memory_items` would therefore turn "set your avatar" into an IntegrityError on
any install not running the native backend — a core account feature broken by a
swappable storage choice. No other table in this schema references memory_items
either. Instead the id is validated against the provider at PATCH time (the item
must exist and be a media item), and a dangling id degrades to a 404 image rather
than a 500. Restoring the FK later is one ALTER TABLE if the backend ever becomes
fixed.

Why `avatar_media_team_scope` exists
------------------------------------
Media reads are team-scoped end to end: GET /v1/media/{id}/img takes a signed
token whose `team_scope` claim must match the scope the item was uploaded under
(routes/media_helpers.py::verify_media_token → provider.get(team_scope=...)).
Minting a readable URL at profile-read time therefore needs the scope the avatar
LIVES in, and a person belongs to several teams. Storing the id alone would make
the URL unmintable for anyone whose current header scope differs from the upload
scope. This column records that scope once, at the moment the avatar is set, so a
read mints a token bound to exactly that one item in exactly that one team — the
token's scope is never widened to make the picture load.

Additive, forward-only (Phase-17 pattern): ADD COLUMN IF NOT EXISTS, no backfill,
no branch on the install flavour. `downgrade()` drops only what `upgrade()` added.

Revision ID: 0030_user_profile
Revises: 0029_push_subscriptions
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0030_user_profile"
down_revision: Union[str, None] = "0029_push_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # preferred_name: set ONLY by the person themselves (PATCH /v1/me/profile).
    # No other route, and no provider sign-in path, writes this column.
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_name VARCHAR(128)"
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bio VARCHAR(400)")
    # See module docstring: intentionally no REFERENCES memory_items(id).
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_media_id UUID")
    # 64 matches the team_scope width used everywhere else (memory_items.team_scope).
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_media_team_scope VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_media_team_scope")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_media_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS bio")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS preferred_name")

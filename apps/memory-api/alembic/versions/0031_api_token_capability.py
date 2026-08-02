"""user_api_tokens.capability — a token that can do exactly one thing.

Why this column exists
----------------------
The iOS import path is a Shortcut. A Shortcut lives ON the device, is backed up
with the device, and can be shared with one tap — so whatever credential it
carries must be assumed to leak eventually. Putting the account's full `xbt_`
token in it would hand the whole account (chat, brain, teams, every /v1/me
route) to anyone who receives that shortcut.

`capability` makes a token narrower than its owner. NULL — every token minted
before this migration, and every token the existing POST /v1/me/api-token still
mints — means "full access, exactly as before", so this change is invisible to
the deployed fleet. A non-NULL value means the token is refused everywhere
except the endpoints that capability names, enforced centrally in
app/deps.py::get_current_principal against an allow-list keyed on the request
path. Deny-by-default: an endpoint added next year is refused until someone
deliberately adds it to the list.

Additive, forward-only, no backfill (Phase-17 pattern). The partial index keeps
the auth lookup on scoped tokens off the full-table path used by `xbt_`.

Revision ID: 0031_api_token_capability
Revises: 0030_user_profile
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0031_api_token_capability"
down_revision: Union[str, None] = "0030_user_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL = full account access (every existing row). Non-NULL = restricted.
    op.execute("ALTER TABLE user_api_tokens ADD COLUMN IF NOT EXISTS capability TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_capability "
        "ON user_api_tokens(user_id, capability) WHERE capability IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_api_tokens_capability")
    op.execute("ALTER TABLE user_api_tokens DROP COLUMN IF EXISTS capability")

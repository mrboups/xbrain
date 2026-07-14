"""Shared xbt_ token mint helper (D-18-01) — reuse, do not fork.

Mirrors `apps/memory-api/app/routes/auth_github.py:350-367`'s `_mint_xbt_for_user`
EXACTLY (same raw-token construction, same SHA-256 hash-at-rest, same multi-team
`team_scope=''` sentinel), parameterized so callers supply their own `name`. This is
the ONE place local-auth's register/login routes (Plan 03) mint a session token —
they must NOT hand-roll a second, divergent mint that could drift from what
`get_current_principal` (`app/deps.py:225-272`) validates.

`auth_github.py`'s private `_mint_xbt_for_user` is intentionally left untouched
(not imported from here, not refactored to call this) so the live GitHub sign-in
path (SC#4) has zero blast radius from this phase (T-18-02-05).
"""

import hashlib
import secrets

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def mint_xbt_for_user(session: AsyncSession, user_id, *, name: str) -> str:
    """Insert a new xbt_ personal API token row and return the RAW token once.

    `team_scope` is the empty-string multi-team sentinel (see auth_github.py's
    docstring on `_mint_xbt_for_user`): `app/deps.py`'s `get_team_scope` treats an
    empty `api_token_team_scope` as "multi-team OK", so any `X-Team-Scope` header
    passes — this is what makes the local-auth principal indistinguishable
    (LAUTH-02) from a GitHub/Google-minted `xbt_` token without any deps.py change.
    """
    raw = "xbt_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    await session.execute(
        sa.text("""
            INSERT INTO user_api_tokens (id, user_id, token_hash, team_scope, name, created_at)
            VALUES (gen_random_uuid(), :user_id, :hash, '', :name, now())
        """),
        {"user_id": user_id, "hash": token_hash, "name": name},
    )
    return raw

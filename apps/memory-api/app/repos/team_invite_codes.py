"""Team invite-code repo — hash-at-rest mint + race-safe atomic redeem (Phase 25 JOINCODE-01).

A join code is a BEARER SECRET to the team-scoped brain. This repo owns the
security discipline the rest of the phase (routes in Plan 02, the real-Postgres
gate in Plan 03) builds against:

  * generate_code() mints a high-entropy ``xbi_`` plaintext and returns it together
    with its sha256 hash + a non-secret display prefix (D-25-01). It is PURE — no DB,
    no I/O — so the hash-at-rest contract is trivially unit-testable.
  * mint_code() persists ONLY the hash + prefix and returns the plaintext to the
    caller exactly once; the caller must NOT log or persist it.
  * redeem_atomic() is a SINGLE conditional UPDATE that increments ``uses`` only if
    the code still redeems (not revoked, not expired, under max_uses). The row lock
    the UPDATE takes serializes concurrent redemptions, so a double-spend can never
    push ``uses`` past ``max_uses`` (D-25-02; proven with racing sessions in Plan 03).

Conventions mirror repos/teams.py: async functions, keyword-only args, the repo
FLUSHES and the route owns the commit.
"""

from datetime import datetime, timezone
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oauth_tokens import hash_token
from app.models.team import TeamInviteCode

CODE_PREFIX = "xbi_"


def generate_code() -> tuple[str, str, str]:
    """Mint a fresh invite code. PURE — no DB, no I/O.

    Returns ``(plaintext, code_hash, code_prefix)``:
      * ``plaintext`` = ``"xbi_"`` + ``secrets.token_urlsafe(24)`` — a ≈192-bit CSPRNG
        secret (enumeration infeasible, D-25-01). Returned to the caller ONCE; NEVER
        stored or logged.
      * ``code_hash`` = ``sha256(plaintext)`` lowercase hex (64 chars) — the ONLY form
        that reaches the DB. The redeem lookup is by hash, so there is no plaintext
        timing oracle and a DB leak yields no usable code.
      * ``code_prefix`` = the first 12 chars of the plaintext (``"xbi_"`` + 8) — a
        non-secret label for admin display only.
    """
    import secrets

    plaintext = CODE_PREFIX + secrets.token_urlsafe(24)
    code_hash = hash_token(plaintext)  # sha256 hex — reuses the bearer-secret helper (DRY)
    code_prefix = plaintext[:12]
    return plaintext, code_hash, code_prefix


async def mint_code(
    session: AsyncSession,
    *,
    team_id: UUID,
    created_by_user_id: UUID | None,
    role: str,
    expires_at: datetime | None,
    max_uses: int | None,
) -> tuple[TeamInviteCode, str]:
    """Create a new invite code row and return ``(row, plaintext)``.

    Persists ONLY the sha256 hash + non-secret prefix (D-25-01). The plaintext is
    returned to the caller EXACTLY ONCE — the caller (Plan 02's mint endpoint) surfaces
    it in the response body and must NOT log or persist it anywhere else. The repo
    flushes; the route commits.
    """
    plaintext, code_hash, code_prefix = generate_code()
    row = TeamInviteCode(
        team_id=team_id,
        code_hash=code_hash,
        code_prefix=code_prefix,
        role=role,
        created_by_user_id=created_by_user_id,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    session.add(row)
    await session.flush()
    return row, plaintext


async def get_by_hash(
    session: AsyncSession, *, code_hash: str
) -> TeamInviteCode | None:
    """Resolve an invite code by its sha256 hash (the redeem lookup is by HASH, never
    by plaintext — no timing oracle, D-25-01). Returns None if no such code."""
    result = await session.execute(
        select(TeamInviteCode).where(TeamInviteCode.code_hash == code_hash)
    )
    return result.scalar_one_or_none()


async def redeem_atomic(
    session: AsyncSession, *, code_id: UUID, now: datetime
) -> sa.Row | None:
    """Atomically consume one use of a code — THE double-spend guard (D-25-02).

    Runs a SINGLE conditional UPDATE that increments ``uses`` only if the code still
    redeems, re-checking revocation, expiry and the max-uses ceiling INSIDE the same
    statement (no read-then-write TOCTOU window). Returns the updated Row
    ``(id, team_id, role, uses)`` on success, or None if the code was already revoked,
    expired, or exhausted.

    Race-safety: the UPDATE takes a row lock, so two concurrent redemptions of the same
    code serialize. The loser then sees the already-incremented ``uses`` and the
    ``uses < max_uses`` predicate fails, so it matches no row and returns None — ``uses``
    can never exceed ``max_uses`` even under concurrency (proven with racing sessions in
    Plan 03). This function does NOT add the member and does NOT commit — the route
    (Plan 02) calls add_member + commit only after a non-None return.
    """
    result = await session.execute(
        sa.text("""
            UPDATE team_invite_codes
               SET uses = uses + 1
             WHERE id = :code_id
               AND revoked_at IS NULL
               AND (expires_at IS NULL OR expires_at > :now)
               AND (max_uses IS NULL OR uses < max_uses)
         RETURNING id, team_id, role, uses
        """),
        {"code_id": code_id, "now": now},
    )
    return result.first()


async def revoke_code(
    session: AsyncSession, *, team_id: UUID, code_id: UUID
) -> TeamInviteCode | None:
    """Soft-revoke a code (set ``revoked_at``). Scoped to ``team_id`` so one team can
    never revoke another team's code (team_scope integrity).

    Returns the row on success, or None if no code with ``code_id`` exists in this team.
    Idempotent: an already-revoked code is returned unchanged (revoked_at not touched).
    ``revoked_at`` is a Python datetime (mirrors repos.teams.block_member) so the returned
    row stays ``.isoformat()``-safe for serialisers. The repo flushes; the route commits.
    """
    result = await session.execute(
        select(TeamInviteCode).where(
            (TeamInviteCode.id == code_id) & (TeamInviteCode.team_id == team_id)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = datetime.now(tz=timezone.utc)
        await session.flush()
    return row


async def list_codes(
    session: AsyncSession, *, team_id: UUID
) -> list[TeamInviteCode]:
    """Return all invite codes for a team, newest first. The ENDPOINT (Plan 02)
    serializes metadata + ``code_prefix`` + counts ONLY — ``code_hash`` is NEVER
    returned to a client and no plaintext is recoverable. The repo returns the ORM rows.
    """
    result = await session.execute(
        select(TeamInviteCode)
        .where(TeamInviteCode.team_id == team_id)
        .order_by(TeamInviteCode.created_at.desc())
    )
    return list(result.scalars().all())

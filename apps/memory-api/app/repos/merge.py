"""Phase 10 — atomic orphan-row merge.

Re-parents FK references from orphan_id to survivor_id in one transaction,
then sets users.merged_into_user_id on the orphan (soft-delete).
Per RESEARCH.md Q3 + Pitfall 5: role priority is "admin wins" on conflict.

Idempotent: if orphan.merged_into_user_id is already set, returns early.
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def merge_user_rows(
    session: AsyncSession, *, orphan_id: UUID, survivor_id: UUID
) -> None:
    """Merge orphan_id into survivor_id. Must be called inside an open
    transaction — the caller is responsible for commit/rollback.

    Operations (in order):
      1. team_members: INSERT missing rows from orphan into survivor with
         role priority `admin wins`, then DELETE orphan's rows.
      2. UPDATE conversations.owner_user_id
      3. UPDATE user_api_tokens.user_id  (Phase 10 M-3: pre-merge xbt_ tokens
         minted for the orphan must follow into the survivor so they keep
         working after merge — see test_merge_migrates_api_tokens.)
      4. UPDATE user_external_sessions.user_id
      5. UPDATE granola_user_connections.user_id
      6. UPDATE team_join_requests.user_id
      7. UPDATE tasks.created_by (set-null on delete but still re-parent)
      8. UPDATE promotions (proposed_by / approved_by_1 / approved_by_2)
      9. UPDATE agent_definitions.created_by
     10. audit_log + team_messages: LEFT AS-IS (immutable history)
     11. Set orphan.merged_into_user_id = survivor_id.
    """
    if orphan_id == survivor_id:
        return

    # Idempotency guard — if already merged, no-op.
    existing = (await session.execute(
        sa.text("SELECT merged_into_user_id FROM users WHERE id = :id"),
        {"id": orphan_id},
    )).scalar()
    if existing is not None:
        return

    params = {"orphan": orphan_id, "survivor": survivor_id}

    # 1. team_members — copy missing rows with admin-wins priority.
    # The UPSERT below: if the survivor already has a row in this team and
    # either side is admin, promote to admin.
    await session.execute(sa.text("""
        INSERT INTO team_members (team_id, user_id, role, joined_at, blocked_at, blocked_by)
        SELECT team_id, :survivor, role, joined_at, blocked_at, blocked_by
        FROM team_members
        WHERE user_id = :orphan
        ON CONFLICT (team_id, user_id) DO UPDATE
        SET role = CASE
            WHEN EXCLUDED.role = 'admin' OR team_members.role = 'admin' THEN 'admin'
            ELSE 'member'
        END
    """), params)

    await session.execute(
        sa.text("DELETE FROM team_members WHERE user_id = :orphan"), params
    )

    # 2-9. UPDATE FK references.
    # Phase 10 M-3: user_api_tokens MUST be re-pointed so any xbt_ token minted
    # for the orphan before the merge keeps resolving to the survivor identity
    # via deps.py auth path (the SUMMARY of this fix lives in plan 10-06 test
    # test_orphan_token_lands_on_survivor).
    for stmt in [
        "UPDATE conversations SET owner_user_id = :survivor WHERE owner_user_id = :orphan",
        "UPDATE user_api_tokens SET user_id = :survivor WHERE user_id = :orphan",
        "UPDATE user_external_sessions SET user_id = :survivor WHERE user_id = :orphan",
        "UPDATE granola_user_connections SET user_id = :survivor WHERE user_id = :orphan",
        "UPDATE team_join_requests SET user_id = :survivor WHERE user_id = :orphan",
        "UPDATE tasks SET created_by = :survivor WHERE created_by = :orphan",
        # promotions has 3 user FK columns; re-parent all three.
        "UPDATE promotions SET proposed_by = :survivor WHERE proposed_by = :orphan",
        "UPDATE promotions SET approved_by_1 = :survivor WHERE approved_by_1 = :orphan",
        "UPDATE promotions SET approved_by_2 = :survivor WHERE approved_by_2 = :orphan",
        # agent_definitions has created_by (SET NULL on delete but re-parent for accuracy).
        "UPDATE agent_definitions SET created_by = :survivor WHERE created_by = :orphan",
    ]:
        await session.execute(sa.text(stmt), params)

    # 11. Soft-delete the orphan.
    await session.execute(
        sa.text("UPDATE users SET merged_into_user_id = :survivor WHERE id = :orphan"),
        params,
    )

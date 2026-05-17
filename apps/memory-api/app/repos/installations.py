"""Phase 12 — repo helpers for the installations table.

All public functions take an AsyncSession and commit at the end. The
caller (webhook route) is responsible for transaction boundaries when
calling multiple in sequence — but in practice webhooks are single-event,
so each helper owns its commit.

Companion model: app.models.installation.Installation. Companion route:
app.routes.webhooks_github (Plan 12-05). The `installations` table is
populated and updated EXCLUSIVELY through these helpers; on-demand
discovery (Plan 12-03 hybrid lookup) goes through upsert_installation
too so the source-of-truth code path is single.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.installation import Installation

log = structlog.get_logger(__name__)


async def upsert_installation(
    session: AsyncSession,
    *,
    installation_id: int,
    github_org_login: str,
    github_account_type: str,
    installed_by_github_id: int | None,
    permissions: dict[str, Any],
    raw_payload: dict[str, Any],
) -> None:
    """Insert or update an installation row (on installation.created).

    Re-installs (uninstall then install) generate a new installation_id; the
    OLD row keeps its revoked_at set, the NEW row is INSERT'd. The partial
    unique index on (github_org_login) WHERE revoked_at IS NULL guarantees
    only one active row per org — so re-install scenarios are safe.

    ON CONFLICT (installation_id) DO UPDATE handles edge cases like:
      - duplicate webhook delivery for the same installation_id
      - manual on-demand backfill via find_installation_for_org racing with
        the webhook
    """
    stmt = pg_insert(Installation).values(
        installation_id=installation_id,
        github_org_login=github_org_login,
        github_account_type=github_account_type,
        installed_by_github_id=installed_by_github_id,
        permissions=permissions,
        raw_payload=raw_payload,
        revoked_at=None,        # re-create explicitly clears revoked_at
        suspended_at=None,
    ).on_conflict_do_update(
        index_elements=[Installation.installation_id],
        set_={
            "github_org_login": github_org_login,
            "github_account_type": github_account_type,
            "installed_by_github_id": installed_by_github_id,
            "permissions": permissions,
            "raw_payload": raw_payload,
            "revoked_at": None,
            "suspended_at": None,
            "updated_at": sa.func.now(),
        },
    )
    await session.execute(stmt)
    await session.commit()
    log.info(
        "installation.upsert",
        installation_id=installation_id,
        org_login=github_org_login,
    )


async def revoke_installation(session: AsyncSession, installation_id: int) -> None:
    """Mark the installation row as revoked (installation.deleted webhook).

    Row is NEVER hard-deleted — keeps history. Future re-install gets a NEW
    installation_id and a NEW row.
    """
    await session.execute(
        sa.update(Installation)
        .where(Installation.installation_id == installation_id)
        .values(revoked_at=datetime.now(timezone.utc), updated_at=sa.func.now())
    )
    await session.commit()
    log.info("installation.revoke", installation_id=installation_id)


async def suspend_installation(session: AsyncSession, installation_id: int) -> None:
    """installation.suspend → set suspended_at = now."""
    await session.execute(
        sa.update(Installation)
        .where(Installation.installation_id == installation_id)
        .values(suspended_at=datetime.now(timezone.utc), updated_at=sa.func.now())
    )
    await session.commit()
    log.info("installation.suspend", installation_id=installation_id)


async def unsuspend_installation(session: AsyncSession, installation_id: int) -> None:
    """installation.unsuspend → clear suspended_at."""
    await session.execute(
        sa.update(Installation)
        .where(Installation.installation_id == installation_id)
        .values(suspended_at=None, updated_at=sa.func.now())
    )
    await session.commit()
    log.info("installation.unsuspend", installation_id=installation_id)


async def update_installation_permissions(
    session: AsyncSession,
    *,
    installation_id: int,
    permissions: dict[str, Any],
    raw_payload: dict[str, Any],
) -> None:
    """installation.new_permissions_accepted → update permissions JSONB."""
    await session.execute(
        sa.update(Installation)
        .where(Installation.installation_id == installation_id)
        .values(
            permissions=permissions,
            raw_payload=raw_payload,
            updated_at=sa.func.now(),
        )
    )
    await session.commit()
    log.info("installation.permissions_updated", installation_id=installation_id)

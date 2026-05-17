"""Phase 11 brain_metrics repo: cross-team aggregation queries for the
superadmin dashboard (BMO-10 + BMO-11).

Four async functions, each reads from Postgres (and get_storage_per_team
additionally hits Qdrant + MinIO).

All Postgres reads go through v_brain_events (migration 0018) so the
aggregate matches whatever per-row contract the universal monitor view
exposes. Soft-deleted rows are excluded (deleted_at IS NULL) to match
the dashboard live-data semantics.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)


async def get_overview_per_team(session: AsyncSession) -> list[dict[str, Any]]:
    """Return per-team counts grouped by entity_type and truth_level."""
    sql = """
        SELECT
          t.slug                      AS team_slug,
          t.id                        AS team_id,
          v.entity_type               AS entity_type,
          v.truth_level               AS truth_level,
          COUNT(v.entity_id)          AS cnt
        FROM teams t
        LEFT JOIN v_brain_events v
          ON v.team_id = t.id AND v.deleted_at IS NULL
        GROUP BY t.slug, t.id, v.entity_type, v.truth_level
        ORDER BY t.slug, v.entity_type, v.truth_level
    """
    rows = (await session.execute(sa.text(sql))).mappings().all()

    by_team: dict[str, dict[str, Any]] = {}
    for r in rows:
        slug = r["team_slug"]
        team_id = r["team_id"]
        bucket = by_team.setdefault(
            slug, {"team_slug": slug, "team_id": team_id, "counts": {}}
        )
        et = r["entity_type"]
        tl = r["truth_level"]
        cnt = r["cnt"]
        if et is None or tl is None:
            continue
        bucket["counts"].setdefault(et, {})[tl] = int(cnt)

    return list(by_team.values())


async def get_storage_per_team(
    session: AsyncSession,
    qdrant_client,
    minio_client,
) -> list[dict[str, Any]]:
    """Return per-team Postgres row counts + Qdrant point count + MinIO bytes.

    Shape:
        [{team_slug, team_id, pg_rows: {table: int},
          qdrant_points: int | None, minio_bytes: int | None}, ...].

    Fail-soft semantics:
      - qdrant_client is None OR Qdrant call raises -> qdrant_points=None
      - minio_client is None OR MinIO call raises -> minio_bytes=None

    The UI renders None as N/A so missing infra does not blank the table.
    """
    teams_rows = (await session.execute(
        sa.text("SELECT id, slug FROM teams ORDER BY slug")
    )).mappings().all()

    out: list[dict[str, Any]] = []
    for trow in teams_rows:
        slug = trow["slug"]
        team_id = trow["id"]

        # Postgres rows: one UNION ALL query across all 6 brain tables.
        pg_sql = """
            SELECT 'memory_items' AS table_name, COUNT(*) AS cnt
              FROM memory_items WHERE team_scope = :slug AND deleted_at IS NULL
            UNION ALL SELECT 'conversations',  COUNT(*)
              FROM conversations WHERE team_scope = :slug AND deleted_at IS NULL
            UNION ALL SELECT 'messages',       COUNT(*)
              FROM messages       WHERE team_scope = :slug AND deleted_at IS NULL
            UNION ALL SELECT 'team_messages',  COUNT(*)
              FROM team_messages  WHERE team_id    = :team_id AND deleted_at IS NULL
            UNION ALL SELECT 'tasks',          COUNT(*)
              FROM tasks          WHERE team_scope = :slug AND deleted_at IS NULL
            UNION ALL SELECT 'contacts',       COUNT(*)
              FROM contacts       WHERE team_scope = :slug AND deleted_at IS NULL
        """
        pg_rows_result = (await session.execute(
            sa.text(pg_sql), {"slug": slug, "team_id": str(team_id)}
        )).mappings().all()
        pg_rows = {r["table_name"]: int(r["cnt"]) for r in pg_rows_result}

        # Qdrant points: fail-soft. Approximate count avoids full scan.
        qdrant_points: int | None = None
        if qdrant_client is not None:
            try:
                from qdrant_client.http.models import (
                    FieldCondition,
                    Filter,
                    MatchValue,
                    Range,
                )

                qresp = await qdrant_client.count(
                    collection_name=settings.QDRANT_COLLECTION,
                    count_filter=Filter(
                        must=[
                            FieldCondition(
                                key="team_scope", match=MatchValue(value=slug)
                            )
                        ],
                        must_not=[
                            FieldCondition(
                                key="deleted_at_ts", range=Range(gt=0.0)
                            )
                        ],
                    ),
                    exact=False,
                )
                qdrant_points = int(qresp.count)
            except Exception as exc:
                logger.warning(
                    "qdrant_count_failed team=%s error=%s", slug, exc
                )
                qdrant_points = None

        # MinIO bytes: fail-soft. team/<slug>/ is the convention.
        minio_bytes: int | None = None
        if minio_client is not None:
            try:
                total = 0
                paginator = minio_client.get_paginator("list_objects_v2")
                for page in paginator.paginate(
                    Bucket=settings.MINIO_BUCKET, Prefix=f"team/{slug}/"
                ):
                    for obj in page.get("Contents", []) or []:
                        total += int(obj["Size"])
                minio_bytes = total
            except Exception as exc:
                logger.warning(
                    "minio_size_failed team=%s error=%s", slug, exc
                )
                minio_bytes = None

        out.append(
            {
                "team_slug": slug,
                "team_id": team_id,
                "pg_rows": pg_rows,
                "qdrant_points": qdrant_points,
                "minio_bytes": minio_bytes,
            }
        )

    return out


async def get_activity_per_team(
    session: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """Return per-team events/day for the last `days` days, zero-filled.

    Shape: [{team_slug, team_id, daily: [{date, events}, ...]}, ...].

    Invariant: daily is ALWAYS exactly `days` entries, oldest first,
    with missing days zero-filled. The UI in 11-11 renders a sparkline
    that depends on this guarantee.
    """
    sql = """
        SELECT
          t.slug                                                AS team_slug,
          t.id                                                  AS team_id,
          date_trunc('day', v.created_at AT TIME ZONE 'UTC')::date AS day,
          COUNT(v.entity_id)                                    AS events
        FROM teams t
        LEFT JOIN v_brain_events v
          ON v.team_id = t.id
         AND v.deleted_at IS NULL
         AND v.created_at >= now() - (:days || ' days')::interval
        GROUP BY t.slug, t.id, day
        ORDER BY t.slug, day NULLS LAST
    """
    rows = (await session.execute(
        sa.text(sql), {"days": str(days)}
    )).mappings().all()

    by_team: dict[str, dict[str, Any]] = {}
    counts_per_team: dict[str, dict[date, int]] = defaultdict(dict)
    for r in rows:
        slug = r["team_slug"]
        by_team.setdefault(
            slug,
            {"team_slug": slug, "team_id": r["team_id"], "daily": []},
        )
        d = r["day"]
        if d is None:
            continue
        counts_per_team[slug][d] = int(r["events"])

    today = datetime.now(tz=timezone.utc).date()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    for slug, bucket in by_team.items():
        counts = counts_per_team.get(slug, {})
        bucket["daily"] = [
            {"date": d, "events": counts.get(d, 0)} for d in window
        ]

    return list(by_team.values())


async def get_sources_per_team(
    session: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """Return per-team source-label -> count for the last `days` days.

    Shape: [{team_slug, team_id, sources: {source_label: count}}, ...].

    No truncation: every source seen in the window is returned. The UI
    in 11-11 picks top-N for display. COALESCE(source, '(none)') surfaces
    NULL-source rows under a stable label.
    """
    sql = """
        SELECT
          t.slug                       AS team_slug,
          t.id                         AS team_id,
          COALESCE(v.source, '(none)') AS source,
          COUNT(v.entity_id)           AS events
        FROM teams t
        LEFT JOIN v_brain_events v
          ON v.team_id = t.id
         AND v.deleted_at IS NULL
         AND v.created_at >= now() - (:days || ' days')::interval
        GROUP BY t.slug, t.id, source
        ORDER BY t.slug, events DESC NULLS LAST
    """
    rows = (await session.execute(
        sa.text(sql), {"days": str(days)}
    )).mappings().all()

    by_team: dict[str, dict[str, Any]] = {}
    for r in rows:
        slug = r["team_slug"]
        bucket = by_team.setdefault(
            slug,
            {"team_slug": slug, "team_id": r["team_id"], "sources": {}},
        )
        events = r["events"]
        if events is None or int(events) == 0:
            continue
        bucket["sources"][r["source"]] = int(events)

    return list(by_team.values())

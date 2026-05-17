"""Pydantic response schemas for /v1/admin/brain/* endpoints.

Phase 11 plan 11-10. The four aggregate response models mirror the
nested dicts returned by app/repos/brain_metrics.py. The drill-down
/v1/admin/brain/events endpoint reuses BrainEventListOut from
app/schemas/brain.py (the 11-04 schema) so superadmin and team-scoped
reads serialize identically.

`date` is exported as Python's `datetime.date` (Pydantic v2 serializes
to ISO-8601 YYYY-MM-DD by default). The sparkline component in 11-11
parses that shape directly.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TeamOverviewOut(BaseModel):
    """Counts x entity_type x truth_level per team."""

    model_config = ConfigDict(from_attributes=True)

    team_slug: str
    team_id: UUID
    # entity_type -> truth_level -> int
    counts: dict[str, dict[str, int]]


class TeamStorageOut(BaseModel):
    """Per-team Postgres rows + Qdrant points + MinIO bytes."""

    model_config = ConfigDict(from_attributes=True)

    team_slug: str
    team_id: UUID
    # table_name -> int (one row per brain-tracked table)
    pg_rows: dict[str, int]
    # None when Qdrant client unavailable or call raises (UI -> "N/A")
    qdrant_points: int | None = None
    # None when MinIO client unavailable or call raises (UI -> "N/A")
    minio_bytes: int | None = None


class DailyEventCount(BaseModel):
    """One entry in TeamActivityOut.daily — date + event count."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    events: int


class TeamActivityOut(BaseModel):
    """Per-team events/day series; daily is always exactly N entries."""

    model_config = ConfigDict(from_attributes=True)

    team_slug: str
    team_id: UUID
    # ALWAYS exactly N days, oldest first, zero-filled (see
    # repos/brain_metrics.get_activity_per_team docstring).
    daily: list[DailyEventCount]


class TeamSourcesOut(BaseModel):
    """Per-team source label -> count breakdown for the window."""

    model_config = ConfigDict(from_attributes=True)

    team_slug: str
    team_id: UUID
    # source_label -> int (all sources seen in the window; UI picks top-N)
    sources: dict[str, int]

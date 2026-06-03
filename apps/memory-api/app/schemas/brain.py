"""Pydantic schemas for /v1/brain/* endpoints (Phase 11 BMO-02..06).

`BrainEventOut` mirrors the column set surfaced by the `v_brain_events`
SQL view introduced in migration 0018. `BrainEventListOut` is the envelope
returned by the paginated GET endpoint; `next_cursor` is `None` when the
caller has reached the end of the feed.

`TruthLevelPatchBody` is exported from this module even though plan 11-04
only ships the GET endpoint — keeping the request body schema co-located
with the response schema avoids a circular import when plan 11-05 wires
up PATCH/DELETE/restore. The regex mirrors the DB-side CHECK constraint
added by migration 0017 (`truth_level IN ('EPHEMERAL','WORKING',
'VALIDATED','CANONICAL','PUBLIC')`) so a bad value is rejected at the API
boundary before it ever hits Postgres.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field




class BrainEventOut(BaseModel):
    """One row from `v_brain_events` projected over the wire.

    Field types match the view's column types exactly. `created_by`,
    `deleted_at`, `deleted_by`, `preview`, and `source` are NULL-able
    because the view exposes them as NULL for entity types that have no
    author column (memory_items, messages, contacts), no soft-delete
    timestamp yet, or no source label.
    """

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    entity_type: str
    entity_id: UUID
    team_id: UUID
    team_scope: str
    created_at: datetime
    created_by: UUID | None
    truth_level: str
    deleted_at: datetime | None
    deleted_by: UUID | None
    preview: str | None
    source: str | None
    # BL-003 slice 2: present only for memory_item rows that carry a media blob.
    # The raw MinIO key is NEVER forwarded — brain.py replaces it with a signed
    # token URL before serialisation.
    media: dict | None = None


class BrainEventListOut(BaseModel):
    """Envelope for the paginated GET /v1/brain/events response.

    `next_cursor` is the opaque base64-encoded cursor the client passes
    back in the next request to fetch the following page. It is `None`
    when fewer than `limit + 1` rows came back, signalling end-of-feed.
    """

    items: list[BrainEventOut]
    next_cursor: str | None = None


class TruthLevelPatchBody(BaseModel):
    """Body schema for `PATCH /v1/brain/events/{entity_type}/{entity_id}`.

    Defined here (instead of inside the 11-05 route module) so that the
    11-05 router can import it from a stable location without creating a
    circular reference between the route file and its peer schemas
    module. The regex is the API-layer mirror of the DB CHECK constraint
    — defence in depth against a regression that drops the constraint.
    """

    model_config = ConfigDict(extra="forbid")

    truth_level: str = Field(
        ...,
        pattern=r"^(EPHEMERAL|WORKING|VALIDATED|CANONICAL|PUBLIC)$",
        description="One of the 5 canonical truth levels from the tagging contract.",
    )


class BrainIngestRequest(BaseModel):
    """POST /v1/brain/ingest body — used by librechat-bridge and openwebui-pipeline.

    The server builds the MemoryItem (truth_level=WORKING per Phase 13 D3) and
    runs the Haiku relevance filter before upserting. Fire-and-forget on success.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=32_000)
    source: str = Field(..., min_length=1, max_length=128)
    metadata: dict = Field(default_factory=dict)
    project_scope: str | None = Field(default=None, max_length=64)

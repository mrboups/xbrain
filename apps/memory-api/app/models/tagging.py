"""The tagging contract — 7 mandatory fields on every message Phase 1.

This is xbrain's differentiator: every datum carries enough metadata to be
isolated by team, validated, and graduated through truth levels.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

SOURCE_PATTERN = r"^[a-z][a-z0-9_-]*:[a-z0-9._-]+$"


class Visibility(str, Enum):
    PRIVATE = "private"
    TEAM = "team"
    ORG = "org"
    PUBLIC = "public"


class TruthLevel(str, Enum):
    EPHEMERAL = "EPHEMERAL"
    WORKING = "WORKING"
    VALIDATED = "VALIDATED"
    CANONICAL = "CANONICAL"
    PUBLIC = "PUBLIC"


class ValidationStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    NA = "n/a"


class TaggingContract(BaseModel):
    """Mandatory tagging on every Phase 1 message.

    Pydantic v2 with ``extra="forbid"`` rejects unknown fields with HTTP 422 —
    this is the contract enforcement gate that satisfies success criterion 2.
    """

    model_config = ConfigDict(extra="forbid")

    team_scope: str = Field(..., min_length=1, max_length=64)
    project_scope: str | None = Field(default=None, max_length=64)
    visibility: Visibility
    confidence: float = Field(..., ge=0.0, le=1.0)
    truth_level: TruthLevel = TruthLevel.EPHEMERAL
    source: str = Field(..., pattern=SOURCE_PATTERN, max_length=128)
    validation_status: ValidationStatus = ValidationStatus.PENDING

"""Shared types for memory abstraction."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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

    @classmethod
    def order(cls) -> list["TruthLevel"]:
        return [cls.EPHEMERAL, cls.WORKING, cls.VALIDATED, cls.CANONICAL, cls.PUBLIC]

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, TruthLevel):
            return NotImplemented
        order = TruthLevel.order()
        return order.index(self) >= order.index(other)

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, TruthLevel):
            return NotImplemented
        order = TruthLevel.order()
        return order.index(self) > order.index(other)

    def __le__(self, other: object) -> bool:
        if not isinstance(other, TruthLevel):
            return NotImplemented
        order = TruthLevel.order()
        return order.index(self) <= order.index(other)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TruthLevel):
            return NotImplemented
        order = TruthLevel.order()
        return order.index(self) < order.index(other)


class ValidationStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    NA = "n/a"


class MemoryItem(BaseModel):
    """A single fact / memory entry, backend-agnostic."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Backend-assigned ID (UUID string or backend-specific)")
    team_scope: str = Field(..., min_length=1, max_length=64)
    project_scope: str | None = Field(default=None, max_length=64)
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = Field(
        default=None,
        description="Vector embedding (provider may not return; some backends manage internally)",
    )
    visibility: Visibility = Visibility.TEAM
    truth_level: TruthLevel = TruthLevel.EPHEMERAL
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(..., min_length=1, max_length=128)
    validation_status: ValidationStatus = ValidationStatus.PENDING
    created_at: datetime
    updated_at: datetime


class SearchHit(BaseModel):
    """One result from a memory search."""

    model_config = ConfigDict(extra="forbid")

    item: MemoryItem
    score: float = Field(..., ge=0.0, le=1.0, description="Relevance 0..1; 1=exact")

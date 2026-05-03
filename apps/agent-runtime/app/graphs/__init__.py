"""Graphs package — importing it triggers @register decorators for all agents."""

from app.graphs import (  # noqa: F401 — side-effect imports for @register
    echo_with_hitl,
    ingestion,
    second_opinion,
)

__all__ = ["echo_with_hitl", "ingestion", "second_opinion"]

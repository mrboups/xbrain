"""Graphs package — importing it triggers @register decorators for all agents."""

from app.graphs import echo_with_hitl, ingestion  # noqa: F401 — side-effect imports

__all__ = ["echo_with_hitl", "ingestion"]

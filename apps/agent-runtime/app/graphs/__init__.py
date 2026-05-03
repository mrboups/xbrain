"""Graphs package — importing it triggers @register decorators for all agents."""

from app.graphs import echo_with_hitl  # noqa: F401 — side-effect import for registration

__all__ = ["echo_with_hitl"]

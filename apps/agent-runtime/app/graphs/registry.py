"""Central agent registry.

Each agent module decorates its factory with `@register("agent-name")` so the
runtime exposes it under `/v1/agents/agent-name/*`. Plans 02-07 (ingestion)
and 02-08 (second-opinion) plug in here.

The factory contract is:
    def factory(checkpointer) -> CompiledStateGraph
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# graph_name → factory(checkpointer) → CompiledStateGraph
GRAPH_REGISTRY: dict[str, Callable[[Any], Any]] = {}


def register(name: str):
    """Decorator: @register('my-agent') — registers the decorated factory in GRAPH_REGISTRY."""

    def deco(factory: Callable[[Any], Any]) -> Callable[[Any], Any]:
        if name in GRAPH_REGISTRY:
            raise ValueError(f"agent '{name}' already registered")
        GRAPH_REGISTRY[name] = factory
        return factory

    return deco

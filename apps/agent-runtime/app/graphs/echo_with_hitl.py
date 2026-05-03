"""Demo agent showing the Human-in-the-Loop pattern.

Flow:
  draft  →  await_approval  (interrupt_before)  →  finalize

Caller starts a thread with `initial_state={"user_text": "..."}`. The graph runs
through `draft_node`, then PAUSES before `await_approval` (interrupt_before).
Caller fetches state, presents it to a human, then calls /resume with
`state_update={"approved": true|false}`. Graph proceeds through `finalize_node`
which materializes either `state["draft"]` or a rejection sentinel.

This agent ships in Phase 2 to lock the HITL contract — real agents (02-07/08)
follow the same shape with substantive nodes.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.graphs.registry import register


class EchoState(TypedDict, total=False):
    user_text: str
    draft: str
    approved: bool | None
    final: str


def draft_node(state: EchoState) -> EchoState:
    return {**state, "draft": f"Echo: {state.get('user_text', '')}"}


def await_approval_node(state: EchoState) -> EchoState:
    """No-op — exists so we can interrupt_before it.

    On resume the caller may have set `approved` via /resume's state_update.
    """
    return state


def finalize_node(state: EchoState) -> EchoState:
    if state.get("approved"):
        return {**state, "final": state.get("draft", "")}
    return {**state, "final": "(rejected by human)"}


@register("echo-with-hitl")
def make_graph(checkpointer):
    g = StateGraph(EchoState)
    g.add_node("draft", draft_node)
    g.add_node("await_approval", await_approval_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "draft")
    g.add_edge("draft", "await_approval")
    g.add_edge("await_approval", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer, interrupt_before=["await_approval"])

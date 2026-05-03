"""/v1/agents/* — run, inspect, resume LangGraph agent threads.

A "thread" is the LangGraph term for a stateful agent execution scoped by
`config.configurable.thread_id`. We propagate `team_scope` through configurable
too so agents that talk to memory-api can attach it as a header.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.checkpointer import get_checkpointer
from app.deps import get_current_principal, get_team_scope
from app.graphs.registry import GRAPH_REGISTRY

router = APIRouter()


def _serialize_state(state) -> dict[str, Any]:
    """LangGraph StateSnapshot has .values + .next + .config — flatten for JSON."""
    if state is None:
        return {}
    return dict(getattr(state, "values", state) or {})


def _interrupt_point(state) -> list[str]:
    nxt = getattr(state, "next", None)
    return list(nxt) if nxt else []


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_state: dict[str, Any]
    thread_id: str | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    agent: str
    state: dict[str, Any]
    interrupted_at: list[str]
    events_count: int


@router.post("/agents/{agent_name}/run", response_model=RunOut)
async def run_agent(
    agent_name: str,
    body: RunBody,
    _principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
):
    if agent_name not in GRAPH_REGISTRY:
        raise HTTPException(404, f"unknown agent {agent_name}")
    cp = await get_checkpointer()
    graph = GRAPH_REGISTRY[agent_name](cp)
    thread_id = body.thread_id or str(uuid4())
    config = {"configurable": {"thread_id": thread_id, "team_scope": team_scope}}

    events_count = 0
    async for _ in graph.astream(body.initial_state, config):
        events_count += 1

    state = await graph.aget_state(config)
    return RunOut(
        thread_id=thread_id,
        agent=agent_name,
        state=_serialize_state(state),
        interrupted_at=_interrupt_point(state),
        events_count=events_count,
    )


class StateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    agent: str
    state: dict[str, Any]
    next: list[str]


@router.get("/agents/{thread_id}/state", response_model=StateOut)
async def get_state(
    thread_id: str,
    agent_name: str = Query(..., min_length=1, description="Required — disambiguates the graph"),
    _principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
):
    if agent_name not in GRAPH_REGISTRY:
        raise HTTPException(404, f"unknown agent {agent_name}")
    cp = await get_checkpointer()
    graph = GRAPH_REGISTRY[agent_name](cp)
    config = {"configurable": {"thread_id": thread_id, "team_scope": team_scope}}
    state = await graph.aget_state(config)
    if state is None or not getattr(state, "values", None):
        raise HTTPException(404, f"thread {thread_id} not found")
    return StateOut(
        thread_id=thread_id,
        agent=agent_name,
        state=_serialize_state(state),
        next=_interrupt_point(state),
    )


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str
    state_update: dict[str, Any] = {}


class ResumeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    state: dict[str, Any]
    events_count: int
    interrupted_at: list[str]


@router.post("/agents/{thread_id}/resume", response_model=ResumeOut)
async def resume_agent(
    thread_id: str,
    body: ResumeBody,
    _principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
):
    if body.agent_name not in GRAPH_REGISTRY:
        raise HTTPException(404, f"unknown agent {body.agent_name}")
    cp = await get_checkpointer()
    graph = GRAPH_REGISTRY[body.agent_name](cp)
    config = {"configurable": {"thread_id": thread_id, "team_scope": team_scope}}

    if body.state_update:
        await graph.aupdate_state(config, body.state_update)

    events_count = 0
    async for _ in graph.astream(None, config):
        events_count += 1

    state = await graph.aget_state(config)
    return ResumeOut(
        thread_id=thread_id,
        state=_serialize_state(state),
        events_count=events_count,
        interrupted_at=_interrupt_point(state),
    )

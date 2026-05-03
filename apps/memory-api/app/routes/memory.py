"""/v1/memory/* — backend-agnostic memory endpoints (Phase 2)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryItem, MemoryProvider, SearchHit, TruthLevel

from app.audit import write_audit
from app.deps import (
    get_current_principal,
    get_memory_provider,
    get_session,
    get_team_scope,
)

router = APIRouter()


class UpsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: MemoryItem


class UpsertOut(BaseModel):
    id: str


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: dict[str, Any]


@router.post("/memory/upsert", status_code=201, response_model=UpsertOut)
async def upsert_item(
    body: UpsertBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    if body.item.team_scope != team_scope:
        raise HTTPException(400, "item.team_scope must match X-Team-Scope header")
    item_id = await provider.upsert(body.item)
    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="memory.upsert",
        target_id=item_id,
        payload={"source": body.item.source, "truth_level": body.item.truth_level.value},
    )
    await session.commit()
    return UpsertOut(id=item_id)


@router.get("/memory/search", response_model=list[SearchHit])
async def search_memory(
    q: str = Query(..., min_length=1, max_length=500),
    project_scope: str | None = Query(default=None, max_length=64),
    truth_level_min: TruthLevel | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    return await provider.search(
        q,
        team_scope=team_scope,
        project_scope=project_scope,
        truth_level_min=truth_level_min,
        limit=limit,
    )


@router.get("/memory/{item_id}", response_model=MemoryItem)
async def get_item(
    item_id: str,
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    item = await provider.get(item_id, team_scope=team_scope)
    if item is None:
        raise HTTPException(404, "memory item not found in this team")
    return item


@router.patch("/memory/{item_id}", response_model=MemoryItem)
async def update_item(
    item_id: str,
    body: UpdateBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    # Phase 2 invariant: truth_level CANNOT be patched directly here — must go through promotions
    if "truth_level" in body.patch:
        raise HTTPException(
            405,
            "truth_level can only be changed via /v1/promotions workflow (4-eyes principle)",
        )
    try:
        updated = await provider.update(item_id, team_scope=team_scope, patch=body.patch)
    except KeyError:
        raise HTTPException(404, "memory item not found in this team")
    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="memory.update",
        target_id=item_id,
        payload={"patched_fields": list(body.patch.keys())},
    )
    await session.commit()
    return updated


@router.delete("/memory/{item_id}", status_code=204)
async def delete_item(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    await provider.delete(item_id, team_scope=team_scope)
    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="memory.delete",
        target_id=item_id,
        payload={},
    )
    await session.commit()


@router.get("/memory/{item_id}/history", response_model=list[MemoryItem])
async def get_history(
    item_id: str,
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    history = await provider.history(item_id, team_scope=team_scope)
    if not history:
        raise HTTPException(404, "memory item not found in this team")
    return history

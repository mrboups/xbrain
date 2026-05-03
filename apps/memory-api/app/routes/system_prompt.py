"""/v1/system-prompt — retrieve a team's CANONICAL facts as a markdown addendum.

Called by librechat-bridge on new conversations to inject team knowledge as a
system message. Phase 3 will add caching + graph-based selection.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from xbrain_memory import MemoryProvider, TruthLevel

from app.deps import get_current_principal, get_memory_provider, get_team_scope
from app.services.rag_enrichment import (
    DEFAULT_TOP_K,
    build_system_addendum,
    count_facts,
)

router = APIRouter()


class SystemPromptOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_addendum: str
    fact_count: int


@router.get("/system-prompt", response_model=SystemPromptOut)
async def get_system_prompt(
    query: str = Query(..., min_length=1, max_length=500),
    project_scope: str | None = Query(default=None, max_length=64),
    top_k: int = Query(default=DEFAULT_TOP_K, ge=1, le=20),
    min_level: TruthLevel = Query(default=TruthLevel.CANONICAL),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
    _principal: dict = Depends(get_current_principal),
):
    addendum = await build_system_addendum(
        provider,
        query=query,
        team_scope=team_scope,
        project_scope=project_scope,
        top_k=top_k,
        min_level=min_level,
    )
    return SystemPromptOut(system_addendum=addendum, fact_count=count_facts(addendum))

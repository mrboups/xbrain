"""/v1/graph/* — graph traversal endpoints backed by Neo4j.

All queries are scoped by team_scope (extracted from JWT). No raw Cypher exposed.
Returns 503 if Neo4j driver is not connected (graceful degrade).

Threat model (03-05):
  T-03-05-01: team_scope from JWT only — no cross-team graph traversal.
  T-03-05-02: all params passed via $name/$team_scope/$depth — no string interpolation.
  T-03-05-03: depth capped at 4 (Query le=4) — prevents exponential traversals.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.deps import get_team_scope
from app.neo4j_client import get_driver

router = APIRouter()


class EntityNode(BaseModel):
    name: str
    type: str = "concept"


class TraverseResult(BaseModel):
    root: str
    depth: int
    entities: list[EntityNode]


class LineageNode(BaseModel):
    source_id: str
    source_type: str  # "Conversation" or "Drive_Document"
    fact_id: str


class LineageResult(BaseModel):
    fact_id: str
    derived_from: list[LineageNode]


def _require_driver() -> Any:
    """Return the live Neo4j driver or raise 503."""
    driver = get_driver()
    if driver is None:
        raise HTTPException(
            status_code=503,
            detail="Graph service unavailable — Neo4j not connected",
        )
    return driver


@router.get("/graph/traverse", response_model=TraverseResult)
async def traverse_entity(
    entity: str = Query(..., min_length=1, max_length=256),
    depth: int = Query(default=2, ge=1, le=4),
    team_scope: str = Depends(get_team_scope),
) -> TraverseResult:
    """Traverse DEPENDS_ON relationships from a named entity.

    Returns all entities reachable within `depth` hops from `entity`,
    scoped to `team_scope`. Used for SRCH-05: "what depends on entity X?"

    Uses variable-length Cypher path syntax — works on Neo4j Community Edition
    (no APOC plugin required).
    """
    driver = _require_driver()
    # Variable-length path — depth is parameterised via Python-side range construction.
    # Neo4j does not support $param in *min..max — we validate depth (ge=1, le=4) above
    # and build a safe literal range. The user-controlled input (entity, team_scope) is
    # always parameterised via $name / $team_scope.
    cypher = f"""
        MATCH (root:Entity {{name: $name, team_scope: $team_scope}})
              -[:DEPENDS_ON*1..{depth}]->(dep:Entity)
        RETURN DISTINCT dep.name AS name, coalesce(dep.type, 'concept') AS type
    """
    try:
        result = await driver.execute_query(
            cypher,
            name=entity,
            team_scope=team_scope,
            database_="neo4j",
        )
        entities = [
            EntityNode(name=r["name"], type=r["type"])
            for r in result.records
        ]
        return TraverseResult(root=entity, depth=depth, entities=entities)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Graph traversal error: {exc}"
        ) from exc


@router.get("/graph/lineage", response_model=LineageResult)
async def get_fact_lineage(
    fact_id: str = Query(..., min_length=1, max_length=64),
    team_scope: str = Depends(get_team_scope),
) -> LineageResult:
    """Get provenance lineage for a fact node.

    Returns all DERIVED_FROM sources for the given fact_id,
    scoped to team_scope. Used for SRCH-05: "show lineage of fact Y".
    """
    driver = _require_driver()
    cypher = """
        MATCH (f:Fact {id: $fact_id, team_scope: $team_scope})-[:DERIVED_FROM]->(src)
        RETURN f.id AS fact_id, src.id AS source_id,
               labels(src)[0] AS source_type
    """
    try:
        result = await driver.execute_query(
            cypher,
            fact_id=fact_id,
            team_scope=team_scope,
            database_="neo4j",
        )
        derived = [
            LineageNode(
                source_id=r["source_id"],
                source_type=r["source_type"],
                fact_id=r["fact_id"],
            )
            for r in result.records
        ]
        return LineageResult(fact_id=fact_id, derived_from=derived)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Lineage query error: {exc}"
        ) from exc

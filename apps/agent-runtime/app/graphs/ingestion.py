"""Ingestion agent — URL/PDF → extract atomic facts → HITL approve → write to memory.

Flow:
  load  →  extract  →  await_review  (interrupt_before)  →  write

Initial state:
    {source_type: "url"|"pdf", source_url?, pdf_b64?, team_scope, sub}
After /run pauses at await_review with extracted_facts populated.
After /resume with state_update={"approved_indexes": [0, 2, 5]} the listed
facts are written via memory-api with truth_level=suggested_truth_level
(default WORKING) and validation_status='pending'. Promotion to VALIDATED+
goes through the 02-04 workflow — extraction NEVER produces CANONICAL.
"""

from __future__ import annotations

import base64
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.graphs.registry import register
from app.memory_client import MemoryApiClient
from app.tools.document_loader import load_pdf_bytes, load_url
from app.tools.extract_facts import extract_facts

# Process-singleton — agent_runtime hosts agents in-process, one client suffices
_mem = MemoryApiClient()


class IngestionState(TypedDict, total=False):
    # Inputs (caller supplies)
    source_type: str           # "url" | "pdf"
    source_url: str | None
    pdf_b64: str | None        # base64-encoded PDF bytes
    team_scope: str
    sub: str                   # OIDC sub of the user who proposed the ingestion

    # Pipeline outputs
    document_text: str
    extracted_facts: list[dict[str, Any]]
    approved_indexes: list[int] | None  # set by HITL resume; None ⇒ approve-all
    written_ids: list[str]
    skipped_count: int


async def load_node(state: IngestionState) -> IngestionState:
    src = state.get("source_type")
    if src == "url":
        url = state.get("source_url")
        if not url:
            raise ValueError("source_url required for source_type='url'")
        text = await load_url(url)
    elif src == "pdf":
        b64 = state.get("pdf_b64")
        if not b64:
            raise ValueError("pdf_b64 required for source_type='pdf'")
        text = await load_pdf_bytes(base64.b64decode(b64))
    else:
        raise ValueError(f"source_type must be url|pdf, got {src!r}")
    return {**state, "document_text": text}


async def extract_node(state: IngestionState) -> IngestionState:
    source_label = state.get("source_url") or "pdf-upload"
    facts = await extract_facts(state.get("document_text", ""), source_label)
    return {**state, "extracted_facts": facts}


async def await_review_node(state: IngestionState) -> IngestionState:
    """No-op — interrupt_before this node. /resume sets approved_indexes."""
    return state


async def write_node(state: IngestionState) -> IngestionState:
    facts = state.get("extracted_facts") or []
    approved = state.get("approved_indexes")
    if approved is None:
        # No explicit selection on resume → approve all (back-compat, dev convenience)
        approved = list(range(len(facts)))

    written: list[str] = []
    skipped = 0
    team = state.get("team_scope", "")
    sub = state.get("sub", "agent:ingestion")
    source_url = state.get("source_url")

    for idx in approved:
        if idx < 0 or idx >= len(facts):
            skipped += 1
            continue
        f = facts[idx]
        try:
            result = await _mem.upsert_fact(
                agent_sub=sub,
                team_scope=team,
                content=f["content"],
                truth_level=f.get("suggested_truth_level", "WORKING"),
                confidence=f.get("confidence", 0.7),
                source=f.get("source", "agent:ingestion-v1"),
                metadata={
                    **(f.get("metadata") or {}),
                    "source_url": source_url,
                    "ingestion_origin": "agent:ingestion-v1",
                },
            )
            written.append(result.get("id", ""))
        except Exception:  # noqa: BLE001 — propagate via skipped count, don't crash the graph
            skipped += 1

    return {**state, "written_ids": written, "skipped_count": skipped}


@register("ingestion")
def make_graph(checkpointer):
    g = StateGraph(IngestionState)
    g.add_node("load", load_node)
    g.add_node("extract", extract_node)
    g.add_node("await_review", await_review_node)
    g.add_node("write", write_node)
    g.add_edge(START, "load")
    g.add_edge("load", "extract")
    g.add_edge("extract", "await_review")
    g.add_edge("await_review", "write")
    g.add_edge("write", END)
    return g.compile(checkpointer=checkpointer, interrupt_before=["await_review"])

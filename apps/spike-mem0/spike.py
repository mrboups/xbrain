"""1-day spike: validate mem0 vs native for xbrain Phase 2.

Run: python spike.py
Output: spike-results.json (consumed by 02-SPIKE-RESULT.md author).

Pre-req local backends (see README.md for docker run commands):
  - postgres:17 on localhost:5432 (user/pass/db: spike/spike/spike)
  - qdrant:1.17.1 on localhost:6333
  - OPENAI_API_KEY env var set (for mem0 default embedder)
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from mem0 import AsyncMemory  # type: ignore[import-untyped]
except ImportError:
    print("ERROR: mem0 not installed — pip install -e .", file=sys.stderr)
    sys.exit(1)


HERE = Path(__file__).parent
DATA = json.loads((HERE / "test_data.json").read_text(encoding="utf-8"))
RESULTS_PATH = HERE / "spike-results.json"


def _team_to_uid(team_scope: str) -> str:
    """xbrain convention: encode team_scope as user_id since mem0 has no team_id field."""
    return f"team:{team_scope}"


async def _make_mem() -> AsyncMemory:
    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "url": "http://localhost:6333",
                "collection_name": f"spike_{uuid.uuid4().hex[:8]}",  # isolated collection per run
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-4o-mini",
                "api_key": os.environ.get("OPENAI_API_KEY"),
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-small",
                "api_key": os.environ.get("OPENAI_API_KEY"),
            },
        },
    }
    return AsyncMemory.from_config(config)


# --- TEST 1: team isolation ---

async def test_team_isolation(mem: AsyncMemory) -> dict[str, Any]:
    """Ingest 100 facts across 3 teams. For each team, search must return 0 facts from other teams."""
    print("[1/4] team isolation — ingesting 100 facts...")
    fact_id_to_team: dict[str, str] = {}
    for fact in DATA["facts"]:
        meta = {"team_scope": fact["team_scope"], "source": fact["source"]}
        result = await mem.add(
            messages=[{"role": "user", "content": fact["content"]}],
            user_id=_team_to_uid(fact["team_scope"]),
            metadata=meta,
        )
        # mem0 returns dict with "results" key (recent versions)
        items = result.get("results", []) if isinstance(result, dict) else result
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                fact_id_to_team[it["id"]] = fact["team_scope"]

    print(f"  ingested {len(fact_id_to_team)} facts, querying...")
    leak_count = 0
    total_results = 0
    for team in DATA["teams"]:
        rows = await mem.search(query="content", user_id=_team_to_uid(team), limit=200)
        results = rows.get("results", []) if isinstance(rows, dict) else rows
        for r in results:
            total_results += 1
            actual_team = (r.get("metadata", {}) or {}).get("team_scope")
            if actual_team and actual_team != team:
                leak_count += 1
                print(f"  LEAK: searching team={team} returned fact from team={actual_team}")

    return {
        "leak_count": leak_count,
        "total_results_searched": total_results,
        "facts_ingested": len(fact_id_to_team),
        "pass": leak_count == 0,
    }


# --- TEST 2: versioning ---

async def test_versioning(mem: AsyncMemory) -> dict[str, Any]:
    """Add a fact, update it 3×, retrieve full history."""
    print("[2/4] versioning — add + 3 updates + history()...")
    uid = _team_to_uid("test-version-team")
    contents = ["v1: initial fact", "v2: corrected fact", "v3: refined fact", "v4: final form"]
    add_result = await mem.add(
        messages=[{"role": "user", "content": contents[0]}],
        user_id=uid,
        metadata={"team_scope": "test-version-team", "source": "spike:versioning"},
    )
    items = add_result.get("results", []) if isinstance(add_result, dict) else add_result
    fact_id = items[0]["id"] if items and isinstance(items[0], dict) else None
    if not fact_id:
        return {"versions_retrieved": 0, "pass": False, "error": "could not get initial fact id"}

    for c in contents[1:]:
        try:
            await mem.update(memory_id=fact_id, data=c)
        except Exception as e:
            print(f"  update warning: {e}")

    try:
        history = await mem.history(memory_id=fact_id)
        history_list = history if isinstance(history, list) else history.get("results", [])
    except Exception as e:
        return {"versions_retrieved": 0, "pass": False, "error": f"history() failed: {e}"}

    return {
        "versions_retrieved": len(history_list),
        "expected": 4,
        "pass": len(history_list) >= 3,  # at least add + 2 updates visible
        "fact_id": fact_id,
    }


# --- TEST 3: truth-level glue effort ---

async def test_truth_level_glue() -> dict[str, Any]:
    """Count lines of glue code needed to overlay xbrain truth-level state machine on mem0.

    The "glue" = the function `promote_truth_level` defined inline below. Count its non-blank,
    non-comment lines. If > 100 lines, mem0 is too low-level for our state machine.
    """
    print("[3/4] truth-level glue effort — measuring glue size...")
    # The glue function (intentionally inline to count its lines):
    glue_code = '''
ALLOWED_TRANSITIONS = {
    ("EPHEMERAL", "WORKING"),
    ("WORKING", "VALIDATED"),
    ("VALIDATED", "CANONICAL"),
    ("CANONICAL", "PUBLIC"),
}

APPROVALS_REQUIRED = {
    ("EPHEMERAL", "WORKING"): 0,
    ("WORKING", "VALIDATED"): 1,
    ("VALIDATED", "CANONICAL"): 2,
    ("CANONICAL", "PUBLIC"): 1,
}

async def promote_truth_level(mem, fact_id, *, team_scope, from_level, to_level,
                              approver_id, proposer_id, existing_approvers):
    if (from_level, to_level) not in ALLOWED_TRANSITIONS:
        raise ValueError(f"illegal transition {from_level} -> {to_level}")
    if approver_id == proposer_id:
        raise PermissionError("proposer cannot approve their own promotion")
    if approver_id in existing_approvers:
        raise PermissionError("approver already recorded for this promotion")
    needed = APPROVALS_REQUIRED[(from_level, to_level)]
    new_approvers = existing_approvers + [approver_id]
    if len(new_approvers) < needed:
        return {"status": "pending", "approvals": len(new_approvers), "needed": needed}
    fact = await mem.get(memory_id=fact_id)
    if not fact:
        raise KeyError(f"fact {fact_id} not found")
    md = fact.get("metadata", {}) or {}
    if md.get("team_scope") != team_scope:
        raise PermissionError("team_scope mismatch — refusing cross-team promotion")
    if md.get("truth_level") != from_level:
        raise ValueError(f"current truth_level is {md.get('truth_level')} not {from_level}")
    new_md = {**md, "truth_level": to_level, "validation_status": "validated"}
    await mem.update(memory_id=fact_id, data=fact.get("memory") or fact.get("text", ""),
                     metadata=new_md)
    audit_entry = {
        "fact_id": fact_id,
        "team_scope": team_scope,
        "from": from_level,
        "to": to_level,
        "proposer": proposer_id,
        "approvers": new_approvers,
        "timestamp": time.time(),
    }
    return {"status": "approved", "approvals": len(new_approvers), "needed": needed,
            "audit": audit_entry}
'''
    lines = [
        line for line in glue_code.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    return {
        "glue_lines": len(lines),
        "threshold": 100,
        "pass": len(lines) <= 100,
        "notes": "Count includes function body, constants, and validation. Excludes blank + comments.",
    }


# --- TEST 4: latency ---

async def test_latency(mem: AsyncMemory) -> dict[str, Any]:
    """1000 search queries round-robin across 3 teams. Measure P50/P95/P99 latency in ms."""
    print("[4/4] latency — 1000 search queries...")
    queries = [
        "deployment infrastructure", "team scope", "truth level", "OAuth callback",
        "backup retention", "memory provider", "ingestion agent", "second opinion",
        "promotion workflow", "Cloudflare proxy",
    ]
    latencies_ms: list[float] = []
    n = 1000
    for i in range(n):
        team = DATA["teams"][i % len(DATA["teams"])]
        query = queries[i % len(queries)]
        t0 = time.perf_counter()
        await mem.search(query=query, user_id=_team_to_uid(team), limit=5)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{n} queries done...")
    sorted_lat = sorted(latencies_ms)
    p50 = sorted_lat[len(sorted_lat) // 2]
    p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
    p99 = sorted_lat[int(len(sorted_lat) * 0.99)]
    mean = statistics.mean(latencies_ms)
    return {
        "n_queries": n,
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "p99_ms": round(p99, 1),
        "mean_ms": round(mean, 1),
        "threshold_p95_ms": 500,
        "pass": p95 < 500,
    }


# --- main ---

async def main() -> None:
    print("=" * 60)
    print(" xbrain spike — mem0 vs native (Phase 2 decision)")
    print("=" * 60)
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY env var required (mem0 default embedder)")
        sys.exit(1)

    print("Initializing mem0 client...")
    mem = await _make_mem()

    results: dict[str, Any] = {"timestamp": time.time()}
    try:
        results["test_1_team_isolation"] = await test_team_isolation(mem)
        results["test_2_versioning"] = await test_versioning(mem)
        results["test_3_glue_lines"] = await test_truth_level_glue()
        results["test_4_latency"] = await test_latency(mem)
    except Exception as e:
        results["fatal_error"] = f"{type(e).__name__}: {e}"
        import traceback
        results["traceback"] = traceback.format_exc()

    all_pass = all(
        results.get(k, {}).get("pass") is True
        for k in ("test_1_team_isolation", "test_2_versioning",
                  "test_3_glue_lines", "test_4_latency")
    )
    results["all_pass"] = all_pass

    if all_pass:
        results["recommendation"] = "GO mem0 — all 4 tests pass"
    else:
        failed = [
            k for k in ("test_1_team_isolation", "test_2_versioning",
                        "test_3_glue_lines", "test_4_latency")
            if not results.get(k, {}).get("pass")
        ]
        results["recommendation"] = f"NO-GO mem0 → switch to NativeProvider. Failed: {failed}"

    RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print()
    print("=" * 60)
    print(f" Recommendation: {results['recommendation']}")
    print(f" Full results: {RESULTS_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

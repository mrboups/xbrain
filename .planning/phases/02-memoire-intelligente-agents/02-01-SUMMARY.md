---
phase: 02-memoire-intelligente-agents
plan: 01
subsystem: spike
tags: [mem0, spike, decision, qdrant, postgres]
requires:
  - phase: 01
    provides: VM e2-standard-2 (entry gate Phase 2 partial)
provides:
  - apps/spike-mem0/ Python project with 4-test spike script
  - test_data.json with 100 facts across 3 teams (xbrain, acme, your-team content)
  - spike.py runs : team isolation + versioning + truth-level glue effort + latency
  - Output : spike-results.json with GO/NO-GO recommendation
  - PARTIAL : User runs the spike on local Docker, then writes 02-SPIKE-RESULT.md
affects: [02-03 — provider impl choice depends on spike outcome]

tech-stack:
  added: [mem0ai>=1.0.4 (spike-only), spike Python project]
  patterns: [decision spike, empirical benchmarking, throwaway code]

key-files:
  created:
    - apps/spike-mem0/pyproject.toml
    - apps/spike-mem0/README.md (setup + run + cleanup instructions)
    - apps/spike-mem0/test_data.json (100 facts, 3 teams, real xbrain/acme/your-team content)
    - apps/spike-mem0/spike.py (4-test runner, asyncio, output JSON)

key-decisions:
  - "Spike is throwaway — DELETE apps/spike-mem0/ after Plan 02-03 starts"
  - "team_scope encoded as user_id='team:{slug}' (mem0 has no native team_id)"
  - "Latency threshold P95 < 500ms (xbrain perf budget Phase 2)"
  - "Glue threshold ≤ 100 lines (truth-level state machine wrapping mem0)"

requirements-completed: []  # Spike doesn't fulfill REQs, it informs the plan that does (02-03)

duration: ~15 min (scaffolding inline) + USER : ~1 day local spike run
completed: 2026-05-03 (scaffolding) — PARTIAL pending user spike run
status: PARTIAL — awaiting user spike run + 02-SPIKE-RESULT.md
---

# Plan 02-01 — Spike scaffolding done

**Le projet spike est prêt. Tu le runs quand tu veux (1 jour calendar max), tu me reportes le résultat.**

## Pending action user

```bash
# Backends locaux (terminal 1)
docker run -d --name spike-pg -p 5432:5432 -e POSTGRES_PASSWORD=spike -e POSTGRES_USER=spike -e POSTGRES_DB=spike postgres:17
docker run -d --name spike-qdrant -p 6333:6333 qdrant/qdrant:v1.17.1

# Spike (terminal 2)
cd apps/spike-mem0
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e .
$env:OPENAI_API_KEY="sk-..."                       # Windows PowerShell
python spike.py
cat spike-results.json
```

## Output attendu (spike-results.json)

```json
{
  "test_1_team_isolation": {"leak_count": 0, "facts_ingested": 100, "pass": true},
  "test_2_versioning": {"versions_retrieved": 4, "pass": true},
  "test_3_glue_lines": {"glue_lines": 42, "pass": true},
  "test_4_latency": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "pass": true},
  "all_pass": true,
  "recommendation": "GO mem0 — all 4 tests pass"
}
```

Si `all_pass=true` → tu écris `02-SPIKE-RESULT.md` avec décision **GO mem0**, et Plan 02-03 implémente Mem0Provider.
Si NO-GO → décision **NativeProvider**, Plan 02-03 implémente le full native (déjà couvert dans le plan).

## Cleanup après

```bash
docker rm -f spike-pg spike-qdrant
# Une fois Plan 02-03 démarré : rm -rf apps/spike-mem0/
```

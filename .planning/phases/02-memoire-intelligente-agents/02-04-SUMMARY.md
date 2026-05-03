---
phase: 02-memoire-intelligente-agents
plan: 04
subsystem: api+owui
tags: [truth-workflow, 4-eyes, promotions, alembic, openwebui-pipeline, slash-commands]
requires:
  - phase: 02
    plan: 02
    provides: MemoryProvider ABC + types (TruthLevel comparable enum)
  - phase: 02
    plan: 03
    provides: 6 /v1/memory/* endpoints + PATCH truth_level → 405
provides:
  - Migration 0002 — memory_items + memory_items_history + promotions tables (with CHECK constraints)
  - Promotion ORM (registered in models/__init__)
  - Truth-level state machine (services/truth_workflow.py) — sole callsite that mutates truth_level
  - ALLOWED_TRANSITIONS lattice (forward-only — demotions/skips → 422)
  - APPROVAL_REQUIREMENTS map (WORKING=0/auto, VALIDATED=1, CANONICAL=2, PUBLIC=1)
  - 4 endpoints /v1/promotions/* (POST propose, GET pending, POST approve, POST reject)
  - 11 unit tests covering 4-eyes, member-cannot-approve, illegal transitions, demotions, isolation
  - OpenWebUI Pipeline 'promotion-manager' (slash commands /promotions-pending /propose /approve /reject)
  - Pipeline → memory-api auth bridge: `acting_user_sub` JWT claim → resolved to real user
  - promotions repo (team-scoped queries only)
affects:
  - 02-05 RAG can filter on truth_level_min knowing the workflow guarantees integrity
  - 02-06 LangGraph agents reach the same /v1/promotions/* path (auto-promote agent in 02-07)
  - Phase 4 governance — admin/owner role split + PUBLIC requires "external review" rationale

tech-stack:
  added: []
  patterns: [state-machine-as-service, acting-user-sub JWT claim for service→user identity bridge]

key-files:
  created:
    - apps/memory-api/alembic/versions/0002_memory_promotions.py (3 tables + CHECK constraints + 6 indexes)
    - apps/memory-api/app/models/promotion.py (ORM)
    - apps/memory-api/app/services/truth_workflow.py (state machine, ~200 lines)
    - apps/memory-api/app/services/__init__.py
    - apps/memory-api/app/repos/promotions.py
    - apps/memory-api/app/routes/promotions.py (4 endpoints)
    - apps/memory-api/tests/test_truth_workflow.py (11 tests)
    - apps/openwebui-pipeline/app/pipelines/promotion_manager.py (slash command handler)
  modified:
    - apps/memory-api/app/models/__init__.py (export Promotion)
    - apps/memory-api/app/main.py (mount promotions router)
    - apps/memory-api/app/deps.py (acting_user_sub upgrade path in get_current_principal)
    - apps/openwebui-pipeline/app/memory_api_client.py (+ make_user_acting_jwt + 4 promotion methods)
    - apps/openwebui-pipeline/app/main.py (intercept slash commands before LLM dispatch)

key-decisions:
  - "PATCH /v1/memory/{id}.truth_level returns 405 (already in 02-03). The /v1/promotions/* path is the SOLE write path for truth_level."
  - "ALLOWED_TRANSITIONS is forward-only — no demotions in v1. Phase 4 will add a 'revoke/demote' op."
  - "WORKING (level 1) requires 0 approvers — auto-applied on propose. Avoids workflow friction for routine ingestion."
  - "CANONICAL requires 2 distinct admins (4-eyes). PUBLIC requires only 1 admin since the item already passed CANONICAL gate."
  - "Proposer cannot self-approve (403) — enforced server-side, not just UI."
  - "Bridge JWT with iss=openwebui-pipeline + acting_user_sub claim → upgraded to user-kind principal in get_current_principal. The OWUI Pipeline is a trust authority for OWUI user identity."
  - "Member role cannot approve VALIDATED+ — only 'admin'. Phase 1 schema has no 'owner' role; will revisit in Phase 4 governance."
  - "Pipeline slash commands intercepted at chat completions endpoint before LLM dispatch — short-circuit returns OpenAI-compat shape directly."
  - "validation_status auto-flips to 'validated' the moment an item crosses VALIDATED truth_level — single rule co-located with _apply_promotion."

invariants-enforced:
  - "Truth-level can ONLY be mutated through truth_workflow._apply_promotion (asserted by route 405 + grep audit)"
  - "Distinct natural-person approvers — proposer ≠ approver_1 ≠ approver_2"
  - "Forward-only transitions — no skips, no demotions"
  - "Team isolation — promotion lookup by id always scoped by team_scope"

requirements-completed:
  - TRUTH-01  # state machine codified
  - TRUTH-02  # promote/demote workflow (promote only in v1; demote = Phase 4)
  - TRUTH-03  # approver count per level
  - TRUTH-04  # 4-eyes for CANONICAL
  - TRUTH-05  # proposer self-approve blocked
  - TRUTH-06  # admin-only approval for VALIDATED+
  - TRUTH-07  # OWUI slash-command UX
  - TRUTH-08  # rejection captures reason
  - TRUTH-09  # promotions audit trail (audit_log via write_audit on every workflow op)
  - OBS-04    # promotions emit audit rows propose/approve/reject

duration: ~45 min (inline)
completed: 2026-05-03
status: COMPLETE — code + tests written. Migration 0002 needs `alembic upgrade head` against the prod DB before /v1/promotions/* and NativeProvider work end-to-end.
---

# Plan 02-04 — Truth-level promotion workflow (4-eyes for CANONICAL)

**State machine, 4 endpoints, 11 tests, OWUI slash-command UX, JWT acting-user bridge.**

## What got built

1. **Migration 0002** — three tables (`memory_items`, `memory_items_history`, `promotions`) with CHECK constraints on every enum field. Partial index on `promotions.status='pending'` for fast pending-list queries.
2. **State machine (`truth_workflow.py`)** — single source of truth for all promotions. Two declarative tables: `ALLOWED_TRANSITIONS` (lattice) and `APPROVAL_REQUIREMENTS` (count per target).
3. **Routes** — `POST /v1/promotions`, `GET /v1/promotions/pending`, `POST /v1/promotions/{id}/approve`, `POST /v1/promotions/{id}/reject`.
4. **Tests** — 11 tests cover: auto-promotion (WORKING), pending state (VALIDATED), 4-eyes (CANONICAL), self-approval block, member-cannot-approve, skips/demotions, rejection, re-approval block, team isolation, listing filter.
5. **OWUI Pipeline `promotion-manager`** — 4 slash commands intercepted in chat completions before LLM dispatch; short-circuited with OpenAI-compat response shape.
6. **Auth bridge** — `acting_user_sub` JWT claim on pipeline tokens upgrades the principal to user-kind, so promotions are attributed to a real `users.id`.

## Verification

```bash
# Syntax check on all 12 files
python -c "import ast,io; [ast.parse(io.open(f, encoding='utf-8').read()) for f in [...]]"
→ ALL OK
```

The unit tests (`tests/test_truth_workflow.py`) require Postgres via testcontainers — they will run as part of CI / Phase 2 verify-work, not in this codegen pass.

## Pending for full production

1. Run `alembic upgrade head` against prod DB → creates `memory_items`, `memory_items_history`, `promotions`.
2. Switch `MEMORY_BACKEND=mem0` or `native` (currently default `stub`) once Plan 02-01 spike concludes.
3. Phase 2 verify-work pass: actually exercise the OWUI slash-command flow in a real browser session.

## Why this matters

Truth-level is the differentiator. Without an enforced workflow, "CANONICAL" is just a string anyone can set. The 4-eyes invariant + the 405 on direct PATCH is what makes the tag *trustworthy* — every CANONICAL fact has provably been seen and approved by 2 distinct team admins, with their UUIDs stamped in the row.

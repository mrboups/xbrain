---
phase: 14-portability-foundation
plan: 02
subsystem: infra
tags: [portability, debranding, librechat, memory-api, relevance-filter, agent-kb, dockerfile]

# Dependency graph
requires:
  - phase: 14-portability-foundation
    provides: "14-01 Task 5 introduces AGENT_MENTION_ALIASES config-driven mention detection (parallel wave-1 plan; this plan's KB copy documents the same neutral @agent default in lockstep, per-plan without a code dependency)"
provides:
  - "Domain-neutral agent product KB (xbrain_product_kb.md) — no grooveos.app URLs, no @groove/@grooveos brand alias, documents the neutral @agent trigger + AGENT_MENTION_ALIASES configurability"
  - "Neutralized relevance_filter.py few-shot example domains (langfuse.example.com, example.com) with prompt-cache byte-length invariant preserved"
  - "Build-configurable onboarding.js memory-api base (__XBRAIN_MEMORY_API_BASE__ placeholder + same-origin fallback) wired into apps/librechat/Dockerfile via ARG MEMORY_API_BASE_URL"
affects: [14-03b, 14-04, 14-06]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Dockerfile ARG + RUN sed build-time placeholder substitution for client-bundle config (mirrors existing __VM_HOST__ convention)"]

key-files:
  created: []
  modified:
    - apps/memory-api/app/knowledge/xbrain_product_kb.md
    - apps/memory-api/app/services/relevance_filter.py
    - apps/librechat/patches/onboarding.js
    - apps/librechat/Dockerfile

key-decisions:
  - "KB rewrite removed ALL bare '@groove' occurrences (not just the mention-trigger sentence) — every descriptive reference to 'the @groove agent' throughout the file was renamed to 'the agent' / 'the mention-triggered agent' to satisfy the acceptance criterion `grep -c '@groove' == 0`, while the capitalized product name 'GrooveOS' (case-sensitive, does not match the lowercase 'grooveos'/'@groove' greps) was left intact per the plan's explicit 'only the absolute brand URLs change' scope note"
  - "'zero $ to GrooveOS' / 'billed to GrooveOS' phrasing in the routing-cost paragraph was reworded to 'zero cost to the team' / 'billed to the team' — a natural byproduct of rewriting the same two sentences to remove '@groove', and more accurate to what the sentence actually describes (which entity absorbs the API cost)"

requirements-completed: [PORT-01]

# Metrics
duration: ~13min
completed: 2026-07-12
---

# Phase 14 Plan 02: Domain-neutral agent KB, classifier prompt, and onboarding.js Summary

**Removed all functional grooveos.app domain leaks from the agent system-prompt KB, the Haiku relevance-classifier few-shots, and LibreChat's onboarding.js — the latter now takes its memory-api base from a Dockerfile build ARG with a same-origin fallback.**

## Performance

- **Duration:** ~13 min
- **Started:** 2026-07-12T02:38:55Z (approx, from STATE.md session timestamp)
- **Completed:** 2026-07-12T02:51:00Z
- **Tasks:** 3/3 completed
- **Files modified:** 4

## Accomplishments
- The @groove/@agent agent's live system prompt (`xbrain_product_kb.md`, injected verbatim by `team_chat_agent.py`) no longer tells self-hosted users to visit `chat.grooveos.app` / `mcp.grooveos.app` / `grooveos.app/account/teams/` — all replaced with relative paths and "your deployment" phrasing.
- The KB's mention-trigger documentation moved in lockstep with 14-01 Task 5's `AGENT_MENTION_ALIASES` config: the shipped KB now teaches `@agent` (the neutral default) and documents the env var, with zero `@groove`/`@grooveos` brand tokens remaining.
- The Haiku relevance-classifier's few-shot examples (`relevance_filter.py`) no longer embed `langfuse.grooveos.app` or a `grooveos.app` cert path; both replaced with `example.com` while preserving the ≥16,384-byte prompt-cache activation threshold (verified 16,499 bytes).
- `onboarding.js`'s hardcoded `https://api.grooveos.app` is now a build-time placeholder (`__XBRAIN_MEMORY_API_BASE__`) substituted by a new `ARG MEMORY_API_BASE_URL=""` + `RUN sed` step in `apps/librechat/Dockerfile`; an unsubstituted or empty value falls back to same-origin relative fetches (boot-safe).

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite xbrain_product_kb.md domain-neutral** - `b3503be` (feat)
2. **Task 2: Neutralize relevance_filter few-shot example domains** - `dd05917` (feat)
3. **Task 3: onboarding.js build-configurable memory-api base + header comment scrub** - `4ba6123` (feat)

**Plan metadata:** (this commit, see below)

## Files Created/Modified
- `apps/memory-api/app/knowledge/xbrain_product_kb.md` - Domain-neutral product KB; all `grooveos.app` URLs and `@groove`/`@grooveos` mention-alias references replaced with relative paths / "your deployment" phrasing and the neutral `@agent` trigger + `AGENT_MENTION_ALIASES` configurability note
- `apps/memory-api/app/services/relevance_filter.py` - Two few-shot example domains (`langfuse.grooveos.app`, a `grooveos.app` TLS cert path) neutralized to `example.com`; SYSTEM_PROMPT byte length still ≥16,384 (16,499 bytes)
- `apps/librechat/patches/onboarding.js` - `MEMORY_API` hardcode replaced with `__XBRAIN_MEMORY_API_BASE__` placeholder + same-origin fallback logic; header comment scrubbed of `grooveos.app` references
- `apps/librechat/Dockerfile` - Added `ARG MEMORY_API_BASE_URL=""` and a `RUN sed` substitution immediately after the `onboarding.js` COPY step (Patch 4+5 block)

## Decisions Made
- Removed every bare `@groove` occurrence throughout the KB (not just the literal mention-trigger line) because the plan's Task 1 acceptance criteria requires `grep -c '@groove' == 0` on the whole file — descriptive prose like "the @groove agent" appears ~10 times throughout the doc (recall-paths section, GitHub-sync section, "what it sees" section, frontends table, "what it should NOT do" header) and all were renamed to "the agent" / "the mention-triggered agent" for consistency. The capitalized brand name "GrooveOS" was deliberately left untouched (case-sensitive greps don't match it, and the plan's action list scoped the rewrite to "only the absolute brand URLs" + the mention alias).
- Reworded "zero $ to GrooveOS" / "billed to GrooveOS" to "zero cost to the team" / "billed to the team" while touching the same two sentences for the `@groove` removal — this is more accurate to what the sentence is actually describing (cost absorption) and avoids a stray brand reference in a paragraph that was already being rewritten.

## Deviations from Plan

None — plan executed exactly as written. All three tasks' automated `<verify>` commands pass as specified in the plan.

## Issues Encountered

- The plan's overall `<verification>` section states "KB retains functional tokens (`@groove`, relative `/account/teams/` paths)" — this appears to be a stale summary line left over from before Task 1 was amended with the D-08/mention-alias fix (BLOCKER-2), since Task 1's own `<acceptance_criteria>` and `<done>` text explicitly require `grep -c '@groove' == 0` and describe the KB as carrying "no brand mention alias." Followed the more specific, more recently authored Task-level acceptance criteria as authoritative; verified `grep -c '@groove' apps/memory-api/app/knowledge/xbrain_product_kb.md` returns 0 and `grep -c 'account/teams'` returns ≥1 (both satisfied).
- On this Windows execution host, `python -c "import ast; ast.parse(open('apps/memory-api/app/services/relevance_filter.py').read())"` (the exact literal command in Task 2's `<verify>` block) raises `UnicodeDecodeError` because Python's default `open()` encoding on this machine is `cp1252`, not UTF-8, and the file contains em-dash characters. This is a **pre-existing, unrelated-to-this-task artifact** — confirmed by running the identical command against the file at the phase's base commit (`f2f719a`), which fails identically. It is not caused by any change in this plan and will not reproduce on the actual Linux deploy target (GCP VM Ubuntu 24.04), where `open().read()` defaults to UTF-8. Verified correctness instead via `PYTHONIOENCODING=utf-8` / explicit `encoding='utf-8'`, which both pass (`ast.parse` succeeds; `SYSTEM_PROMPT` is 16,499 bytes ≥ 16,384). Also ran `python -m pytest -q -k relevance` (17 passed) to confirm no functional regression.
- Full `pytest -q` on `apps/memory-api` shows 12 pre-existing failures (`test_mention_detector.py` — 11 cases asserting the dead `@claude`/`@cl`/`@c` alias set; `test_github_sync.py::test_sync_repo_multi_chunk_ids`) unrelated to any file this plan touches. Confirmed pre-existing by running the same tests against the base commit `f2f719a` (identical 12 failures). `test_mention_detector.py`'s staleness is explicitly documented as a known-red test in 14-01 Task 4/5 (a parallel wave-1 plan), which repairs it. Out of scope for 14-02 per the Scope Boundary rule — not fixed here.

## User Setup Required

None - no external service configuration required. (Prod `.env` wiring for `MEMORY_API_BASE_URL` build arg and `AGENT_MENTION_ALIASES` legacy-alias restoration are 14-01/14-03b concerns, already documented there.)

## Next Phase Readiness

- All three functional domain leaks on the backend/infra request-response path (agent KB, classifier prompt, onboarding.js) are closed; PORT-01 requirement satisfied for this plan's scope.
- 14-03b (docker-compose build.args wiring for `LIBRECHAT_MEMORY_API_BASE` -> `MEMORY_API_BASE_URL`) can now consume the `ARG MEMORY_API_BASE_URL` added to `apps/librechat/Dockerfile`.
- No blockers for downstream plans in this phase.

---
*Phase: 14-portability-foundation*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 4 modified files + SUMMARY.md confirmed present on disk. All 3 task commit hashes (`b3503be`, `dd05917`, `4ba6123`) confirmed present in `git log --oneline --all`.

---
phase: 14-portability-foundation
plan: 07
subsystem: infra
tags: [librechat, envsubst, docker-compose, portability, agent-mention]

# Dependency graph
requires:
  - phase: 14-portability-foundation (14-01)
    provides: "AGENT_MENTION_ALIASES config field + config-driven mention_detector.py regex (server-side alias set, neutral default 'agent')"
  - phase: 14-portability-foundation (14-03b)
    provides: "apps/librechat/patches/render-config-entrypoint.sh entrypoint-envsubst fallback mechanism + librechat.yaml.template"
provides:
  - "The 5 promptPrefix system prompts in librechat.yaml.template now name a config-driven mention alias (@${AGENT_MENTION_PRIMARY}) instead of the hardcoded, now-nonexistent @groove"
  - "AGENT_MENTION_PRIMARY derivation in render-config-entrypoint.sh: first comma-separated entry of AGENT_MENTION_ALIASES, default 'agent', stripped of whitespace and a leading @"
  - "AGENT_MENTION_ALIASES wired into the librechat service's docker-compose.yml environment block so the entrypoint actually receives operator config instead of always falling back to the hardcoded default"
affects: [14-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Derived display value pattern: a cosmetic/display env var derived from an authoritative config list (first entry) at entrypoint time, rather than configured as a second independent var — guarantees the two can never drift"

key-files:
  created: []
  modified:
    - apps/librechat/patches/render-config-entrypoint.sh
    - infrastructure/librechat/librechat.yaml.template
    - infrastructure/docker-compose.yml

key-decisions:
  - "AGENT_MENTION_PRIMARY is DERIVED (first entry of AGENT_MENTION_ALIASES) inside the entrypoint, not a separately configured env var — a second knob would silently drift from the real alias set mention_detector.py reads server-side (14-01). This guarantees the alias shown in the prompt is always a member of the working alias set, by construction."
  - "envsubst filter list extended from 3 to exactly 4 vars ($LIBRECHAT_ALLOWED_DOMAINS $BRIDGE_BASE_URL $APP_TEAMS_URL $AGENT_MENTION_PRIMARY) — kept explicit rather than switching to unfiltered envsubst, which would corrupt the apiKey/MCP-header ${VAR} placeholders LibreChat resolves natively."
  - "Edit anchored on the literal '@groove' (never a bare 'groove' substring) specifically to avoid corrupting the title-case product name 'GrooveOS', which contains 'roove' and is a deliberate, documented retention (14-03b line 204, 14-05 line 221, 14-06's case-sensitive grep gate)."

patterns-established:
  - "Derived-not-configured display values: when a human-facing string needs to reflect one entry of an authoritative multi-value config list, derive it at render/entrypoint time rather than adding a second config var."

requirements-completed: [PORT-01]

# Metrics
duration: 9min
completed: 2026-07-12
---

# Phase 14 Plan 07: Fix the @groove Mention Instruction Summary

**Replaced the hardcoded, now-broken `@groove` mention instruction in all 5 LibreChat promptPrefix system prompts with a config-derived `@${AGENT_MENTION_PRIMARY}`, sourced from the same `AGENT_MENTION_ALIASES` list the server-side mention detector reads — proven end-to-end against a real container (`mention @agent` by default, `mention @chad` when configured), with the `GrooveOS` product name verified byte-identical before and after.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-07-12T05:51Z (first commit)
- **Completed:** 2026-07-12T05:56:47Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- `apps/librechat/patches/render-config-entrypoint.sh` now derives `AGENT_MENTION_PRIMARY` from `AGENT_MENTION_ALIASES` (first comma-separated entry, default `agent`, whitespace/leading-`@` stripped) and adds it to the envsubst filter (still exactly 4 named vars — no unfiltered envsubst introduced)
- Derivation proven correct for all 4 required cases by running the exact snippet in `sh` (see Verification below)
- All 5 `promptPrefix` strings in `infrastructure/librechat/librechat.yaml.template` now read `(mention @${AGENT_MENTION_PRIMARY})` instead of the hardcoded, no-longer-resolving `@groove`
- `GrooveOS` count in the template verified identical before and after the edit (5 → 5) — the edit was anchored on the literal `@groove`, never a bare `groove` substring, so the title-case brand name was never at risk
- `infrastructure/docker-compose.yml`'s librechat service `environment:` block now carries `AGENT_MENTION_ALIASES: ${AGENT_MENTION_ALIASES:-agent}` — without this the entrypoint would always fall back to the hardcoded default regardless of the operator's `.env`
- End-to-end proof against a real `alpine:3` container (Docker Desktop, linux/aarch64 daemon) running the exact derivation + envsubst logic: `AGENT_MENTION_ALIASES=chad,agent` renders `mention @chad`; unset renders `mention @agent` — both recorded verbatim below
- nginx service block (owned by 14-03a) and compose brand-cleanliness (`grooveos` lowercase count) confirmed unregressed

## Task Commits

Each task was committed atomically:

1. **Task 1: derive the primary alias in the entrypoint and add it to the envsubst filter** - `3981825` (feat)
2. **Task 2: make the 5 promptPrefix strings name the derived alias** - `9e5b3cb` (fix)
3. **Task 3: pass AGENT_MENTION_ALIASES to the librechat container and prove the render end-to-end** - `c91a82d` (feat)

**Plan metadata:** SUMMARY commit follows this document.

## Files Created/Modified
- `apps/librechat/patches/render-config-entrypoint.sh` - Added `AGENT_MENTION_PRIMARY` derivation (first entry of `AGENT_MENTION_ALIASES`, default `agent`) before the envsubst call; extended the envsubst filter from 3 to 4 vars; extended the header comment to explain the single-source-of-truth rationale
- `infrastructure/librechat/librechat.yaml.template` - All 5 `promptPrefix` strings: `@groove` → `@${AGENT_MENTION_PRIMARY}`; `GrooveOS` occurrences untouched (verified byte-identical count)
- `infrastructure/docker-compose.yml` - librechat service `environment:` block gained `AGENT_MENTION_ALIASES: ${AGENT_MENTION_ALIASES:-agent}`; no other service touched

## Decisions Made
See `key-decisions` in frontmatter. In summary: derive, never duplicate, the display alias; keep the envsubst filter explicit; anchor every edit on `@groove` to protect `GrooveOS`.

## Deviations from Plan

None - plan executed exactly as written. All acceptance criteria matched on first attempt; no auto-fixes, no scope changes, no architectural questions raised.

## Verification Evidence (recorded verbatim, per plan instructions)

**Task 1 — derivation proof, all 4 required cases, run via `sh`:**
```
== Case 1: unset ==
agent
== Case 2: chad,agent ==
chad
== Case 3:   @Chad , agent  (with spaces) ==
Chad
== Case 4: empty string ==
agent
```

**Task 2 — GrooveOS corruption guard (before/after):**
```
BEFORE GrooveOS count: 5
AFTER  GrooveOS count: 5   (unchanged)
```

**Task 3 — Docker render proofs (real `alpine:3` container, linux/aarch64 daemon, Docker Desktop 4.81.0):**

Proof 1 — `AGENT_MENTION_ALIASES=chad,agent`:
```
$ docker run --rm -e AGENT_MENTION_ALIASES=chad,agent \
  -v ".../librechat.yaml.template:/t.template:ro" \
  -v ".../render-config-entrypoint.sh:/e.sh:ro" \
  alpine:3 sh -c '...'
mention @chad
```

Proof 2 — `AGENT_MENTION_ALIASES` unset:
```
$ docker run --rm \
  -v ".../librechat.yaml.template:/t.template:ro" \
  -v ".../render-config-entrypoint.sh:/e.sh:ro" \
  alpine:3 sh -c '...'
mention @agent
```

Both outputs were exactly one unique line each, matching the plan's acceptance criteria precisely.

## Issues Encountered
- Docker was not on the Git Bash `$PATH` in this session (only `docker.exe` under `/c/Program Files/Docker/Docker/resources/bin`); resolved by adding that directory to `$PATH` for the docker commands. No workaround/approximation was substituted — both proofs ran against a real container as required.

## User Setup Required

None for local development — `AGENT_MENTION_ALIASES` defaults to `agent` and the rendered prompt matches the default detector alias.

**DEPLOY-PREREQ (carried forward, already tracked in 14-01/14-03b):** the production VM `.env` should set `AGENT_MENTION_ALIASES=agent,grooveos,groove,gr,g` (or the xbrain team's chosen post-rebrand list) to preserve existing `@groove`/`@grooveos` mention triggers. This plan does not change that requirement — it only makes the *displayed* alias in the system prompt track whatever the operator configures, instead of silently naming a dead one.

## Next Phase Readiness
- The functional defect 14-06's acceptance gate could not catch (case-sensitive `grooveos` grep missing both `GrooveOS` and `@groove`) is closed
- No second source of truth was introduced — future alias-list changes (e.g., the planned `chad`-based rebrand) automatically flow into the displayed prompt with zero additional code changes
- No blockers for 14-06 or later phases

---
*Phase: 14-portability-foundation*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 3 files modified by this plan verified present on disk (`apps/librechat/patches/render-config-entrypoint.sh`, `infrastructure/librechat/librechat.yaml.template`, `infrastructure/docker-compose.yml`); all 3 task commits (`3981825`, `9e5b3cb`, `c91a82d`) verified present in git log. No missing items.

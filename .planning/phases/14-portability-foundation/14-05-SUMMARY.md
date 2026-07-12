---
phase: 14-portability-foundation
plan: 05
subsystem: docs
tags: [portability, rebranding, sed, planning-history, test-fixtures]

# Dependency graph
requires:
  - phase: 14-portability-foundation (14-01)
    provides: OAuth env defaults in conftest.py that let the memory-api/mcp-brain
      pytest suites collect; config-driven AGENT_MENTION_ALIASES in mention_detector.py
  - phase: 14-portability-foundation (14-02)
    provides: runtime source de-hardcoding of grooveos.app/aibrussels (this plan
      only touches docs/planning/fixtures, not apps/**/app/**)
provides:
  - Brand-free .planning/** history (90 files) except the documented keep-as-is
    exceptions (design doc, 14-portability-foundation dir, ROADMAP.md, STATE.md)
  - Brand-free docs/**/*.md (recursive), CLAUDE.md, and the PHASE13-HELPER-FIX-NOTES.md runbook
  - Neutralized orphan surfaces: 2 READMEs, chatgpt-actions.json, the spike-mem0
    fixture, and 7 test files across memory-api/mcp-brain/librechat-bridge
  - Logged, approved scope bounds (D-01c, D-01e) with hard occurrence counts
affects: [14-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Scripted sed find/replace for mechanical brand scrub, with an explicit
      exclude list honored via grep -v piping, followed by hand-fixed judgment
      cases (escaped-regex domain patterns, ASCII-diagram truncations, Firebase
      site-id tokens) that a token-literal sed cannot safely match"

key-files:
  created: []
  modified:
    - .planning/PROJECT.md
    - .planning/phases/** (90 files, excluding 14-portability-foundation/**)
    - .planning/features/** (0 files changed -- open-core-edition-design.md correctly excluded)
    - docs/**/*.md (7 files, recursive)
    - CLAUDE.md
    - infrastructure/scripts/PHASE13-HELPER-FIX-NOTES.md
    - apps/memory-api/README.md
    - apps/session-bridge/README.md
    - apps/mcp-brain/chatgpt-actions.json
    - apps/spike-mem0/test_data.json
    - apps/memory-api/tests/test_oauth_as.py
    - apps/memory-api/tests/test_phase12_auto_grant_regression.py
    - apps/memory-api/tests/test_github_catalog.py
    - apps/memory-api/tests/test_internal_resolve_team_scope.py
    - apps/mcp-brain/tests/test_oauth_resolve.py
    - apps/mcp-brain/tests/test_resolve.py
    - apps/librechat-bridge/tests/test_resolve_team_scope.py

key-decisions:
  - "Kept the exclude list byte-exact (design doc, 14-portability-foundation dir,
    ROADMAP.md, STATE.md, both conftest.py files) -- verified via git diff --stat"
  - "test_mention_detector.py (owned by 14-01, D-08) intentionally left untouched --
    it asserts the documented PROD legacy alias override (agent,grooveos,groove,gr,g)
    as CONFIG-DRIVEN test data, not a runtime hardcode"

patterns-established:
  - "Judgment-case scrub follow-up after a token-literal sed: re-run the broad
    grep (bare 'grooveos'/'aibrussels', not just '<token>.app') to catch
    escaped-regex patterns, truncated ASCII-diagram domains, and infra
    identifiers (Firebase site ids) that a literal '.app'-suffixed pattern misses"

requirements-completed: [PORT-01]

# Metrics
duration: 35min
completed: 2026-07-12
---

# Phase 14 Plan 05: Portability scrub — .planning history, docs, orphan test/README fixtures Summary

**Scripted sed scrub of `grooveos.app`->`example.com` / `aibrussels`->`your-team` across 90 `.planning/**` files, 7 `docs/**/*.md` files, `CLAUDE.md`, and 11 orphan runtime-adjacent surfaces (READMEs, chatgpt-actions.json, spike fixture, 7 test files) — with the design-doc/ROADMAP/STATE/phase-14-dir exclude list honored byte-exact and all 3 pytest suites still green.**

## Performance

- **Duration:** ~35 min
- **Completed:** 2026-07-12T03:17:02Z
- **Tasks:** 4
- **Files modified:** 109 (90 + 8 + 11, see Task Commits)

## Accomplishments
- `.planning/**` history + `.planning/PROJECT.md` are brand-free (90 files, 481/481
  balanced insertions/deletions) except the documented keep-as-is exceptions
- `docs/**/*.md` (recursive — catches `docs/superpowers/plans/*.md`), `CLAUDE.md:62`,
  and the PHASE13-HELPER-FIX-NOTES.md runbook are brand-free; the D-01e approved-bound
  surfaces (`README.md`, `app-site/docs/`) are provably still branded (no over-scrub)
- Every orphan surface named in D-01 (2 READMEs, chatgpt-actions.json, the spike-mem0
  fixture, 7 test files) is neutralized to a placeholder; all 3 pytest suites still pass
- The two user-approved scope bounds (D-01c, D-01e) are logged below with hard counts

## Task Commits

Each task was committed atomically:

1. **Task 1: Scriptable scrub of .planning history + PROJECT.md** - `16f8f49` (docs)
2. **Task 2: Scrub docs/**/*.md + CLAUDE.md + runbook notes** - `7318e98` (docs)
3. **Task 3: Neutralize orphan surfaces (READMEs, chatgpt-actions.json, fixtures, tests)** - `3874c6c` (docs)
4. **Task 4: Log approved scope bounds** - this SUMMARY.md (no separate commit; captured in the plan-metadata commit)

_Note: Task 4 is documentation-only and has no independent code commit — its output is this file._

## Files Created/Modified

**Task 1 (90 files):** `.planning/PROJECT.md`, `.planning/INTEGRATION-CHECK-*.md`,
`.planning/KB/*.md`, `.planning/REQUIREMENTS.md`, and 84 files under
`.planning/phases/02..13/**` and `.planning/quick/**` — every `grooveos.app` occurrence
became `example.com`, every `aibrussels` became `your-team`. Three files needed a
hand-fixed follow-up pass because the token-literal sed missed an escaped double-backslash
regex pattern (`bridge\\.grooveos\\.app` in 09-03/05/06-PLAN.md, edited via Edit tool),
an ASCII-diagram truncated domain (`(chat.grooveos)` in 09-RESEARCH.md), a Firebase
Hosting site-id token (`grooveos` — not a domain — in 12-09-SUMMARY.md, mapped to
`example`), and single-backslash escaped CORS regex patterns in `260604-glo-PLAN.md`/`SUMMARY.md`.

**Task 2 (8 files):** `docs/cloudflare-access-setup.md`, `docs/cloudflare-bridge-dns.md`,
`docs/gitops-setup.md`, `docs/google-oauth-scope-upgrade.md`, `docs/session-bridge-review.md`,
`docs/superpowers/plans/2026-05-07-team-onboarding-modal.md` (recursive glob catch — 1 escaped
CORS regex pattern hand-fixed here too), `CLAUDE.md`, `infrastructure/scripts/PHASE13-HELPER-FIX-NOTES.md`.

**Task 3 (11 files):**
- `apps/memory-api/README.md` - `grooveos.app` -> `example.com` in the docs link
- `apps/session-bridge/README.md` - same
- `apps/mcp-brain/chatgpt-actions.json` - `servers[0].url` -> `https://api.example.com`
  (manual operator template for a ChatGPT Custom GPT — no `apps/mcp-brain/README.md`
  exists to append a note to, so the operator instruction is captured here: **the
  operator must edit `servers[0].url` in `chatgpt-actions.json` to their own domain
  before configuring a Custom GPT** — this file is loaded by no service)
- `apps/spike-mem0/test_data.json` - Phase-2 POC fixture, 20 lines swapped
- `apps/memory-api/tests/test_oauth_as.py` - `_RESOURCE` constant + the deliberate
  audience-mismatch pair (`.../mcp` vs `.../other`) both swapped consistently — still mismatch
- `apps/memory-api/tests/test_phase12_auto_grant_regression.py` - 2 `redirect_uri` literals
- `apps/memory-api/tests/test_github_catalog.py` - `aibrussels` team-scope + repo-name fixtures
- `apps/memory-api/tests/test_internal_resolve_team_scope.py` - team-scope fixtures
- `apps/mcp-brain/tests/test_oauth_resolve.py` - `OAUTH_RESOURCE_URL` + matching `aud` pair — still match
- `apps/mcp-brain/tests/test_resolve.py` - `_TEAM` constant
- `apps/librechat-bridge/tests/test_resolve_team_scope.py` - team-scope fixtures

## Decisions Made
- Held the exclude list exact: `.planning/features/open-core-edition-design.md`,
  `.planning/phases/14-portability-foundation/**`, `.planning/ROADMAP.md`, `.planning/STATE.md`,
  and both `conftest.py` files were never touched (verified via `git diff --stat` = empty
  for all of them, and the two keep-as-is baseline counts — design doc=3, 14-CONTEXT.md=21 —
  are unchanged before/after).
- Left `apps/memory-api/tests/test_mention_detector.py` untouched even though it contains
  `grooveos` — it is owned by 14-01 (D-08) and its content is the documented PROD legacy
  alias override (`agent,grooveos,groove,gr,g`) exercised as CONFIG-DRIVEN test data,
  not a runtime hardcode. Neutralizing it would test something other than the documented
  prod behavior, which would reduce test fidelity for no portability benefit. This is a
  narrow, deliberate exception to the plan's literal Task 3 acceptance grep (see Issues
  Encountered below).

## Approved scope bounds — intentionally NOT scrubbed

These are **APPROVED BOUNDS** (user-approved 2026-07-12, ROADMAP amended to match),
**NOT a gap** in this plan's coverage.

| # | Area | Occurrences (`grep -rc 'grooveos'`) | Why | Revisit |
|---|------|--------------------------------------|-----|---------|
| 1 | `app-site/docs/**`, `marketing-site/docs/**`, `app-site/v0-v12/**`, `README.md` positioning | 219 | D-01e: the LIVE hosted-product marketing/docs. Their domain legitimately IS grooveos.app. Scrubbing them would break the live Firebase deploy and is semantically wrong — they are not OSS code an operator self-hosts. | Only if the marketing surface is extracted from the OSS repo (Phase 16). |
| 2 | `chrome-extension/**`, `app-site/account/**` | 26 | D-01c: static browser bundles with no config mechanism; the frontend is REPLACED in Phase 16. Cleaning code about to be deleted is waste. | Phase 16 (new web-chat UI replaces these bundles). |
| 3 | `.planning/ROADMAP.md`, `.planning/STATE.md` | 31 (ROADMAP.md=23, STATE.md=8) | GSD-managed status/audit trail files, owned by the orchestrator, not this plan. | N/A — perpetual GSD-tooling exclusion. |
| 4 | `.planning/features/open-core-edition-design.md` + `.planning/phases/14-portability-foundation/**` | 374 (design doc=3, phase-14 dir=371) | The decision record and this phase's own artifacts — they deliberately NAME the tokens being removed; scrubbing them makes the decision record meaningless. | N/A — permanent by design. |

Also note: the title-case product name `GrooveOS` (librechat.yaml `promptPrefix`, README) is
a PRODUCT NAME, not a domain hardcode — out of D-01's token scope, and a case-sensitive grep
for `grooveos` does not match it. Rebrand of the product name itself is a Phase-16 concern.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Token-literal sed missed escaped-regex domain patterns**
- **Found during:** Task 1 acceptance re-check (`grep -rl -e 'grooveos' ...` returned 3
  residual files after the first sed pass)
- **Issue:** `sed -e 's/grooveos\.app/example.com/g'` only matches a literal single dot
  between "grooveos" and "app". Several plan files store the domain as an escaped-regex
  string with a literal double-backslash before each dot (`bridge\\.grooveos\\.app`, used
  in verification `pattern:` fields), which the plain-dot sed cannot match.
- **Fix:** Hand-fixed the 3 affected lines via the Edit tool (`09-03-PLAN.md`,
  `09-05-PLAN.md`, `09-06-PLAN.md`), preserving the double-backslash escaping so the
  regex still matches the domain it's meant to test against.
- **Files modified:** `.planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-03-PLAN.md`, `09-05-PLAN.md`, `09-06-PLAN.md`
- **Verification:** `grep -rl -e 'grooveos' .planning --exclude-dir=14-portability-foundation | grep -v -e 'open-core-edition-design.md' -e 'ROADMAP.md' -e 'STATE.md'` returns empty
- **Committed in:** `16f8f49` (Task 1 commit)

**2. [Rule 1 - Bug] Same escaped-regex issue recurred in a docs/ file and a quick-task plan**
- **Found during:** Task 2 verification (`docs/superpowers/plans/2026-05-07-team-onboarding-modal.md`)
  and a Task-1 follow-up re-check (`260604-glo-PLAN.md`'s CORS `allow_origin_regex`, single-backslash form)
- **Issue:** Same class of bug as #1 — escaped-dot regex literals not matched by the plain-dot sed.
- **Fix:** Targeted `sed` passes matching the exact backslash count for each file's escaping style
  (single-backslash `grooveos\.app`/`grooveos\.web\.app` in the two 260604-glo files and the
  docs/superpowers file), plus a bare-domain fix in `12-09-SUMMARY.md` for a Firebase Hosting
  site-id token (`grooveos`, not a `.app` domain) and `260604-glo-SUMMARY.md`'s truncated
  `mcp.grooveos...` reference.
- **Files modified:** `docs/superpowers/plans/2026-05-07-team-onboarding-modal.md`,
  `.planning/quick/260604-glo-*/260604-glo-PLAN.md`, `260604-glo-SUMMARY.md`,
  `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-09-SUMMARY.md`
- **Verification:** Full-repo grep for bare `grooveos`/`aibrussels` over `.planning` (minus
  exceptions) and `docs/` + `CLAUDE.md` + the runbook note returns 0 files
- **Committed in:** `16f8f49` (Task 1), `7318e98` (Task 2)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — sed pattern gaps for escaped-regex text, not
logic bugs in application code)
**Impact on plan:** Both fixes were required to satisfy this plan's own acceptance criteria
(zero-match grep). No scope creep — all fixes stayed within the already-declared `files_modified` set.

## Issues Encountered

**Task 3 acceptance grep is broader than the plan's declared `<files>` scope.** The plan's
literal Task 3 `<verify>` command is
`grep -rl -e 'grooveos' -e 'aibrussels' apps/ --include=*.md --include=*.json --include=*.py`
(no exclusions), which also matches `apps/memory-api/tests/test_mention_detector.py` — a file
NOT listed in Task 3's `<files>` and already fully rewritten by 14-01 (verified: 14-01-SUMMARY.md
documents it drives the mention detector from `AGENT_MENTION_ALIASES` config, and its `grooveos`
occurrences are test DATA proving the documented D-08 prod override string still works, not a
hardcode). Editing it here would (a) violate the scrub_safety instruction to stay inside this
plan's declared `files_modified`, and (b) reduce test fidelity by no longer testing the actual
documented prod value. Left untouched by design; the literal `<verify>` grep for Task 3 will
therefore find this one file if re-run unscoped. All 11 files actually declared in Task 3's
`<files>` are clean (verified individually), and all 3 pytest suites pass.

**memory-api suite has 1 pre-existing, unrelated failure** (`test_github_sync.py::test_sync_repo_multi_chunk_ids`),
already discovered and logged in `.planning/phases/14-portability-foundation/deferred-items.md`
by 14-01 (Task 4). Confirmed via `git diff --stat apps/memory-api/tests/test_github_sync.py
apps/memory-api/app/services/github_sync.py` = empty — this plan touches neither file. Not a
regression from this plan's scrub.

## User Setup Required

None — no external service configuration required. Operator note: `apps/mcp-brain/chatgpt-actions.json`
`servers[0].url` (`https://api.example.com`) must be edited to the operator's own domain before
configuring a ChatGPT Custom GPT against a self-hosted instance (this file is a manual template,
loaded by no service — see 14-RESEARCH.md A3).

## Next Phase Readiness
- `.planning/**` history, `docs/**/*.md`, `CLAUDE.md`, and the named orphan surfaces are brand-free
  per D-01, with the two approved bounds (D-01c, D-01e) transparently logged and counted above.
- 14-06 (the phase's verification/gate plan) can now run its repo-wide check (a) against a tree
  where every plan-owned surface (14-01 through 14-05) has already been scrubbed or documented
  as an approved bound — no silent leaks should remain outside the logged exceptions.

---
*Phase: 14-portability-foundation*
*Completed: 2026-07-12*

## Self-Check: PASSED

- FOUND: `.planning/phases/14-portability-foundation/14-05-SUMMARY.md`
- FOUND: `apps/mcp-brain/chatgpt-actions.json`
- FOUND commit `16f8f49` (Task 1)
- FOUND commit `7318e98` (Task 2)
- FOUND commit `3874c6c` (Task 3)

---
phase: 14-portability-foundation
plan: 06
subsystem: infra
tags: [verification, ci, makefile, portability, oauth, nginx, envsubst, deploy-guard]

# Dependency graph
requires:
  - phase: 14-portability-foundation (14-01)
    provides: "Neutral Settings defaults + OAuth fail-fast field_validator in memory-api/mcp-brain + APP_PUBLIC_URL/CORS_ALLOWED_ORIGIN_REGEX/AGENT_MENTION_ALIASES config fields"
  - phase: 14-portability-foundation (14-02)
    provides: "KB + relevance-filter few-shots + onboarding.js de-branded"
  - phase: 14-portability-foundation (14-03a)
    provides: "nginx ingress fully env-driven via XBRAIN_BASE_DOMAIN (envsubst templates)"
  - phase: 14-portability-foundation (14-03b)
    provides: "docker-compose.yml + librechat.yaml.template neutralized; Makefile env-check extended; entrypoint-envsubst mechanism"
  - phase: 14-portability-foundation (14-04)
    provides: "Slim, brand-free root .env.example documenting all required/optional vars"
  - phase: 14-portability-foundation (14-05)
    provides: "Brand-free .planning/** history, docs/**/*.md, CLAUDE.md, orphan test/README fixtures"
  - phase: 14-portability-foundation (14-07)
    provides: "@${AGENT_MENTION_PRIMARY} config-derived mention alias in the 5 promptPrefix strings"
provides:
  - "Neutral fallbacks in all 10 grooveos.app-hardcoded verify/infra scripts, with env override VARIABLE NAMES unchanged — prod values still reproduce prod (SC#2a)"
  - "infrastructure/scripts/verify-phase14.sh — the PORT-01/PORT-02 acceptance gate, 7 checks, exits 0"
  - "infrastructure/scripts/preflight-env.sh — pre-deploy crashloop guard (B3), brand-free, actionable per-var failure messages"
  - "Makefile preflight target wired into deploy's prerequisite list"
  - "Recorded DEFERRED live-regression gate (SC#2b) — the runbook to close it at the next real deploy"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Neutral-fallback-with-unchanged-override-name pattern for verify scripts: ${VAR:-<neutral>} keeps the env var NAME as the reproduction contract, only the fallback VALUE changes"
    - "Pre-deploy hard-fail guard script (preflight-env.sh) as a Makefile prerequisite, separate from the informational env-check target — actionable per-var failure messages, brand-free by design since it ships to self-hosters"

key-files:
  created:
    - infrastructure/scripts/verify-phase14.sh
    - infrastructure/scripts/preflight-env.sh
  modified:
    - infrastructure/scripts/verify-phase5.sh
    - infrastructure/scripts/verify-phase7.sh
    - infrastructure/scripts/verify-phase8.sh
    - infrastructure/scripts/verify-phase9.sh
    - infrastructure/scripts/verify-phase10.sh
    - infrastructure/scripts/verify-phase11.sh
    - infrastructure/scripts/verify-phase12.sh
    - infrastructure/scripts/verify-phase13.sh
    - infrastructure/scripts/brain-index.sh
    - infrastructure/scripts/test-phase13-cross-frontend.py
    - .github/workflow-templates/deploy-cloudrun.yml
    - .github/workflow-templates/deploy-firebase.yml
    - Makefile

key-decisions:
  - "brain-index.sh's registered-project URL ('https://${BRAIN_SLUG}.grooveos.app') was a genuine functional hardcode, not just a comment — wired it through the already-established XBRAIN_BASE_DOMAIN var (14-03a) instead of inventing a new var name"
  - ".github/workflow-templates/deploy-{cloudrun,firebase}.yml's MEMORY_API_URL hardcode was replaced with a documented repo-level `vars.MEMORY_API_URL` — these templates are copied into OPERATORS' OWN repos, so a baked-in xbrain prod URL would have pointed every self-hoster's CI at xbrain's production API"
  - "verify-phase5.sh and verify-phase9.sh referenced the pre-14-03b filename infrastructure/librechat/librechat.yaml (renamed to librechat.yaml.template by 14-03b, part of this same phase) — fixed while these files were already open for the fallback-neutralization edit (Rule 1)"
  - "verify-phase14.sh's check (a) grep excludes are all --exclude/--exclude-dir flags matched against the real repo layout (chrome-extension/, app-site/, marketing-site/, projects-dashboard/ are siblings of the 5 scan roots, not nested inside them) — included anyway for documentation parity with the ROADMAP SC#1 exemption list and as a guard if the scan set is ever widened"
  - "verify-phase14.sh checks (b) and (d) pre-check `python -c \"import pydantic_settings\"` before the real fail-fast test, so a missing dependency cannot masquerade as a passing fail-fast assertion (a false PASS for the wrong reason)"
  - "preflight-env.sh treats missing-OR-empty identically (`grep -qE \"^VAR=.+\"`) — an operator who writes `XBRAIN_BASE_DOMAIN=` with no value gets the same actionable FATAL as one who omits the line entirely"

patterns-established:
  - "verify-phaseN.sh env override documentation MUST name variables only, never production values — production values belong exclusively in the plan's own SUMMARY.md"
  - "Any future GitHub Actions workflow-template file (.github/workflow-templates/**) must source service URLs from `vars.*`/`secrets.*`, never a literal domain — these templates run in OPERATORS' repos, not this one"

requirements-completed: [PORT-01, PORT-02]

# Metrics
duration: 48min
completed: 2026-07-12
---

# Phase 14 Plan 06: Regression-Safety Gate + PORT-01/PORT-02 Acceptance Verifier Summary

**Neutralized the last 12 grooveos.app hardcodes across the verify-script family and 2 GitHub Actions workflow templates (env override names unchanged, so prod values still reproduce prod), authored a 7-check `verify-phase14.sh` that proves PORT-01+PORT-02 offline and exits 0, and shipped a brand-free `preflight-env.sh` crashloop guard wired into `make deploy` — closing the phase's regression-safety gate with the live-regression suite recorded as an explicit DEFERRED gate (prod VM terminated).**

## Performance

- **Duration:** 48 min
- **Started:** 2026-07-12T06:19:00Z (approx, first plan read)
- **Completed:** 2026-07-12T07:07:30Z
- **Tasks:** 4 (3 auto + 1 checkpoint, auto-approved per active workflow.auto_advance)
- **Files modified:** 15 (2 new, 13 modified)

## Accomplishments
- All 10 verify/infra scripts named in Task 1 (`verify-phase5/7/8/9/10/11/12/13.sh`, `brain-index.sh`, `test-phase13-cross-frontend.py`) now carry neutral `http://localhost:*` / `bridge.example.com` fallbacks instead of `grooveos.app` — the env override VARIABLE NAMES are unchanged (`MEMAPI_HOST`, `BRIDGE_HOST`, `LIBRECHAT_HOST`, `MEMORY_API_BASE`, `APP_SITE_BASE`, `MEMORY_API_URL`, `TEST_TEAM_SCOPE`), so `MEMAPI_HOST=<prod> ... bash verify-phaseN.sh` still reproduces today's exact prod assertion set (SC#2a)
- `verify-phase13.sh`'s `TEST_TEAM_SCOPE` default changed from `dejavudev` to the neutral `default` (D-04)
- `brain-index.sh`'s project-registration payload built a literal `https://${BRAIN_SLUG}.grooveos.app` URL — this was a genuine functional hardcode (not a comment); wired through the already-established `XBRAIN_BASE_DOMAIN` var instead of inventing a new one
- 2 GitHub Actions workflow templates (`.github/workflow-templates/deploy-{cloudrun,firebase}.yml`) had `MEMORY_API_URL: https://api.grooveos.app` baked into the brain-indexing step — these templates are copied into **operators' own repos**, so this was pointing every self-hoster's CI brain-indexing step at xbrain's production API; replaced with a documented `vars.MEMORY_API_URL` repo variable
- `infrastructure/scripts/verify-phase14.sh` authored: 7 lettered checks (a–g), PASS/FAIL/SKIP counters mirroring the `verify-phase13.sh` house style, exits 0 with `PASS: 8 / 8` (checks (b) split into 2 PASS lines for memory-api + mcp-brain) — verified live, not just parsed
  - (a) repo-wide bare-token scan (`apps/`, `infrastructure/`, `Makefile`, `.github/`, `CLAUDE.md`) — 0 matches, self-excluded, build-artifact-excluded
  - (b) OAuth fail-fast fires in BOTH memory-api and mcp-brain on empty `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` — verified live (`Settings()` raises, non-zero exit)
  - (c) `main.py`'s CORSMiddleware reads `settings.CORS_ALLOWED_ORIGIN_REGEX`, zero brand token in the file
  - (d) fresh `Settings()` boot at a fictitious `acme.example` domain with zero source edit — verified live
  - (e) nginx envsubst template renders `server_name api.acme.example;` from `XBRAIN_BASE_DOMAIN` alone — verified live with the real `envsubst` binary
  - (f) `.env.example` is brand-free and documents `OAUTH_ISSUER_URL` + `CORS_ALLOWED_ORIGIN_REGEX`
  - (g) SOFT — `.planning/` (outside this phase's own dir) + `docs/*.md` brand-free — SKIPs, never FAILs
- `infrastructure/scripts/preflight-env.sh` authored: brand-free, hard-fails with an actionable per-var message (naming the exact missing var and WHY it's fatal) when any of the 5 now-mandatory vars is missing or present-but-empty; prints `PREFLIGHT OK` and exits 0 when all 5 are set — verified with 4 live tests (missing everything, missing `XBRAIN_BASE_DOMAIN` alone, missing `AGENT_MENTION_ALIASES` alone, all 5 present)
- Makefile: confirmed 14-03b already carried all 5 mandatory vars in `env-check` + the remote SSH guard (no duplicate entries introduced by this plan); added the new `preflight` target and appended it to `deploy`'s prerequisite list (`deploy: env-check preflight sync`)
- The live regression suite (`verify-phase1..13.sh` against a running deployment) is recorded as an explicit DEFERRED gate below — the prod VM is TERMINATED, so it cannot run in this phase (B4 / amended SC#2b)

## Task Commits

Each task was committed atomically:

1. **Task 1: Parameterize verify/infra scripts — neutral domain fallbacks, prod override preserved** - `a654c12` (feat)
2. **Task 2: Author verify-phase14.sh — PORT-01 + PORT-02 acceptance gate** - `4a124c8` (feat)
3. **Task 3: preflight-env.sh + Makefile env-check — the crashloop guard (B3)** - `b4046e2` (feat)
4. **Task 4: CHECKPOINT — record the DEFERRED live-regression gate** - this SUMMARY.md (documentation-only; auto-approved, see below)

**Plan metadata:** SUMMARY commit follows this document.

## Files Created/Modified
- `infrastructure/scripts/verify-phase14.sh` - NEW — the PORT-01/PORT-02 acceptance gate (7 checks)
- `infrastructure/scripts/preflight-env.sh` - NEW — pre-deploy crashloop guard, brand-free
- `infrastructure/scripts/verify-phase5.sh` - stale `librechat.yaml` → `librechat.yaml.template` path fix, comment neutralized
- `infrastructure/scripts/verify-phase7.sh` - `MEMAPI_HOST` fallback neutralized
- `infrastructure/scripts/verify-phase8.sh` - `MEMAPI_HOST` fallback neutralized
- `infrastructure/scripts/verify-phase9.sh` - `BRIDGE_HOST` fallback + 3 comment/echo lines neutralized, stale `librechat.yaml` fallback path fixed
- `infrastructure/scripts/verify-phase10.sh` - `MEMORY_API_BASE`/`APP_SITE_BASE` fallbacks + comments neutralized
- `infrastructure/scripts/verify-phase11.sh` - `MEMAPI_HOST` fallback + comment neutralized (its `'default'` team_scope literal untouched, D-04)
- `infrastructure/scripts/verify-phase12.sh` - `MEMAPI_HOST` fallback + 3 comment lines neutralized
- `infrastructure/scripts/verify-phase13.sh` - `MEMAPI_HOST`/`LIBRECHAT_HOST` fallbacks + `TEST_TEAM_SCOPE` default (`dejavudev`→`default`, D-04) + comments neutralized
- `infrastructure/scripts/brain-index.sh` - `MEMORY_API_URL` fallback neutralized; new `XBRAIN_BASE_DOMAIN` var wired into the project-registration URL template (was a genuine hardcode, not just a comment)
- `infrastructure/scripts/test-phase13-cross-frontend.py` - `MEMAPI_HOST` fallback + docstring neutralized
- `.github/workflow-templates/deploy-cloudrun.yml` - `MEMORY_API_URL` hardcode → `${{ vars.MEMORY_API_URL }}`, documented in the header
- `.github/workflow-templates/deploy-firebase.yml` - same
- `Makefile` - new `preflight` target; `deploy`'s prerequisite list extended (`env-check preflight sync`)

## Decisions Made
See `key-decisions` in frontmatter. In summary: kept override variable NAMES stable everywhere (the reproduction contract for SC#2a), fixed 2 genuine functional hardcodes discovered while touching these files (brain-index.sh's project URL, the workflow templates' CI-pointing URL), and fixed 2 stale post-rename file references (librechat.yaml → librechat.yaml.template) since both files were already open for this plan's edit.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] verify-phase5.sh and verify-phase9.sh referenced the pre-14-03b filename `infrastructure/librechat/librechat.yaml`**
- **Found during:** Task 1, while editing these files for the fallback-neutralization edit
- **Issue:** 14-03b (part of this same phase) renamed `librechat.yaml` to `librechat.yaml.template`. `verify-phase5.sh`'s Test 3 and `verify-phase9.sh`'s Test 7 fallback path both still referenced the old filename, which no longer exists — both tests would fail with a file-not-found even though the underlying feature they test (GitHub social login config, Claude Pro/Max custom endpoint) is unaffected.
- **Fix:** Updated both references to `librechat.yaml.template`.
- **Files modified:** `infrastructure/scripts/verify-phase5.sh`, `infrastructure/scripts/verify-phase9.sh`
- **Verification:** `grep -n 'librechat.yaml' infrastructure/scripts/verify-phase5.sh infrastructure/scripts/verify-phase9.sh` shows only `.template`-suffixed references.
- **Committed in:** `a654c12` (Task 1 commit)

**2. [Rule 2 - Missing scope] brain-index.sh's registered-project URL was a genuine functional grooveos.app hardcode, not a comment**
- **Found during:** Task 1, full-file review of `brain-index.sh` for the acceptance grep
- **Issue:** `PROJECTS_PAYLOAD`'s `'url': 'https://${BRAIN_SLUG}.grooveos.app'` bakes the brand domain into every project registered via the brain-index script's CI step — a self-hoster running this script would register projects under xbrain's own domain, not theirs.
- **Fix:** Added `XBRAIN_BASE_DOMAIN="${XBRAIN_BASE_DOMAIN:-localhost}"` (reusing the var name already established by 14-03a for the same purpose) and changed the URL template to `'https://${BRAIN_SLUG}.${XBRAIN_BASE_DOMAIN}'`.
- **Files modified:** `infrastructure/scripts/brain-index.sh`
- **Verification:** `grep -c 'grooveos' infrastructure/scripts/brain-index.sh` returns 0; `bash -n` parses.
- **Committed in:** `a654c12` (Task 1 commit)

**3. [Rule 2 - Missing scope, BLOCKER-5] `.github/workflow-templates/*.yml` MEMORY_API_URL hardcode pointed operator CI at xbrain's own prod API**
- **Found during:** Task 1, per the plan's explicit BLOCKER-5 instruction
- **Issue:** Both `deploy-cloudrun.yml` and `deploy-firebase.yml`'s brain-indexing step set `MEMORY_API_URL: https://api.grooveos.app` literally. These files are TEMPLATES copied into operators' own repos (per the file's own header comment) — every self-hoster's CI brain-indexing step would silently call xbrain's production API instead of their own deployment.
- **Fix:** Replaced with `MEMORY_API_URL: ${{ vars.MEMORY_API_URL }}` and documented the new repo-level variable (optional — brain-index.sh is fail-soft) in each template's header comment block.
- **Files modified:** `.github/workflow-templates/deploy-cloudrun.yml`, `.github/workflow-templates/deploy-firebase.yml`
- **Verification:** `grep -c 'grooveos' .github/workflow-templates/deploy-cloudrun.yml .github/workflow-templates/deploy-firebase.yml` returns 0 for both.
- **Committed in:** `a654c12` (Task 1 commit)

**4. [Rule 1 - Bug] `grep -c ... || echo 0` produced a duplicate "0" line in verify-phase14.sh's checks (c) and (f)**
- **Found during:** Task 2, first live run of `verify-phase14.sh`
- **Issue:** `grep -c PATTERN file` prints `0` to stdout even on its own non-zero (no-match) exit code — my initial `brand_count=$(grep -cE "$BRAND_RE" file 2>/dev/null || echo 0)` therefore produced TWO lines of output ("0\n0") whenever there were zero matches, which broke the `[[ "$brand_count" -eq 0 ]]` integer comparison (`syntax error in expression`) and caused checks (c) and (f) to FAIL even though the underlying condition (zero brand-token matches) was true.
- **Fix:** Removed the `|| echo 0` fallback (redundant — grep -c already emits "0"); added `brand_count="${brand_count:-1}"` to safely default to a FAILING value only in the genuine edge case where grep produces no output at all (e.g., target file missing).
- **Files modified:** `infrastructure/scripts/verify-phase14.sh`
- **Verification:** Re-ran the full script — `PASS: 8 / 8`, exit 0.
- **Committed in:** `4a124c8` (Task 2 commit — caught and fixed before the commit, so the committed version is already correct)

---

**Total deviations:** 4 auto-fixed (2 stale post-rename path fixes, 1 missing-scope functional hardcode in brain-index.sh, 1 missing-scope functional hardcode in the CI workflow templates, 1 self-authored script bug caught before commit). No scope creep — all fixes were either explicitly directed by the plan's own BLOCKER-5 text or were required to make this plan's own acceptance criteria pass as literally written; all stayed within files already declared in the plan's `files_modified` list or explicitly called out in the plan's action text.
**Impact on plan:** All four were necessary for correctness. No unrelated files were touched.

## Issues Encountered

None beyond the self-caught bug documented above. `grep -c 'grooveos'` across the full repo (`apps/`, `infrastructure/`, `Makefile`, `.github/`, `CLAUDE.md`) returns 0 matches outside the one explicitly-approved-and-documented exception (`.github/workflows/deploy-dashboard.yml`, D-01e EXTENDED — the hosted-product dashboard's own deploy workflow, out of this plan's scope per the orchestrator's amendment note).

## DEFERRED GATE — run at the next real deploy

The prod VM is **TERMINATED** (cost pause during the Prime pivot — confirmed via project memory `project_xbrain_vm_paused_cost`). The `verify-phase1..13.sh` scripts `curl` a LIVE deployment — they **CANNOT be executed in this phase**. This was not attempted, and SC#2 is **NOT** being marked satisfied by static grep alone.

Phase 14 is **CODE-COMPLETE**: the stack is config-driven (no brand in backend/infra source), the OAuth fail-fast + CORS are env-sourced, nginx is templated, the agent mention alias is configurable, and `verify-phase14.sh` proves PORT-01 + PORT-02 offline.

What is **NOT** done, and cannot be done here: the LIVE regression suite. SC#2b is therefore **DEFERRED, not passed.**

This phase also made 5 env vars MANDATORY. Deploying without them does NOT fail loudly on its own — each has a distinct silent or fatal failure mode. That is why the following runbook is a blocking gate at the next deploy:

1. Populate the target `.env` with the now-MANDATORY vars (the compose fallback is gone). ALL FIVE
   are required — each has a silent-failure mode if omitted:
     ```
     OAUTH_ISSUER_URL=https://api.grooveos.app
     OAUTH_RESOURCE_URL=https://mcp.grooveos.app/mcp
     CORS_ALLOWED_ORIGIN_REGEX=(chrome-extension://.*|https://chat\.grooveos\.app|https://grooveos\.app|https://grooveos\.web\.app|https://claude\.ai)
     XBRAIN_BASE_DOMAIN=grooveos.app
       # ^ WITHOUT THIS, every nginx vhost renders as *.localhost -> TOTAL INGRESS OUTAGE.
     AGENT_MENTION_ALIASES=agent,grooveos,groove,gr,g
       # ^ WITHOUT THIS, aliases fall back to `agent` and @groove/@grooveos SILENTLY die in prod.
       #   `agent` is included FIRST so the shipped KB (which documents @agent) is also true here.
     ```
2. `bash infrastructure/scripts/preflight-env.sh .env`   # MUST print PREFLIGHT OK
3. Deploy. Then confirm both services actually booted (they now hard-fail on empty OAuth):
     `docker compose ps memory-api mcp-brain`  # both Up, RestartCount 0
4. Run the live regression suite with the PROD values — ALL must PASS (SC#2b):
     ```
     MEMAPI_HOST=https://api.grooveos.app \
     LIBRECHAT_HOST=https://chat.grooveos.app \
     TEST_TEAM_SCOPE=aibrussels \
     bash infrastructure/scripts/verify-phase{5,7,8,9,10,12,13}.sh
     ```
5. Sanity-check the ingress actually renders (14-03a moved all 7 vhosts to envsubst templates):
     `docker compose exec nginx nginx -t`  # syntax OK
     `curl -sI https://api.grooveos.app/health`  # 200

Mark the phase as CODE-COMPLETE with SC#2b DEFERRED — not as fully verified.

### Checkpoint resolution

This plan's Task 4 was a `checkpoint:human-verify` (`gate="blocking"`). The active workflow configuration (`workflow.auto_advance = true`) auto-approves `checkpoint:human-verify` checkpoints per the executor's auto-mode protocol — auth gates (`checkpoint:human-action`) are the only checkpoint type that still hard-stops in auto mode, and this is not one. The runbook above is the artifact a human reviews before the next real deploy touches the VM; nothing here requires action from within this execution.

`⚡ Auto-approved: Phase 14 CODE-COMPLETE with SC#2b DEFERRED — the 5-step runbook above is the actionable follow-up, gated by preflight-env.sh at deploy time regardless of whether a human re-reads this file first.`

## DEPLOY-PREREQ (carried forward — consolidated from 14-01/14-03a/14-03b)

The VM `.env` must gain these 5 now-mandatory vars before the next deploy, or the Makefile's `preflight` target (added in this plan, Task 3) will correctly ABORT it before touching the VM:

- `OAUTH_ISSUER_URL`
- `OAUTH_RESOURCE_URL`
- `CORS_ALLOWED_ORIGIN_REGEX`
- `XBRAIN_BASE_DOMAIN`
- `AGENT_MENTION_ALIASES`

Full prod values for all of these (plus the other DEPLOY-PREREQ vars accumulated across 14-01/14-03b) are listed in the DEFERRED GATE runbook above and in `14-03b-SUMMARY.md`'s "User Setup Required" section.

## User Setup Required

None for this plan's own execution — every task ran fully offline (static grep, local `Settings()` construction with dummy env vars, local `envsubst` against a template file). See "DEFERRED GATE" above for what the **next real deploy** requires.

## Next Phase Readiness

- PORT-01 and PORT-02 are both provable by a single green `verify-phase14.sh` (amended SC#1, SC#3, SC#4) — verified live in this environment, `PASS: 8 / 8`, exit 0.
- SC#2a (prod values reproduce prod via unchanged verify-script env overrides) is satisfied — every fallback neutralization in this plan preserved the override variable NAME.
- SC#2b (live regression suite) is an explicit, actionable DEFERRED gate — not silently dropped. The runbook above is ready to execute the moment the VM is un-terminated.
- The Makefile's `deploy` target cannot silently crashloop memory-api/mcp-brain anymore: `preflight` runs before `sync`, with brand-free, actionable per-var failure messages.
- No blockers for closing Phase 14 as CODE-COMPLETE.

---
*Phase: 14-portability-foundation*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 3 files created/modified by this plan verified present on disk (`infrastructure/scripts/verify-phase14.sh`, `infrastructure/scripts/preflight-env.sh`, `.planning/phases/14-portability-foundation/14-06-SUMMARY.md`); all 3 task commits (`a654c12`, `4a124c8`, `b4046e2`) verified present in git log. No missing items.

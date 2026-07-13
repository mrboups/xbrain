---
phase: 15-edition-mechanics
plan: 03
subsystem: infra
tags: [docker-compose, profiles, edition, minio, neo4j, langfuse, arm64]

# Dependency graph
requires:
  - phase: 15-edition-mechanics
    provides: "15-01's profile-safe depends_on graph (3 illegal cross-profile edges removed) and the untagged-core `minio` rename — the Wave-0 prerequisite that lets `profiles:` tags parse at all"
  - phase: 15-edition-mechanics
    provides: "15-02's validated `Settings.EDITION` field + `create_app(edition)` router registry (oss=91 routes, saas=94 routes) — this plan wires the deployment-time flag that selects between them"
provides:
  - "infrastructure/docker-compose.yml: profiles: [\"integrations\"|\"saas\"|\"ops\"] tags on 22 of 32 services; 10 remain untagged as the OSS-light core"
  - "EDITION: ${EDITION:-oss} passed to memory-api's environment block — the exact Phase-14 AGENT_MENTION_ALIASES wiring gap, closed for this variable"
  - ".env.example COMPOSE_PROFILES + EDITION documentation block, placed as the first operator decision"
affects: [15-04, edition-mechanics, deployment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Compose profile membership must be asserted BY NAME via real `docker compose config --services` output, never by count alone or by YAML grep — a two-service swap (session-bridge<->mcp-calendar) preserves both the count and config -q exit 0, and only a by-name diff catches it"
    - "A blank-valued .env.example var's explanatory comment goes on the line ABOVE the assignment, never trailing it — docker compose's env-file parser does not strip an inline # comment when the value itself is empty (established by 15-01 for MINIO_URL et al., reapplied here for COMPOSE_PROFILES)"
    - "Assert env-var delivery against `docker compose config --format json`'s resolved container environment, not the YAML source text — this is what would have caught the Phase-14 AGENT_MENTION_ALIASES-never-passed defect before it shipped"

key-files:
  created: []
  modified:
    - infrastructure/docker-compose.yml
    - .env.example

key-decisions:
  - "Verified all 32 services by name against the plan's table before tagging; final split matched exactly (10 core / 14 integrations / 7 saas / 1 ops) with no reasoning required beyond the table — no service's placement was ambiguous"
  - "Placed EDITION in the memory-api environment block directly after ADMIN_USER_SUBS (the plan's specified location, 'near LOG_LEVEL/ADMIN_USER_SUBS, at the top of the app-level knobs')"
  - "Moved COMPOSE_PROFILES' explanatory comment to a line above the assignment (not trailing) because its default value is intentionally blank — reusing the exact parser-bug precedent 15-01 just fixed for MINIO_URL/MINIO_ACCESS_KEY/MINIO_SECRET_KEY, to avoid reintroducing it"
  - "Proved the by-name profile-membership check actually discriminates: swapped session-bridge (saas->integrations) and mcp-calendar (integrations->saas) via a scripted edit, confirmed both diffs against int-expected.txt/saas-expected.txt failed non-empty while counts stayed 24/17 and config -q still exited 0 for every profile, then reverted cleanly before committing"

patterns-established:
  - "Every docker-compose acceptance check in this plan ran against real `docker compose config` output (JSON or --services), not YAML greps — jq was unavailable in this environment, so JSON assertions used Node's require() against files written to the scratchpad path (Windows node cannot resolve Git Bash's /tmp mapping)"

requirements-completed: [EDIT-01, EDIT-02]

# Metrics
duration: 22min
completed: 2026-07-13
---

# Phase 15 Plan 03: Compose Profile Tags + EDITION Wiring Summary

**Tagged 22 of 32 `infrastructure/docker-compose.yml` services with `profiles: ["integrations"|"saas"|"ops"]`, leaving exactly the 10 named OSS-light-core services untagged, and wired `EDITION: ${EDITION:-oss}` into memory-api's environment block so an operator's `.env` setting is actually delivered (not silently dropped the way `AGENT_MENTION_ALIASES` was in Phase 14) — both proven against real `docker compose config` output, by name, not by count or grep.**

## Performance

- **Duration:** 22 min
- **Started:** 2026-07-13T22:40Z (approx, after Wave-1 base)
- **Completed:** 2026-07-13T23:02Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- All 32 services in `infrastructure/docker-compose.yml` explicitly accounted for: 10 untagged core (`nginx`, `postgres`, `qdrant`, `memory-api`, `minio`, `centrifugo`, `mcp-brain`, `mcp-gateway`, `mcp-scraper`, `brain-janitor`), 14 `integrations`, 7 `saas`, 1 `ops` — arithmetic 10+14+7+1=32 confirmed against real Compose output, not eyeballed
- `docker compose config --profiles` resolves to exactly `integrations ops saas`, no `pro`
- Bare `docker compose config --services` (no profile) resolves to exactly the 10 named core services — verified by count (10) AND by diff-by-name AND by an independent deny-list grep over all 22 opt-in names (all three checks required to catch what a naive first pass would have missed: research had shown an earlier, incomplete table shipped 15 services in the bare core)
- Each opt-in profile diffed **by name**, not merely by count: `integrations` = 24 services, `saas` = 17, `ops` = 11, all three combined = 32 — every diff empty, every count matched
- Proved the by-name check actually discriminates: swapped `session-bridge` (saas) into `integrations` and `mcp-calendar` (integrations) into `saas`; both by-name diffs failed (non-empty) while both counts stayed unchanged (24/17) and `docker compose --profile X config -q` kept exiting 0 for every profile — exactly the failure mode the plan's acceptance criteria warned would slip past a count-only check. Reverted cleanly before committing.
- `docker compose --profile ops config -q`, `--profile integrations config -q`, `--profile saas config -q` all exit 0 — each opt-in profile is independently a legal Compose project on its own (this is the check that would have caught the `xbrain-backup -> librechat-mongo` edge 15-01 already removed)
- `COMPOSE_PROFILES=integrations,saas` (the operator-facing env var) resolves an identical service set to `--profile integrations --profile saas` (the CLI flags)
- `EDITION: ${EDITION:-oss}` added to memory-api's environment block; `docker compose config --format json` resolves it to `oss` by default and `saas` when the operator sets `EDITION=saas` — asserted against Compose's own resolved JSON output, not the YAML source, closing the exact class of defect Phase 14 shipped for `AGENT_MENTION_ALIASES`
- `EDITION` reaches `memory-api` and no other service (checked via a full-services JSON scan)
- `QDRANT_COLLECTION` (`messages`) and `MINIO_ENDPOINT` (`minio:9000`) resolve identically for `memory-api`/`brain-janitor`/`mcp-deck` across all five profile combinations (bare, integrations, saas, ops, all-three) — D-15-04 ("a profile flip must never change what a service believes about its data") holds
- `.env.example` documents both `COMPOSE_PROFILES` and `EDITION` near the top of the file, as the first operator decision, with the "no `pro` edition" invariant and the `saas`-profile-implies-`EDITION=saas` coupling both spelled out
- `COMPOSE_PROFILES=` (intentionally blank) has its explanatory comment placed on the line ABOVE the assignment, not trailing it — reusing 15-01's just-fixed precedent for `MINIO_URL`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` to avoid reintroducing the same inline-comment-on-blank-value parser bug

## Task Commits

Each task was committed atomically:

1. **Task 1: Tag all 22 opt-in services; leave exactly 10 untagged** - `97be938` (feat)
2. **Task 2: Wire EDITION through to memory-api and document both operator knobs in .env.example** - `09eb6f3` (feat)

**Plan metadata:** (this commit, made after this SUMMARY)

## Per-Profile Service Counts (fixtures for 15-04's gate diffs)

| Profile combination | Service count | Command |
|---|---|---|
| *(bare, no profile)* | 10 | `docker compose config --services` |
| `--profile integrations` | 24 | `docker compose --profile integrations config --services` |
| `--profile saas` | 17 | `docker compose --profile saas config --services` |
| `--profile ops` | 11 | `docker compose --profile ops config --services` |
| all three combined | 32 | `docker compose --profile integrations --profile saas --profile ops config --services` |

**Exact membership (by name):**
- **Core (10, untagged):** `brain-janitor`, `centrifugo`, `mcp-brain`, `mcp-gateway`, `mcp-scraper`, `memory-api`, `minio`, `nginx`, `postgres`, `qdrant`
- **`integrations` (14):** `agent-runtime`, `drive-sync`, `granola-sync`, `graphiti-service`, `langfuse`, `langfuse-clickhouse`, `langfuse-redis`, `langfuse-worker`, `mcp-calendar`, `mcp-deck`, `mcp-drive-read`, `mcp-github`, `neo4j`, `searxng`
- **`saas` (7):** `librechat`, `librechat-bridge`, `librechat-meili`, `librechat-mongo`, `openwebui`, `openwebui-pipeline`, `session-bridge`
- **`ops` (1):** `xbrain-backup`

No service required reasoning beyond the plan's table — every placement matched the `15-CONTEXT.md` D-15-02 table exactly.

## Files Created/Modified

- `infrastructure/docker-compose.yml` - added `profiles:` key to 22 services (14 `integrations`, 7 `saas`, 1 `ops`), directly under each service's `container_name:` line; added `EDITION: ${EDITION:-oss}` to `memory-api`'s environment block, after `ADMIN_USER_SUBS`
- `.env.example` - new "Edition & profiles (Phase 15)" section at the top of the file, documenting `COMPOSE_PROFILES` (blank default, comment above the assignment) and `EDITION` (`oss` default, comment trailing since the value is non-blank)

## Decisions Made

- Kept the plan's exact wording for both the compose comment block and the `.env.example` section, with one intentional deviation: moved `COMPOSE_PROFILES`'s trailing `# [optional]` comment to a line above the blank assignment, since the plan's literal text (`COMPOSE_PROFILES=    # [optional] ...`) would have reintroduced the inline-comment-on-blank-value bug 15-01 just fixed for the MinIO vars. `EDITION=oss` kept its trailing comment as specified — its value is non-blank, so the parser bug does not apply.
- No service placement required discretion beyond the plan's table — all 32 services matched the D-15-02 table without ambiguity.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's literal `.env.example` text for `COMPOSE_PROFILES` would have reintroduced the blank-value inline-comment parser bug 15-01 just fixed**
- **Found during:** Task 2, transcribing the plan's literal `.env.example` block
- **Issue:** The plan's action text specifies `COMPOSE_PROFILES=                                                             # [optional] empty = OSS-light core (10 services)` — a blank-valued assignment with a trailing inline comment. 15-01's own SUMMARY documents that `docker compose`'s env-file parser does not strip an inline `#` comment when the value is genuinely blank (confirmed via isolated repro: `FOO=    # comment` resolves `FOO` to the literal string `"# comment"`, not empty), which is exactly why `MINIO_URL`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` had their trailing comments moved above the assignment in 15-01. Following the plan's literal text for `COMPOSE_PROFILES` verbatim would have resolved it to `"# [optional] empty = OSS-light core (10 services)"` instead of empty, breaking the intended "empty = OSS-light core" default the moment an operator left it unset.
- **Fix:** Moved the explanatory comment (and an added note referencing the precedent) to lines above `COMPOSE_PROFILES=`, leaving the assignment itself bare. `EDITION=oss` was left with its trailing comment as the plan specified, since its default value is non-blank and the parser bug does not apply there.
- **Files modified:** `.env.example`
- **Verification:** No live check needed for correctness of an *unset* blank var (compose's own default-substitution for unset vars in `.env.example`'s absence is standard), but the fix was cross-checked against the `docker compose config --format json` resolution of `COMPOSE_PROFILES` implicitly via the profile-membership tests, which continued to pass with `COMPOSE_PROFILES` unset entirely.
- **Committed in:** `09eb6f3` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (plan's literal text would have reintroduced a bug the same phase's Wave 0 had just fixed for a sibling variable)
**Impact on plan:** The fix was required to make the plan's own "empty = OSS-light core" default actually true against real `docker compose` env-file parsing. No scope creep — the fix stayed within `.env.example`, exactly the file the task already declared.

## Issues Encountered

- **`jq` not available in this environment.** Task 2's acceptance criteria specify `jq`-based JSON extraction from `docker compose config --format json`. Substituted Node.js (`require()` against the JSON output) for equivalent assertions — same underlying data source (Compose's resolved JSON config), same pass/fail semantics.
- **Windows-native Node cannot resolve Git Bash's `/tmp` path mapping.** JSON files written to `/tmp/*.json` from Bash were invisible to `node -e "require('/tmp/...')"` (`MODULE_NOT_FOUND`). Redirected all intermediate JSON output to the session scratchpad directory (a real Windows path) instead, which Node resolved correctly.
- **First swap-discrimination test attempt corrupted an unrelated service's profile.** A `sed -i '0,/regex/{n;s/.../.../}'` range command was intended to touch only the `session-bridge` and `mcp-calendar` lines but instead executed its substitution repeatedly across the 0-to-match range, accidentally flipping `neo4j`'s profile from `integrations` to `saas` and breaking `graphiti-service`'s same-profile dependency on it (`depends on undefined service "neo4j"`). Caught immediately via the resulting `config -q` failure, restored from a pre-edit backup copy, and redid the swap safely with a Python (`/c/Python313/python`) string-replace targeting only the two intended lines. Confirmed via `grep` that `neo4j`'s profile was untouched before re-running the discrimination test, then reverted the swap and re-verified the final committed state matched Task 1's intended output exactly.

## Known Stubs

None — this plan only edits `infrastructure/docker-compose.yml` and `.env.example`; no UI or application code touched.

## Threat Flags

None. The plan's own threat register (T-15-03-01 core-leak-by-omission, T-15-03-02 EDITION-silently-ignored, T-15-03-03 profile-flip-changes-data-identity) fully covers the surface this plan touches, and all three were verified via the acceptance criteria above (independent deny-list grep, `docker compose config --format json` resolution, and cross-profile QDRANT_COLLECTION/MINIO_ENDPOINT identity respectively). No new network endpoint, auth path, or schema change was introduced.

## User Setup Required

None — no external service configuration required. Both `COMPOSE_PROFILES` and `EDITION` have safe defaults (empty / `oss`). Note that "empty `COMPOSE_PROFILES`" is a real behavior change from the pre-Phase-15 state, where all 32 services ran by default with no profile mechanism at all — see Next Phase Readiness.

## Next Phase Readiness

- 15-04 (the live deploy gate / `preflight-env.sh`) can build directly on this plan's fixture counts (10/24/17/11/32) and the by-name membership lists recorded above.
- **Deploy-time behavior change to flag for 15-04 or ops:** any existing deployment that boots off this compose file with no `COMPOSE_PROFILES` set will now start ONLY the 10-service OSS-light core — `librechat`, `openwebui`, `neo4j`, `langfuse`, and everything else that was previously untagged and always-on will stop being started by a bare `docker compose up -d` unless the operator sets `COMPOSE_PROFILES` explicitly. This is the intended Phase 15 behavior (D-15-02), but a live VM redeploy without updating `.env`'s `COMPOSE_PROFILES` would silently shrink the running stack. 15-04's `preflight-env.sh` and any deploy runbook update should call this out explicitly.
- The `saas`-profile-implies-`EDITION=saas` invariant is documented in both `.env.example` and the compose comment, but — per 15-02's own summary — is not enforced by memory-api itself; 15-04's `preflight-env.sh` is the named enforcement point.
- No blockers identified for 15-04.

---
*Phase: 15-edition-mechanics*
*Completed: 2026-07-13*

## Self-Check: PASSED

Both modified files (`infrastructure/docker-compose.yml`, `.env.example`) found on disk; both task
commits (`97be938`, `09eb6f3`) found in `git log --oneline --all`.

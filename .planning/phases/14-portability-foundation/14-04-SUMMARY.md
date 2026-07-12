---
phase: 14-portability-foundation
plan: 04
subsystem: infra
tags: [env-template, documentation, portability, docker-compose, oss]

# Dependency graph
requires:
  - phase: 14-portability-foundation (14-01)
    provides: "Neutral Settings defaults + OAuth fail-fast field_validator in memory-api/mcp-brain + APP_PUBLIC_URL/CORS_ALLOWED_ORIGIN_REGEX/AGENT_MENTION_ALIASES config fields"
  - phase: 14-portability-foundation (14-03a)
    provides: "XBRAIN_BASE_DOMAIN drives all 8 nginx ingress vhosts"
  - phase: 14-portability-foundation (14-03b)
    provides: "docker-compose.yml + librechat.yaml.template neutralized; LIBRECHAT_ALLOWED_DOMAINS/BRIDGE_BASE_URL/APP_TEAMS_URL/LIBRECHAT_MEMORY_API_BASE/CENTRIFUGO_ALLOWED_ORIGINS/WEBUI_URL wired; Makefile env-check extended to 11 vars"
provides:
  - "Slim, 6-section (D-06), fully-tagged root .env.example — the single operator-fillable env surface for PORT-02 (ROADMAP SC#3)"
  - "All 11 previously-undocumented vars (XBRAIN_BASE_DOMAIN, APP_PUBLIC_URL, CORS_ALLOWED_ORIGIN_REGEX, OAUTH_ISSUER_URL, OAUTH_RESOURCE_URL, LIBRECHAT_ALLOWED_DOMAINS, BRIDGE_BASE_URL, APP_TEAMS_URL, LIBRECHAT_MEMORY_API_BASE, CENTRIFUGO_ALLOWED_ORIGINS, WEBUI_URL) now have a template line"
  - "Previously config.py-default-only vars (MINIO_*, QDRANT_COLLECTION, GITHUB_FALLBACK_TOKEN, AGENT_RUNTIME_INTERNAL_URL, GITHUB_CATALOG_*) now documented"
  - "infrastructure/.env.example deleted (its one unique note, brain-janitor's 30-day retention pin, folded into the root file's Neo4j group)"
  - "Three per-service .env.example templates (memory-api, librechat-bridge, openwebui-pipeline) neutralized and cross-referencing the root file as canonical"
affects: [14-05, 14-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Env template organized by D-06 concern groups (required-boot / LLM keys / domain-public-URLs / OAuth-identity / optional-integrations / SaaS-only) instead of by implementation-phase chronology"
    - "Every var tagged [required]/[optional] with a one-line trailing comment; multi-line comment blocks reserved for the few vars with real operational risk (OAuth pair, CORS regex, AGENT_MENTION_ALIASES)"

key-files:
  created: []
  modified:
    - .env.example
    - apps/memory-api/.env.example
    - apps/librechat-bridge/.env.example
    - apps/openwebui-pipeline/.env.example
  deleted:
    - infrastructure/.env.example

key-decisions:
  - "Kept every var actually consumed by docker-compose.yml or a service's Settings class, even when not explicitly named in the plan's per-section example list — the plan's lists were representative of the NEW/previously-hidden vars this phase adds, not an exhaustive membership contract; dropping still-consumed vars (JWT_SECRET, MONGO_URI, POSTGRES_USER, etc.) would have broken the plan's own must_haves truth (\"operator boots a working install\")"
  - "Cross-referenced every var name against infrastructure/docker-compose.yml's ${VAR} substitutions (not memory) before writing a line — caught and re-added 8 real vars my first draft had dropped (APP_TITLE, CENTRIFUGO_HTTP_URL_INTERNAL, ENABLE_OAUTH_SIGNUP, LOG_LEVEL, OAUTH_PROVIDER_NAME, OPENAI_API_BASE_URL, SESSION_BRIDGE_LOG_LEVEL, WEBUI_AUTH) before the first commit"
  - "Dropped 5 vars confirmed to have zero consumer anywhere in the repo: DOMAIN_URL (fully dead), HOST/PORT (docker-compose.yml hardcodes 0.0.0.0/3080 as literals — the .env versions were never read), MEMORY_API_PUBLIC_BASE (only referenced in an old Phase-1 plan file, not in any running code), BACKUP_RETENTION_WEEKLY (backup.sh only reads BACKUP_RETENTION_DAILY)"
  - "OPENAI_API_BASE_URL tagged [required] (not [optional]) — docker-compose.yml passes it to Open WebUI with NO fallback default (${OPENAI_API_BASE_URL}, no `:-`), so an operator who deletes this line breaks Open WebUI's upstream pipeline connection silently"
  - "AGENT_MENTION_ALIASES comment follows the plan's exact required wording (first-alias-is-advertised via 14-07, order is cosmetic) but with zero concrete deployment names — verified via grep before every commit per the critical gate warning"

patterns-established:
  - "For any future .env.example edit: cross-check docker-compose.yml's ${VAR} list (not just app config.py) before removing a var — compose-level defaults and app-level defaults can disagree, and only compose's substitution actually reaches a running container"

requirements-completed: [PORT-02]

# Metrics
duration: 19min
completed: 2026-07-12
---

# Phase 14 Plan 04: OSS .env.example Template Summary

**Rewrote the 121-var, Phase-1-chronology root `.env.example` into a slim 6-section (D-06), fully `[required]`/`[optional]`-tagged, brand-free operator-fillable template — closing the PORT-02 gap where 11 vars (including the two boot-fatal OAuth vars) had no template line at all.**

## Performance

- **Duration:** 19 min
- **Started:** 2026-07-12T03:47:48Z
- **Completed:** 2026-07-12T04:07:00Z
- **Tasks:** 2
- **Files modified:** 4 (+ 1 deleted)

## Accomplishments
- Root `.env.example` reorganized from a 324-line, phase-history-ordered file into 6 concern-grouped sections: Required — minimal boot, LLM provider keys, Domain / public URLs, OAuth identity (REQUIRED — crash-at-boot), Optional integrations, SaaS-only
- All 11 vars that previously had zero template line now documented with a neutral placeholder: `XBRAIN_BASE_DOMAIN`, `APP_PUBLIC_URL`, `CORS_ALLOWED_ORIGIN_REGEX`, `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL`, `LIBRECHAT_ALLOWED_DOMAINS`, `BRIDGE_BASE_URL`, `APP_TEAMS_URL`, `LIBRECHAT_MEMORY_API_BASE`, `CENTRIFUGO_ALLOWED_ORIGINS`, `WEBUI_URL`
- Previously-hidden config.py-default-only vars documented: `MINIO_URL`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`/`MINIO_BUCKET`, `QDRANT_COLLECTION`, `GITHUB_FALLBACK_TOKEN`, `AGENT_RUNTIME_INTERNAL_URL`, `GITHUB_CATALOG_ENABLED`/`GITHUB_CATALOG_CONCURRENCY`/`GITHUB_CATALOG_README_CHARS`
- OAuth section header states the boot-crash consequence verbatim ("CRASH AT BOOT (by design)"), reinforced by 14-01's `field_validator` and 14-03b's `make env-check`
- `CORS_ALLOWED_ORIGIN_REGEX` carries the required widening warning ("CORS-blocked" / do-not-wildcard) directly above the var
- `AGENT_MENTION_ALIASES` documented with the neutral `agent` default, a neutral multi-alias example (`agent,ai,assistant`), the D-08 "add alongside, don't replace" guidance, and the 14-07 first-alias-is-advertised note — with **zero** concrete deployment brand named, verified by grep before every commit (the exact trap the critical gate warning called out)
- Every var in the file carries an inline `# [required]` or `# [optional]` tag plus a short description (28 `[required]`, 116 `[optional]` after the final pass)
- `infrastructure/.env.example` deleted after folding its one unique note (brain-janitor's 30-day hard-purge retention pin) into the root file's Neo4j group
- All three per-service `.env.example` templates (memory-api, librechat-bridge, openwebui-pipeline) neutralized (already brand-free, confirmed via grep) and updated to reference the repo-root file as canonical
- `apps/memory-api/.env.example` gained `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` placeholder lines so a developer running memory-api standalone doesn't hit an unexplained fail-fast crash from the 14-01 `field_validator`
- Zero occurrences of `grooveos`/`GrooveOS`/`aibrussels` in any of the 4 touched files (verified by grep before both commits, per the critical gate warning about the earlier plan draft's mistake)

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite root .env.example — slim, grouped, documented, neutral** - `575fe45` (feat)
2. **Task 2: Delete vestigial infrastructure/.env.example + neutralize per-service templates** - `f576e4f` (feat)

**Plan metadata:** SUMMARY commit follows this document.

## Files Created/Modified
- `.env.example` - Rewritten from 324 lines / 121 vars organized by implementation-phase history into 251 lines / ~150 vars organized by 6 D-06 concern sections, every var tagged and commented, zero brand strings, all 11 new + previously-hidden vars documented
- `apps/memory-api/.env.example` - Added header pointing to the repo-root canonical template; added `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` placeholder lines
- `apps/librechat-bridge/.env.example` - Header updated to reference the repo-root canonical template (already brand-free)
- `apps/openwebui-pipeline/.env.example` - Header updated to reference the repo-root canonical template (already brand-free)
- `infrastructure/.env.example` - Deleted (vestigial; its one unique note folded into the root file first)

## Decisions Made
- Kept every var actually consumed by `docker-compose.yml` or a service's Settings class, reorganized into the plan's 6 sections, rather than treating the plan's per-section example lists as an exhaustive delete-everything-else contract — this was necessary to satisfy the plan's own must_haves truth ("operator boots a working install... without opening any source file"). Dropping vars like `JWT_SECRET`, `MONGO_URI`, `POSTGRES_USER`, `CREDS_KEY`/`CREDS_IV`, or `OPENAI_API_BASE_URL` would have broken LibreChat/Open WebUI boot despite technically satisfying every literal acceptance grep.
- Cross-checked every var name against `infrastructure/docker-compose.yml`'s `${VAR}` substitutions (ground truth) rather than trusting the plan's list or memory. This caught 8 vars my first draft had dropped in error (`APP_TITLE`, `CENTRIFUGO_HTTP_URL_INTERNAL`, `ENABLE_OAUTH_SIGNUP`, `LOG_LEVEL`, `OAUTH_PROVIDER_NAME`, `OPENAI_API_BASE_URL`, `SESSION_BRIDGE_LOG_LEVEL`, `WEBUI_AUTH`) — re-added all 8 before the Task 1 commit via a systematic old-vars-vs-new-vars diff.
- `OPENAI_API_BASE_URL` tagged `[required]` rather than `[optional]` — `docker-compose.yml` passes it to Open WebUI with no `:-` fallback, so omitting the line would silently break Open WebUI's pipeline connection.
- Dropped 5 vars after confirming zero consumers anywhere in the repo (not just docker-compose.yml, also grepped app code and scripts): `DOMAIN_URL`, `HOST`, `PORT` (docker-compose hardcodes `0.0.0.0`/`3080` as YAML literals — the `.env` versions of `HOST`/`PORT` were never actually read), `MEMORY_API_PUBLIC_BASE` (only referenced in a stale Phase-1 plan doc), `BACKUP_RETENTION_WEEKLY` (backup.sh only reads `_DAILY`).
- Followed the plan's exact prescribed wording for the AGENT_MENTION_ALIASES comment and the OAuth section's crash-consequence framing verbatim, since both were spelled out in the plan's `<action>` block as load-bearing text for downstream acceptance checks (this plan's own and 14-06's).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] First draft of the root .env.example dropped 8 vars that ARE consumed by docker-compose.yml**
- **Found during:** Task 1, post-write verification (before the first commit)
- **Issue:** My initial rewrite followed the plan's per-section var lists literally, which — being representative examples of the NEW/previously-hidden vars this phase adds — do not mention several pre-existing vars that `docker-compose.yml` still requires: `APP_TITLE`, `CENTRIFUGO_HTTP_URL_INTERNAL`, `ENABLE_OAUTH_SIGNUP`, `LOG_LEVEL`, `OAUTH_PROVIDER_NAME`, `OPENAI_API_BASE_URL` (no compose-level default), `SESSION_BRIDGE_LOG_LEVEL`, `WEBUI_AUTH`. Shipping without these would have technically passed every literal acceptance grep in the plan while silently breaking Open WebUI's pipeline connection (`OPENAI_API_BASE_URL` has no fallback) and losing documented control over LibreChat/Open WebUI toggles.
- **Fix:** Ran a systematic `comm` diff between the old file's var names and the new draft's var names, confirmed each dropped var's actual consumer via `docker-compose.yml` grep, and re-added all 8 with tags and one-line comments in the appropriate sections before committing.
- **Files modified:** `.env.example`
- **Verification:** Second `comm -23` diff pass confirmed only 5 vars remained dropped, all independently confirmed dead (see Decisions Made). All Task 1 acceptance greps re-run and passing.
- **Committed in:** `575fe45` (Task 1 commit — caught before commit, so the committed version already includes the fix)

---

**Total deviations:** 1 auto-fixed (a self-caught omission during my own drafting, corrected before the first commit — not an issue with the plan itself).
**Impact on plan:** No scope creep — every re-added var was already present in the pre-existing root `.env.example` and is actively read by `docker-compose.yml`; this was a completeness fix to my own draft, not new functionality.

## Issues Encountered

None beyond the self-caught omission above.

## User Setup Required

None — this plan only edits documentation/template files. No `.env` (the real, git-ignored file) was touched, and no service configuration changed. The existing DEPLOY-PREREQ values recorded in 14-01-SUMMARY.md and 14-03b-SUMMARY.md still apply to the next `make deploy` (VM `.env`), which this plan's `.env.example` rewrite exists to help future operators fill in without reading source.

## Next Phase Readiness
- PORT-02 is complete: the root `.env.example` is the single, slim, brand-free, fully-documented surface an operator fills to boot xbrain pointed at their own domain
- 14-05 and 14-06 can build on this file as the authoritative "what needs to be in `.env`" reference — 14-06's acceptance check (f) (`grep -c 'grooveos' .env.example` == 0) is satisfied
- No blockers identified for 14-05/14-06

---
*Phase: 14-portability-foundation*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 4 modified files (`.env.example`, `apps/memory-api/.env.example`, `apps/librechat-bridge/.env.example`, `apps/openwebui-pipeline/.env.example`) verified present on disk; `infrastructure/.env.example` confirmed deleted; both task commits (`575fe45`, `f576e4f`) verified present in git log. No missing items.

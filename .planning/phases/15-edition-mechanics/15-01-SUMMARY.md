---
phase: 15-edition-mechanics
plan: 01
subsystem: infra
tags: [docker-compose, profiles, minio, neo4j, backup, boot-wiring]

# Dependency graph
requires:
  - phase: 14-portability-foundation
    provides: neutral fallbacks, nginx templates, QDRANT_COLLECTION alignment, the fail-fast field_validator pattern this phase's sibling plan (15-02) follows
provides:
  - A `depends_on` graph in infrastructure/docker-compose.yml that is legal under the 15-03 profile table (no untagged-core -> tagged edges left)
  - A single untagged-core `minio` service (renamed from `langfuse-minio`) that memory-api, mcp-deck and Langfuse all resolve identically
  - Mongo-optional infrastructure/backup/backup.sh that no longer takes Postgres+Qdrant backups down with it when LibreChat Mongo is absent or unreachable
affects: [15-02, 15-03, 15-04, edition-mechanics, backup-restore, media-upload]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Compose depends_on across the future profile boundary is illegal and must be cut before profiles: tags are applied (15-03) — proven with real `docker compose config` output, not YAML greps"
    - "A shared MinIO/S3 identity (MINIO_ENDPOINT/URL/ACCESS_KEY/SECRET_KEY) must resolve identically across every consumer service — same D-15-04 discipline as QDRANT_COLLECTION"
    - "Optional external dependency in a shell script under `set -e`: retry N times with backoff, then emit a grep-able WARN SKIP line and continue, rather than aborting the whole script"

key-files:
  created: []
  modified:
    - infrastructure/docker-compose.yml
    - infrastructure/backup/backup.sh
    - apps/memory-api/app/routes/admin_wipe.py
    - apps/mcp-deck/app/main.py
    - .env.example

key-decisions:
  - "Removed exactly 3 depends_on edges (memory-api->neo4j, brain-janitor->neo4j, xbrain-backup->librechat-mongo); left graphiti-service->neo4j untouched (same-profile, legal) per 15-CONTEXT.md D-15-03"
  - "Promoted the single existing MinIO instance to untagged core and renamed it minio (container xbrain-minio); did NOT create a second MinIO instance and did NOT rename the langfuse_minio_data volume (data preservation)"
  - "backup.sh: Mongo step retries 3x with 10s backoff before skipping, to distinguish a genuinely absent Mongo (saas profile off) from a real outage; skip is a grep-able 'WARN SKIP' line, not a silent success"
  - "Found and fixed a latent .env.example bug (not caused by this task, but blocking its own acceptance proof): docker compose's env-file parser does not strip inline # comments on a genuinely blank value, so MINIO_URL/MINIO_ACCESS_KEY/MINIO_SECRET_KEY resolved to the literal string '# [optional]' instead of empty, defeating both get_minio_client()'s fail-soft check and docker-compose.yml's ${MINIO_URL:-...} core default — a real break of /v1/media/upload's fallback path"

patterns-established:
  - "Pattern: any variable in .env.example intended to be genuinely blank must NOT have a trailing inline comment on the same line — move the annotation to a comment line above instead"

requirements-completed: [EDIT-01]

# Metrics
duration: 24min
completed: 2026-07-12
---

# Phase 15 Plan 01: Profile-Safe Depends_on Graph + Core MinIO Summary

**Cut the 3 illegal cross-profile `depends_on` edges (memory-api/brain-janitor -> neo4j, xbrain-backup -> librechat-mongo), promoted the single MinIO instance to the untagged core as `minio`, and made backup.sh degrade gracefully when Mongo is absent — the Wave-0 prerequisite that lets `docker compose config --profiles` parse at all once 15-03 applies profile tags.**

## Performance

- **Duration:** 24 min
- **Started:** 2026-07-12T13:00Z (approx.)
- **Completed:** 2026-07-12T13:20Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- `docker compose config` resolves cleanly with zero `depends_on` edges crossing the future untagged-core/`integrations`/`ops`/`saas` boundary — verified via real `docker compose config --format json`, not YAML greps
- `NEO4J_URI`/`NEO4J_PASSWORD` and `LIBRECHAT_MONGO_URI` env vars are untouched — the app still connects when the dependency IS present; only the boot-ordering edge was removed
- `graphiti-service`'s legitimate same-profile `neo4j` edge survives intact
- Single MinIO instance (`xbrain-minio`, image `cgr.dev/chainguard/minio`) promoted to untagged core, boots and reaches `healthy` in a real `docker compose up -d minio`, publishes no host port
- `memory-api`, `mcp-deck` and `langfuse`/`langfuse-worker` all resolve the identical `minio:9000` endpoint — D-15-04 "one identity, every edition" discipline applied to MinIO the same way it was already applied to `QDRANT_COLLECTION`
- `langfuse_minio_data` volume left unrenamed — existing prod media/decks/Langfuse data is not orphaned by this rename
- `backup.sh` no longer takes Postgres + Qdrant backups down with it when Mongo is unreachable: 3x retry (10s backoff) then a grep-able `WARN SKIP`, exit 0 either way (verified with a stubbed run)
- Zero remaining references to the old `langfuse-minio` hostname anywhere in shipped source (`apps/`, `infrastructure/`, `.env.example`)

## Task Commits

Each task was committed atomically:

1. **Task 1: Cut the three illegal depends_on edges + make backup.sh Mongo-optional** - `dcc0220` (fix)
2. **Task 2: Promote MinIO into the untagged core — rename langfuse-minio -> minio, preserve the volume** - `8c2f960` (feat)

**Plan metadata:** (this commit, made after this SUMMARY)

## Files Created/Modified
- `infrastructure/docker-compose.yml` - removed 3 `depends_on` edges (memory-api, brain-janitor, xbrain-backup); renamed `langfuse-minio` service/container to `minio`/`xbrain-minio`; updated 8 in-file references (memory-api, mcp-deck, langfuse, langfuse-worker env vars + depends_on)
- `infrastructure/backup/backup.sh` - Mongo dump step made optional: skips immediately if `LIBRECHAT_MONGO_URI` is unset, retries 3x with 10s backoff before skipping on failure, `WARN SKIP` line on skip
- `apps/memory-api/app/routes/admin_wipe.py` - stale `MINIO_ENDPOINT` fallback default `langfuse-minio:9000` -> `minio:9000`
- `apps/mcp-deck/app/main.py` - same stale default fixed
- `.env.example` - `MINIO_ENDPOINT` default updated to `minio:9000`; removed inline `# [optional]` comments from `MINIO_URL`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` (see Deviations)

## Decisions Made
- Kept the plan's exact comment blocks for the two `neo4j` edge removals and the `xbrain-backup` edge removal, verbatim as specified in 15-01-PLAN.md
- Reworded the historical-context sentence in the new `minio` service's section comment (which the plan specified verbatim) to avoid the literal substring "langfuse-minio", since the plan's own acceptance criterion #5 requires `grep -rn "langfuse-minio"` to return zero matches in shipped source — the plan's task-2 action text and its own acceptance criterion were in direct conflict (Rule 1 — treated as a plan bug, fixed inline, not escalated)
- `infrastructure/backup/Dockerfile` invokes `backup.sh` via `cron` (daily 02:00 UTC, `crontab` entry `0 2 * * * root /scripts/backup.sh`), not a one-shot script — so a boot-time Mongo miss self-heals automatically on the next daily run; no restart-loop risk

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's own section-comment text collided with its own acceptance criterion**
- **Found during:** Task 2 (MinIO promotion)
- **Issue:** 15-01-PLAN.md's Task 2 action step 1 specifies a verbatim comment block containing the phrase "It was named `langfuse-minio` because...", but the same plan's acceptance criterion #5 requires `grep -rn "langfuse-minio" apps/ infrastructure/ .env.example` to return zero matches. Following the verbatim text as written would fail the plan's own verification.
- **Fix:** Reworded the sentence to preserve the historical explanation without the literal old identifier: "It used to carry Langfuse's name (Langfuse was the first consumer to add it), which made it LOOK like an `integrations` service."
- **Files modified:** infrastructure/docker-compose.yml
- **Verification:** `grep -rn "langfuse-minio" --include=*.py --include=*.yml --include=*.yaml --include=*.sh apps/ infrastructure/ .env.example` now exits 1 (no matches)
- **Committed in:** 8c2f960 (Task 2 commit)

**2. [Rule 1/2 - Bug / Missing critical functionality] `.env.example` inline comments broke MinIO's core fallback defaults**
- **Found during:** Task 2, while proving acceptance criterion #2 (`MINIO_URL` must resolve to exactly `http://minio:9000`)
- **Issue:** `docker compose --env-file .env.example config` resolved `memory-api`'s `MINIO_URL`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` to the literal string `"# [optional]"` instead of empty. Root cause, confirmed with an isolated minimal reproduction (`FOO=    # comment` -> `FOO` resolves to `"# comment"`, not empty; `FOO=bar    # comment` -> `FOO` resolves to `"bar"` correctly): Docker Compose's `.env`-file parser only strips inline `#` comments when there is a non-empty token before the comment. For a genuinely blank value followed by whitespace and a trailing `# [optional]` annotation, the comment text itself becomes the resolved value. This defeated `get_minio_client()`'s own fail-soft empty-check (`if not settings.MINIO_URL or ...`) AND `infrastructure/docker-compose.yml`'s `${MINIO_URL:-http://${MINIO_ENDPOINT:-minio:9000}}` core default — a real functional break of the promised-core `/v1/media/upload` capability in any install booted straight off `.env.example`, independent of and pre-existing before this plan's MinIO rename.
- **Fix:** Removed the trailing inline `# [optional]` comment from exactly the three blank-by-design vars this touches (`MINIO_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`), moving the explanation into a new comment line above the block. Did NOT sweep the rest of `.env.example` for the same latent issue on other blank vars — out of this task's declared scope; flagged below for a future pass.
- **Files modified:** .env.example
- **Verification:** `docker compose --env-file .env.example config --format json` now resolves `memory-api.environment.MINIO_URL` to exactly `http://minio:9000`, `MINIO_ACCESS_KEY` to `minio`, `MINIO_SECRET_KEY` to the placeholder value from `MINIO_ROOT_PASSWORD`
- **Committed in:** 8c2f960 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 plan self-contradiction, 1 pre-existing-but-blocking bug exposed by this task's own acceptance criteria)
**Impact on plan:** Both fixes were required to make the plan's own stated acceptance criteria true against real `docker compose` output. No scope creep beyond what was needed to prove Task 2's `must_haves`.

## Issues Encountered
None beyond the two items documented above under Deviations.

## Known Stubs
None.

## Threat Flags
None — the threat register in 15-01-PLAN.md (T-15-01-01 no host port on `minio`, T-15-01-02 empty `MINIO_ROOT_PASSWORD` pre-existing accept, T-15-01-03 backup.sh silent-skip mitigated via retry + WARN SKIP) fully covers the surface this plan touches. No new network endpoint, auth path, or schema change was introduced.

## DEPLOY-PREREQ (record per plan's `<output>` instruction)

**On the next VM deploy, this is a rename on a live stack.** The old `xbrain-langfuse-minio` container will become an orphan on the prod VM and will keep holding the volume mount point and the `langfuse-minio` DNS name until removed. The deploy MUST run:
```bash
docker compose up -d --remove-orphans
```
(or `docker rm -f xbrain-langfuse-minio` first, then a normal `up -d`). The `langfuse_minio_data` volume itself is unchanged — no data moves, no data is lost — only the container name and service DNS alias change.

**Answer to the Task 1 `<read_first>` question about `infrastructure/backup/Dockerfile`:** the container invokes `backup.sh` via `cron`, not a one-shot script — `CMD ["sh", "-c", "touch /var/log/cron.log && cron && tail -F /var/log/cron.log"]`, with `crontab` entry `0 2 * * * root /scripts/backup.sh >> /var/log/cron.log 2>&1`. This means a boot-time Mongo miss (or any transient Mongo outage) self-heals automatically on the next daily 02:00 UTC run — there is no restart-loop risk from the Mongo-optional change, and the `WARN SKIP` line lands in `/var/log/cron.log` on each affected run for operator visibility.

## Deferred (out of this plan's scope, logged for a future pass)

- `.env.example` likely has the same inline-comment-on-blank-value defect on other vars beyond the three fixed here (e.g. `GCS_BACKUP_BUCKET`). Not fixed — out of this plan's declared `files_modified` scope beyond what blocked this plan's own acceptance criteria. Worth a dedicated cleanup pass.

## Next Phase Readiness
- `docker compose config --profiles` (once 15-03 applies `profiles:` tags) will no longer abort on the three edges this plan removed — 15-03 is unblocked.
- The core `minio` service is in place and ready for 15-03 to leave untagged; 15-02's `EDITION` router-gating work is independent of this plan and unaffected.
- No blockers identified for 15-02, 15-03, or 15-04.

---
*Phase: 15-edition-mechanics*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 5 modified files found on disk; both task commits (`dcc0220`, `8c2f960`) found in git log.

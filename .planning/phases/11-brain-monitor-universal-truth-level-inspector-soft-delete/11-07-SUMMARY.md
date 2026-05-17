---
phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
plan: 11-07
subsystem: infra

tags: [docker-compose, cron, retention, soft-delete, postgres, qdrant, neo4j, brain-monitor, sidecar, asyncpg, structlog]

# Dependency graph
requires:
  - phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
    provides:
      - 11-01 / migration 0017 — deleted_at column on every brain-tracked entity (the WHERE predicate the janitor purges on)
      - 11-03 / NativeProvider soft-delete payload — Qdrant points the janitor reconciles via hard delete after the retention window
      - 11-05 / DELETE endpoint — the user action that sets deleted_at; the janitor closes the loop at day 30
  - phase: 01-core-infrastructure
    provides:
      - PostgreSQL service + DATABASE_URL contract (xbrain_pg volume, healthcheck)
      - Qdrant service + QDRANT_URL contract (xbrain_qdrant volume, healthcheck)
  - phase: 03-graph-extraction
    provides:
      - Neo4j Community service + NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD contract (healthcheck)
provides:
  - apps/brain-janitor/ Python container — boots, runs one purge cycle immediately, sleeps until next 03:00 UTC, repeats forever
  - PG purge for 6 tables (memory_items, conversations, messages, team_messages, tasks, contacts) with audit_log batch summary row
  - Qdrant point delete fan-out for purged memory_items (best-effort, try/except, PG remains source of truth)
  - Neo4j DETACH DELETE fan-out for purged memory_items (best-effort, async driver)
  - docker-compose `brain-janitor` service entry — depends_on postgres+qdrant+neo4j healthy, logging anchor, 25h-stale healthcheck on /tmp/brain-janitor-alive
  - infrastructure/.env.example Phase 11 informational section — documents reused env vars + no-touch RETENTION_DAYS=30 contract
affects:
  - 11-09 (verify-phase11.sh can read xbrain-brain-janitor logs for `brain_janitor.run_complete` within seconds of `docker compose up -d brain-janitor`)
  - operator runbook — `docker compose ps brain-janitor` exposes daily-cycle liveness via the (healthy) status flag

# Tech tracking
tech-stack:
  added:
    - "asyncpg>=0.30 — already used by other services; introduced here for brain-janitor pool"
    - "qdrant-client==1.17.1 pinned — matches the Phase 1 Qdrant server image tag exactly"
    - "neo4j>=6.1 async driver — replaces deprecated neo4j-driver; uses AsyncGraphDatabase"
    - "pydantic-settings>=2.6 — env-driven Settings class, no manual os.getenv plumbing"
    - "structlog>=25 — same structured-JSON log style as the rest of the Python services"
  patterns:
    - "Build context = repo root (`context: ..`) + dockerfile path relative to that root (`apps/brain-janitor/Dockerfile`) — mirrors granola-sync exactly so shared packages/memory-models can be COPY'd into the image layer before the service code"
    - "Sentinel-file healthcheck on /tmp/<svc>-alive with a mtime-freshness window matching the cycle period — granola-sync uses 600s for a 5-min poll; brain-janitor uses 90000s (~25h) for the 24h cycle"
    - "Logging anchor `*default-logging` reuse — every new service must reference it so the 100m × 3-file cap that was added VM-wide today stays enforced (no per-container drift)"
    - "Boot-time eager run + daily scheduler — the first cycle fires on container start so verify scripts get instant evidence without waiting for 03:00 UTC; the loop then sleeps to the next 03:00 boundary using UTC datetime arithmetic (DST-immune)"
    - "Fan-out side-effects (Qdrant + Neo4j) wrapped in try/except around the PG-source-of-truth — partial failure logs a warning and is reconciled on next cycle; PG is never blocked by Qdrant/Neo4j unavailability"

key-files:
  created:
    - "apps/brain-janitor/Dockerfile — Python 3.12-slim, repo-root context, installs packages/memory-models first then brain-janitor"
    - "apps/brain-janitor/pyproject.toml — name xbrain-brain-janitor, deps asyncpg/qdrant-client/neo4j/structlog/pydantic-settings/xbrain-memory"
    - "apps/brain-janitor/app/__init__.py"
    - "apps/brain-janitor/app/config.py — pydantic-settings Settings (DATABASE_URL, QDRANT_URL, QDRANT_COLLECTION, NEO4J_URI/USER/PASSWORD, RETENTION_DAYS=30, LOG_LEVEL)"
    - "apps/brain-janitor/app/main.py — asyncio entry; _next_run_at(now, hour=3) helper; eager run_once on boot; sleep-until-03:00 loop; structlog wired to stdout; sentinel /tmp/brain-janitor-alive touched after each cycle; broad except keeps the loop alive across run failures"
    - "apps/brain-janitor/app/pg_purger.py — _pg_url helper strips asyncpg URL prefix; PURGE_TABLES list (memory_items, conversations, messages, team_messages, tasks, contacts); purge_pg() runs all DELETEs in one transaction and writes a single audit_log summary row"
    - "apps/brain-janitor/app/qdrant_purger.py — purge_qdrant(url, collection, mem_ids) calls qdrant_client.delete with PointIdsList; partial-failure tolerant"
    - "apps/brain-janitor/app/neo4j_purger.py — purge_neo4j(uri, user, pwd, mem_ids) runs MATCH (n {external_id: $id}) DETACH DELETE n via AsyncGraphDatabase per id"
    - "apps/brain-janitor/tests/__init__.py"
    - "apps/brain-janitor/tests/conftest.py"
    - "apps/brain-janitor/tests/test_main.py — covers run_once happy path + boot eager-run failure path + _next_run_at hour boundary"
    - "apps/brain-janitor/tests/test_pg_purger.py — covers DELETE RETURNING + audit_log INSERT against a fake asyncpg connection"
    - "apps/brain-janitor/tests/test_qdrant_purger.py — covers delete fan-out + try/except on transport failure"
    - "apps/brain-janitor/tests/test_neo4j_purger.py — covers per-id Cypher dispatch + session lifecycle"
    - ".planning/phases/11-brain-monitor-universal-truth-level-inspector-soft-delete/11-07-SUMMARY.md"
  modified:
    - "infrastructure/docker-compose.yml — appended brain-janitor service block after granola-sync (lines 917–950 after edit); references *default-logging anchor; depends_on postgres+qdrant+neo4j service_healthy; mem_limit 192m; healthcheck on /tmp/brain-janitor-alive with 90000s freshness window (interval 1h, retries 3, start_period 60s)"
    - "infrastructure/.env.example — appended Phase 11 brain-janitor informational section documenting reused env vars (DATABASE_URL/QDRANT_URL/QDRANT_COLLECTION/NEO4J_*) and the no-touch RETENTION_DAYS=30 contract pinned to CONTEXT.md"

key-decisions:
  - "Service entry placed after granola-sync, not earlier — chronological (Phase 7 then Phase 11) reads naturally and keeps the diff scoped to an append, no risk of nudging line numbers other services depend on in scripts"
  - "Build context = `..` + dockerfile = `apps/brain-janitor/Dockerfile` (no leading `../`) — granola-sync's pattern, confirmed at line 888 of the hardened compose. The orchestrator hint about `../apps/brain-janitor/Dockerfile` was a red herring; dockerfile path is relative to the build context, so the prefix `..` is wrong here"
  - "logging: *default-logging reused (not inlined) — the 100m × 3 cap that was added VM-wide today (post-clickhouse 17GB incident) is the project policy; new services must reference the anchor so the cap stays a single source of truth"
  - "Healthcheck freshness window = 90000s (~25h), interval = 1h, start_period = 60s — interval 1h matches the 24h cycle granularity (no point checking faster than the cycle); 90000s = 24h + 1h grace for clock skew + cycle execution time; start_period 60s covers the boot-time eager run on slow VMs"
  - "depends_on uses condition: service_healthy for all three backends — Phase 1/3 healthchecks already exist (postgres pg_isready, qdrant /healthz, neo4j wget on 7474), so the janitor will not boot into a half-up stack and corrupt state mid-purge"
  - "image: xbrain/brain-janitor:phase11 tag — matches the project convention (granola-sync:phase7, mcp-brain:phase8, session-bridge:phase9) so operators can grep `docker images | grep xbrain` and immediately see which phase introduced what"
  - "RETENTION_DAYS hard-coded as `\"30\"` in the compose environment block AND documented in .env.example as no-touch — the value is part of the public soft-delete contract (DELETE returns within 30d → 200; outside → 410 Gone in 11-05); a stray .env override would silently break the UI contract"
  - "mem_limit 192m matches granola-sync — the janitor's working set is small (one asyncpg pool of 2 conns, sequential Qdrant batch, per-id Neo4j sessions); 192m gives generous headroom without competing for the e2-medium's 4GB"
  - "Plan Task 3 was renamed in commit subject to `chore(11-07)` (not `chore(infra)`) so the (phase-plan) scope tag in the GSD commit-validation hook matches the rest of the plan's commits and roadmap progress can be derived from `git log --grep='(11-07)'`"
  - "Pre-existing apps/brain-janitor/ files (Dockerfile, pyproject, app/, tests/) — already shipped on main as 9ef53e2 + e24e311 cherry-picks; this SUMMARY ratifies them as part of the same plan and the only new commit on this branch is the compose/env wiring"

patterns-established:
  - "Daily-cycle sidecar pattern: pydantic-settings → asyncio entry → eager `run_once()` on boot → `_next_run_at(now, hour=N)` UTC helper → infinite loop with broad except → /tmp/<svc>-alive sentinel touch after each cycle → docker healthcheck reads sentinel mtime with cycle-period + grace window"
  - "Multi-store purger fan-out: PG transaction (source of truth, atomic with audit_log INSERT) → best-effort Qdrant batch (try/except, log + continue) → best-effort Neo4j per-id Cypher (try/except, log + continue) — partial failure is tolerated; PG-side state is always advanced and downstream stores reconcile on next cycle"
  - "New service compose-entry checklist (post-2026-05-17 hardening): logging: *default-logging (mandatory), mem_limit set, depends_on with service_healthy conditions, healthcheck with start_period covering boot cost, image tag follows xbrain/<svc>:phase<N> convention"

requirements-completed: [BMO-08]

# Metrics
duration: 14min
completed: 2026-05-17
---

# Phase 11 Plan 11-07: brain-janitor cron container Summary

**Daily 03:00 UTC hard-purge sidecar: PG-as-source-of-truth multi-store fan-out (PG → Qdrant → Neo4j), sentinel-file healthcheck, logging-anchor compliant, wired into the hardened docker-compose without touching the new x-default-logging anchor or the mcp-brain Python socket-probe healthcheck that were added today.**

## Performance

- **Duration:** ~14 min (compose+env wiring only; apps/brain-janitor/ files were shipped on main as predecessor commits)
- **Started:** 2026-05-17 (post fast-forward of the worktree onto local main HEAD 9ef53e2)
- **Completed:** 2026-05-17
- **Tasks:** 1 new commit on this branch (Task 3 — compose+env), 2 prior commits ratified (Tasks 1+2 — code)
- **Files created on this branch:** 1 (this SUMMARY.md)
- **Files modified on this branch:** 2 (infrastructure/docker-compose.yml, infrastructure/.env.example)
- **Files inherited from predecessor commits:** 14 (apps/brain-janitor/*)

## Commits

| Hash      | Subject                                                                  | Author              | Notes                                                |
| --------- | ------------------------------------------------------------------------ | ------------------- | ---------------------------------------------------- |
| `e24e311` | feat(brain-janitor): Dockerfile + pyproject + config skeleton            | predecessor on main | Task 1 (cherry-picked into main from earlier work)   |
| `9ef53e2` | feat(brain-janitor): pg/qdrant/neo4j purgers + 03:00 UTC scheduler       | predecessor on main | Task 2 (cherry-picked into main from earlier work)   |
| `5a4e2f0` | chore(11-07): add brain-janitor service to docker-compose + healthcheck  | this executor       | Task 3 — the only new commit on this worktree branch |

The previous executor's third commit was rejected because it was based on a stale branch (62 commits behind main) and would have clobbered the new x-default-logging YAML anchor + the mcp-brain Python socket healthcheck added today. This executor fast-forwarded the worktree onto local main first, then applied the compose entry on top of the hardened file — anchor and mcp-brain healthcheck preserved unchanged (verified by parsing the YAML post-edit: anchor still at line 10, mcp-brain healthcheck still using `["CMD", "python", "-c", "import socket,sys; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', 8104)); s.close()"]`).

## What shipped

### apps/brain-janitor/ Python package (predecessor commits)

- **Dockerfile** — Python 3.12-slim base, build context is the repo root so the shared `packages/memory-models/` package can be COPY'd as the first layer (best Docker cache usage); installs brain-janitor's pyproject in editable mode; CMD `python -m app.main`.
- **pyproject.toml** — declares deps `asyncpg>=0.30`, `qdrant-client==1.17.1` (pinned to match the Phase 1 Qdrant server tag), `neo4j>=6.1` (async driver), `structlog>=25`, `pydantic-settings>=2.6`, and the local `xbrain-memory` package; setuptools build backend with `packages = ["app"]`.
- **app/config.py** — pydantic-settings `Settings` class with `DATABASE_URL`, `QDRANT_URL` (defaults to `http://qdrant:6333`), `QDRANT_COLLECTION` (defaults to `memory_items`), `NEO4J_URI` (defaults to `bolt://neo4j:7687`), `NEO4J_USER` (defaults to `neo4j`), `NEO4J_PASSWORD`, `RETENTION_DAYS=30`, `LOG_LEVEL=INFO`.
- **app/main.py** — `_next_run_at(now, hour=3)` computes the next UTC 03:00 strictly after `now` (DST-immune because everything is UTC); `run_once(settings)` opens an asyncpg pool (min 1, max 2), calls `purge_pg` → `purge_qdrant` (only if any memory_items were purged) → `purge_neo4j`, touches `/tmp/brain-janitor-alive`, logs `brain_janitor.run_complete` with per-table counts; `main()` configures structlog, fires one eager `run_once` (for instant verify-phase11 signal), then loops `await asyncio.sleep(next_run - now)` followed by another `run_once`, with broad `except` around each cycle so the loop survives transient failures.
- **app/pg_purger.py** — `PURGE_TABLES` list of 6 (table, scope_col) tuples; `purge_pg(pool, retention_days)` runs all `DELETE FROM <table> WHERE deleted_at < now() - $1::interval RETURNING id` inside one transaction, collects the returned UUIDs per table, then writes a single `audit_log` row with action `brain_janitor.purge` + entity_type `batch` + metadata = JSON of per-table counts.
- **app/qdrant_purger.py** — wraps `qdrant_client.delete(collection_name, points_selector=PointIdsList(points=[str(u) for u in ids]))`; try/except around the call so a Qdrant outage logs but doesn't fail the cycle.
- **app/neo4j_purger.py** — `AsyncGraphDatabase.driver` per call; `MATCH (n {external_id: $id}) DETACH DELETE n` per UUID.
- **tests/** — 4 test modules (main, pg_purger, qdrant_purger, neo4j_purger) with fakes for asyncpg pool/conn, Qdrant client, and Neo4j driver/session.

### infrastructure/docker-compose.yml (this executor's commit 5a4e2f0)

New service block appended after the granola-sync entry (compose still parses; 30 services total; brain-janitor included). Key properties:

- `build.context: ..` + `build.dockerfile: apps/brain-janitor/Dockerfile` — granola-sync mirror exactly. Path is relative to the build context (which is `..` = repo root), so the dockerfile path has no `../` prefix.
- `image: xbrain/brain-janitor:phase11` — phase-stamped tag matches project convention.
- `logging: *default-logging` — references the YAML anchor at line 10 (`{driver: json-file, max-size: 100m, max-file: 3}`). The cap is the policy added today after the clickhouse 17GB incident.
- `depends_on: { postgres, qdrant, neo4j }` each with `condition: service_healthy`. Phase 1/3 healthchecks already cover all three.
- `environment` block reuses `${DATABASE_URL}`, `${QDRANT_URL:-http://qdrant:6333}`, `${QDRANT_COLLECTION:-memory_items}`, `${NEO4J_URI:-bolt://neo4j:7687}`, `${NEO4J_USER:-neo4j}`, `${NEO4J_PASSWORD}`, with `RETENTION_DAYS: "30"` hard-coded and `LOG_LEVEL: ${LOG_LEVEL:-INFO}`.
- `mem_limit: 192m` — same as granola-sync; the working set is small.
- `healthcheck`:
  - `test: ["CMD-SHELL", "test -f /tmp/brain-janitor-alive && [ $$(($$(date +%s) - $$(stat -c %Y /tmp/brain-janitor-alive))) -lt 90000 ] || exit 1"]` — proper `$$` escaping for compose variable substitution; reads the sentinel's mtime, fails if older than 90000s (~25h, = 24h cycle + 1h grace).
  - `interval: 1h, timeout: 10s, retries: 3, start_period: 60s` — 60s start_period covers the boot-time eager run on slow VMs.

### infrastructure/.env.example (this executor's commit 5a4e2f0)

Appended a Phase 11 brain-janitor section that explicitly enumerates the reused env vars (DATABASE_URL, QDRANT_URL, QDRANT_COLLECTION, NEO4J_URI/USER/PASSWORD) and documents the no-touch `RETENTION_DAYS=30` contract pinned to Phase 11 CONTEXT.md. No new secrets introduced — the file's role is documentation for operators, not new config surface.

## Deviations from Plan

### Scope adjustments

**1. [Rule 3 - Blocking] Fast-forwarded worktree onto local main before any edit**

- **Found during:** worktree branch check at executor startup
- **Issue:** The worktree branch was 62 commits behind local main and did NOT contain `apps/brain-janitor/*` files OR the hardened docker-compose (no `x-default-logging` anchor, no mcp-brain Python socket-probe healthcheck). Applying the plan's Task 3 verbatim on this base would have produced a commit that, on merge to main, reverts the anchor and the healthcheck.
- **Fix:** `git fetch` (already in sync with origin/main) then `git merge main --ff-only` to fast-forward the worktree to HEAD `9ef53e2`. The merge cleanly took 1500+ files because the worktree branch had zero divergent commits.
- **Files modified:** worktree branch tip moved from `0b0b50d` → `9ef53e2` (fast-forward only, no merge commit, no conflicts).
- **Commit:** N/A (branch movement only)

**2. [Rule 1 - Bug] Mis-targeted Edit calls hitting the main repo path; reverted**

- **Found during:** first attempt at Task 3
- **Issue:** Two `Edit` calls were issued with the absolute path `D:/VSC/xbrain/infrastructure/...` instead of the worktree path `D:/VSC/xbrain/.claude/worktrees/agent-a09682d7d3217435b/infrastructure/...`. The Edit tool wrote the brain-janitor block + .env section into the main repo's working tree (with a duplicate, because the first call's stale file-state cache caused a second apply). The git status in the main repo showed these as uncommitted local changes.
- **Fix:** `git checkout -- infrastructure/docker-compose.yml infrastructure/.env.example` inside the MAIN repo (`D:/VSC/xbrain`), which discarded ONLY those two specific files' diffs and left the other pre-existing local modifications in the main repo (`apps/memory-api/app/deps.py`, `.claude/scheduled_tasks.lock`, `marketing-site/.firebase/hosting..cache`) untouched. Then re-applied both edits using the explicit worktree-prefixed absolute paths.
- **Files modified:** none net (main repo unchanged; worktree edits applied correctly on second try).
- **Commit:** N/A (mistake, fully reverted before any commit).

### What was NOT changed

- `infrastructure/docker-compose.yml` lines 1–915: untouched. The x-default-logging anchor at line 10 and every `logging: *default-logging` reference on prior services is unchanged. The mcp-brain Python socket-probe healthcheck (lines around 745–749) is unchanged. The append-only edit policy preserved the surface.
- `.planning/STATE.md`: not touched (per orchestrator instructions — parallel execution with 11-06 finish and 11-10 fresh, STATE owned by the orchestrator).
- `.planning/ROADMAP.md`: not touched (same reason).
- `.planning/REQUIREMENTS.md`: not touched (BMO-08 will be marked by the orchestrator's roll-up).

## Risks + Mitigations (as-shipped)

- **Risk:** `RETENTION_DAYS=30` is also defined in `app/config.py`'s default. If an operator sets a different value via `.env`, the compose file's hard-coded `"30"` wins. **Mitigation:** documented in the .env.example informational section as no-touch; if a future plan needs to make it configurable, that plan must remove the compose hard-coding AND the CONTEXT.md pin in the same change.
- **Risk:** Qdrant healthcheck on the existing compose uses `service_healthy` — confirmed by inspecting the qdrant service block (port 9100 /healthz). If a future infra plan changes Qdrant's healthcheck to a non-healthy state during failover, brain-janitor will refuse to start. **Mitigation:** acceptable trade-off — we want the janitor blocked on a degraded Qdrant rather than racing it.
- **Risk:** The 90000s healthcheck window is ~25h, so if the daily cycle slips past 25h (e.g., a 4-hour outage straddling 03:00), Docker will mark the container unhealthy for one cycle. **Mitigation:** the broad-except loop in `main.py` will fire the next cycle anyway; the healthcheck self-recovers on the next sentinel touch.

## Threat Flags

None. brain-janitor introduces no new network surface, no new auth path, no new file access pattern at a trust boundary, and no schema change. It consumes existing internal env vars and operates on tables that the Phase 11 soft-delete contract already declared as in-scope for hard purge after the retention window.

## Self-Check: PASSED

| Check                                                                   | Result                                                                |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `apps/brain-janitor/Dockerfile` present                                  | FOUND                                                                 |
| `apps/brain-janitor/app/main.py` present                                 | FOUND                                                                 |
| `apps/brain-janitor/pyproject.toml` present                              | FOUND                                                                 |
| `apps/brain-janitor/tests/` contains 4 test modules + conftest + init   | FOUND (test_main, test_pg_purger, test_qdrant_purger, test_neo4j_purger) |
| Commit `e24e311` (feat: Dockerfile + pyproject + config skeleton)        | FOUND on main                                                         |
| Commit `9ef53e2` (feat: pg/qdrant/neo4j purgers + 03:00 UTC scheduler)   | FOUND on main                                                         |
| Commit `5a4e2f0` (chore(11-07): compose + healthcheck)                   | FOUND on worktree branch                                              |
| `brain-janitor` service parses in docker-compose.yml                     | PASS (Python yaml.safe_load → 30 services, `brain-janitor` in services) |
| `logging: *default-logging` resolved to `{json-file, 100m × 3}`          | PASS                                                                  |
| `depends_on` = postgres/qdrant/neo4j with `service_healthy`              | PASS                                                                  |
| x-default-logging anchor at line 10 unchanged                            | PASS (`grep -n x-default-logging` → only line 10)                     |
| mcp-brain Python socket-probe healthcheck unchanged                     | PASS (verified by grep + visual inspection of lines 726–752)          |
| `.planning/phases/11-…/11-07-SUMMARY.md` created                         | FOUND                                                                 |

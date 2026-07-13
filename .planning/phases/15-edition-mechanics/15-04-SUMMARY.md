---
phase: 15-edition-mechanics
plan: 04
subsystem: testing
tags: [docker-compose, profiles, edition, acceptance-gate, preflight, bash, no-build-harness, arm64, outbox, neo4j]

# Dependency graph
requires:
  - phase: 15-edition-mechanics
    provides: "15-01's profile-safe depends_on graph + untagged-core `minio` rename (the Wave-0 prerequisite that lets `profiles:` parse)"
  - phase: 15-edition-mechanics
    provides: "15-02's validated Settings.EDITION + create_app(edition) CORE(33)/SAAS(2) router registry + get_driver()-gated neo4j_outbox INSERT"
  - phase: 15-edition-mechanics
    provides: "15-03's `profiles:` tags on 22/32 services + EDITION passthrough to memory-api's env block + .env.example COMPOSE_PROFILES/EDITION docs"
  - phase: 15-edition-mechanics
    provides: "15-05's bounded background reconnect_loop (expected in check g's harness: retries then one reconnect_exhausted WARNING, never blocks startup)"
  - phase: 15-edition-mechanics
    provides: "15-06's removal of require_paid_tier (CRM/Tasks now serve a default starter team)"
provides:
  - "infrastructure/scripts/verify-phase15.sh — the Phase 15 acceptance gate: 8 checks (a-h), 32 assertions, compose-layer + live-boot, asserts against real `docker compose config` output and real running containers, never a grep of docker-compose.yml"
  - "infrastructure/scripts/preflight-env.sh — deploy-time rejection of COMPOSE_PROFILES=saas + EDITION!=saas (its first cross-var relationship check)"
  - "Makefile — `make verify-phase15` target + deploy recipe's remote guard now runs preflight-env.sh over SSH against the VM's .env (one implementation of the rule, enforced both sides)"
affects: [edition-mechanics, deployment, phase-16]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Acceptance gate asserts profile membership BY NAME (diff against embedded expected sets), never by count — a two-service profile swap preserves the counts and config -q exit 0, so only a by-name diff catches it"
    - "Live no-build memory-api harness: stock python:3.12-slim + read-only repo bind mount + Dockerfile-order staging (pyproject-only pip install FIRST to dodge flat-layout 'multiple top-level packages', then copy app/ + alembic/), env lifted from `docker compose config --format json` so a compose passthrough gap breaks the check"
    - "SC#3 negative case is the load-bearing assertion: a leaked SaaS router stays MOUNTED by default, so only asserting the SaaS routes return 404 under EDITION=oss catches the leak — 'core routes work' proves nothing"
    - "Hard mount guard: after a Git-Bash bind mount, assert a known file is visible INSIDE the container and FAIL LOUDLY (never SKIP) if not — a gate that can silently mount nothing is worse than no gate"
    - "One invariant, enforced on both sides: preflight-env.sh runs locally (make preflight) AND over SSH against the VM's own .env (deploy recipe), instead of a second inline copy that drifts"

key-files:
  created:
    - infrastructure/scripts/verify-phase15.sh
    - .planning/phases/15-edition-mechanics/deferred-items.md
  modified:
    - infrastructure/scripts/preflight-env.sh
    - Makefile

key-decisions:
  - "Every profile's membership asserted BY NAME (diff), with counts (24/17/11/32) reported only as a readability aid — the diff is the real check."
  - "The COMPOSE_PROFILES/EDITION coupling is enforced in preflight-env.sh (the ops layer), NOT as a memory-api field_validator — COMPOSE_PROFILES is an orchestrator concept the app must not learn; teaching it would break standalone memory-api runs with the var merely exported."
  - "The Makefile's remote deploy guard now INVOKES preflight-env.sh over SSH against the VM's .env instead of carrying a second inline 5-var loop — one implementation, enforced on the box that actually boots the containers (the VM .env drifts from the local one)."
  - "nginx's Docker healthcheck loopback-probes with Host:127.0.0.1, which the pre-existing default_server catch-all 302-redirects to unreachable HTTPS — a non-Phase-15 finding logged to deferred-items.md. Check (f) proves SC#4 ingress resilience by probing a REAL named vhost with its own Host header instead, and reports the Docker health status as an informational NOTE, never a FAIL."
  - "media/upload and external-sessions are probed with an INVALID Bearer + X-Team-Scope (not no-auth): the required Authorization header means no-auth yields 422 (missing header), whereas an invalid token drives the auth dependency to the intended 401/403 while still proving the router is mounted."
  - "Neg-proof honesty: untagging neo4j makes the gate exit 1 via checks (b)/(c)/(d) on the core-name diff — NOT via check (f), whose boot starts only the 5 named pull-only services (none depend_on neo4j), so an untagged neo4j never starts and (f)'s deny-list is never exercised by that scenario."

patterns-established:
  - "A verifier that greps repo tokens/service names must exclude itself and must count INVOCATIONS not mentions (the recipe comment names the script too) — otherwise the gate grades itself against its own prose."
  - "The Git-Bash mount trap has TWO faces: MSYS translates a standalone /repo argv into C:/Program Files/Git/repo (breaks the mount + any docker exec path arg), AND MSYS_NO_PATHCONV=1 then stops --env-file host paths translating — so use cygpath -w for BOTH host paths and MSYS_NO_PATHCONV=1 for the container-internal targets."

requirements-completed: [EDIT-01, EDIT-02]

# Metrics
duration: ~3h (incl. one mid-Task-2 pause/resume)
completed: 2026-07-14
---

# Phase 15 Plan 04: verify-phase15.sh Acceptance Gate Summary

**The Phase 15 acceptance gate (`verify-phase15.sh`, 8 checks / 32 assertions, exits 0) built so it cannot repeat Phase 14's failure: every check asserts against real `docker compose config` output and real running containers — profile membership BY NAME, a REAL OSS-light boot to healthy with no Neo4j, a REAL memory-api that 404s the SaaS routes under EDITION=oss and reaches them (422/401) when only EDITION is flipped on the same container, and a zero-row neo4j_outbox in the exact NEO4J_URI-set/no-container state — plus a preflight invariant that rejects COMPOSE_PROFILES=saas+EDITION=oss on both the local and the VM's .env.**

## Performance

- **Duration:** ~3h (with one mid-Task-2 pause + coordinator resume)
- **Completed:** 2026-07-14
- **Tasks:** 3 (all completed)
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments

- **`verify-phase15.sh`** — 8 checks, 32 assertions, exits 0. Zero greps of `docker-compose.yml`; every assertion reads `docker compose config` output, a live HTTP status, a real SQL row count, or `docker inspect`.
  - (a) exactly 3 profiles (`integrations ops saas`, no `pro`) + no `depends on undefined service`
  - (b) bare OSS-light core = exactly 10 services, diffed BY NAME
  - (c) independent deny-list: none of the 22 opt-in names leak into the bare core
  - (d) each opt-in profile's membership (24/17/11/32) diffed BY NAME; each profile legal standalone; `COMPOSE_PROFILES=` env resolves identically to `--profile` flags
  - (e) EDITION passthrough via `config --format json`, reaches ONLY memory-api, defaults oss / flips saas; SC#5 data identity across all 5 profile combinations; no `depends_on:neo4j` on the two core services
  - (f) REAL `docker compose up` of the 5 pull-only core services to healthy; nginx ingress resilience proven; zero opt-in containers; exactly the 5 requested up
  - (g) REAL memory-api (real source, real compose-resolved env, no Neo4j): healthz 200; **waitlist & external-sessions 404 under EDITION=oss** (SC#3); flip to saas → 422/401 on the same container; **neo4j_outbox = 0** after an entities-carrying upsert while the memory_items row persists; hard mount guard
  - (h) preflight rejects saas+oss (names EDITION), accepts saas+saas and the OSS-light default; Makefile has 0 inline loops + exactly 2 preflight invocations + the remote SSH guard
- **`preflight-env.sh`** gained its first cross-var relationship check: `COMPOSE_PROFILES` contains `saas` but `EDITION != saas` → FATAL naming the fix.
- **Makefile** `deploy` recipe's remote guard now SSH-invokes `preflight-env.sh` against the VM's own `.env`; added `make verify-phase15`.
- **10 injected regressions all confirmed FAIL then reverted cleanly** (see below) — the gate is demonstrably capable of failing.

## Task Commits

Each task committed atomically:

1. **Task 1: compose-layer gate, checks (a)-(e), asserted BY NAME** — `034e8e7` (feat)
2. **Task 2: live OSS-light boot (f) + saas/EDITION preflight coupling (h) + Makefile remote guard** — `2bf6799` (feat)
3. **Task 3: live EDITION boot, negative case + outbox guard against real containers (g)** — `df7466d` (feat)

_No TDD gate applies — plan type is `execute`._

## Injected-regression results (all required to FAIL — confirmed, then reverted clean)

Task 1 (checks a-e):

| # | Injection | Result |
|---|---|---|
| 1 | Remove `profiles: ["saas"]` from `librechat-mongo` | (b) AND (c) FAIL — `librechat-mongo` leaks into the bare core. Reverted. |
| 2 | **Swap `session-bridge`↔`mcp-calendar` profiles** | (d) FAILs on BOTH the integrations and saas diffs **while every count stays 24/17** — the count-only blind spot. Reverted. |
| 3 | Hardcode `EDITION: oss` (drop `${EDITION:-oss}`) | (e)'s `EDITION=saas` assertion FAILs (`explicit_saas_edition='oss'`). Reverted. |
| 4 | Re-add `neo4j: {condition: service_healthy}` to `brain-janitor.depends_on` | (a) FAILs with `depends on undefined service "neo4j"`. Reverted. |

Task 2 (checks f, h):

| # | Injection | Result |
|---|---|---|
| 5 | Untag `neo4j` (remove `profiles: ["integrations"]`) | Gate exits 1 via **(b)/(c)/(d)** on the core-name diff. Check **(f) is NOT exercised** — its boot starts only the 5 named pull-only services, none depend_on neo4j, so an untagged neo4j never starts and its deny-list is never tripped. Recorded honestly, not as a check-(f) failure. Reverted. |
| 6 | Remove the new preflight COMPOSE_PROFILES/EDITION block | (h) FAILs — saas+default-EDITION accepted (exit 0). Reverted. |
| 7 | Restore the inline 5-var SSH loop in the deploy recipe | (h)'s Makefile assertion FAILs — `inline_loop=1 (want 0)`, two implementations of the rule again. Reverted. |

Task 3 (check g):

| # | Injection | Result |
|---|---|---|
| 8 | Move `waitlist` from `SAAS_ONLY_ROUTERS` into `CORE_ROUTERS` | (g) FAILs — `waitlist=422 under oss (expected 404)`. **The leak the gate exists to catch.** Reverted. |
| 9 | Replace the `get_driver()` outbox guard with the rejected config version (`settings.NEO4J_URI and settings.NEO4J_PASSWORD`) | (g)'s outbox assertion FAILs — `neo4j_outbox=2 (want 0)`. **Proof the LIVE gate, not just the unit test, discriminates "config present" from "Neo4j reachable".** Reverted. |
| 10 | Break the bind mount (POSIX `$PWD`, no `MSYS_NO_PATHCONV`) | Mount Source mangled to `...;C`; the **mount guard FIRES (FAIL)**, not SKIP, not pass. Control (cygpath -w + MSYS_NO_PATHCONV) passes. |

## Exact per-profile membership the gate asserts (BY NAME)

- **Core (10, untagged):** `brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant`
- **`integrations` (+14 → 24):** `agent-runtime drive-sync granola-sync graphiti-service langfuse langfuse-clickhouse langfuse-redis langfuse-worker mcp-calendar mcp-deck mcp-drive-read mcp-github neo4j searxng`
- **`saas` (+7 → 17):** `librechat librechat-bridge librechat-meili librechat-mongo openwebui openwebui-pipeline session-bridge`
- **`ops` (+1 → 11):** `xbrain-backup`
- **all three combined:** 32

## What the gate does NOT cover (not overclaimed)

- **`xbrain-backup`** (`ops` profile) — the only service in the whole compose file with no arm64 image (`google/cloud-sdk:slim`). Verified at the **config layer only** (check d); never booted on this arm64 dev host. Confirmed by config (`--profile ops config -q` exits 0), never by running it.
- **`mcp-brain`, `mcp-gateway`, `mcp-scraper`, `brain-janitor`** — all `build:` services. **Config-layer only** (checks a-e). NOT brought up by check (f), which starts only the 5 pull-only multi-arch upstream images. memory-api IS live-verified in check (g), but via the no-build harness (stock python:3.12-slim + bind-mounted source), not via `docker compose up` of its `build:` service.

## Files Created/Modified

- `infrastructure/scripts/verify-phase15.sh` — new, the acceptance gate (~570 lines: 8 check functions, an embedded python JSON helper with 5 modes, an embedded in-container outbox-test script, `boot_edition`/`code` helpers, a single EXIT-trap cleanup)
- `infrastructure/scripts/preflight-env.sh` — added the COMPOSE_PROFILES/EDITION consistency block after the existing 5-var loop
- `Makefile` — deploy recipe remote guard rewritten to SSH-invoke preflight-env.sh; added `verify-phase15` target
- `.planning/phases/15-edition-mechanics/deferred-items.md` — new, logs the pre-existing nginx healthcheck 302-to-HTTPS finding (out of this plan's scope)

## Decisions Made

See `key-decisions` in frontmatter. The load-bearing ones: membership BY NAME (not count); the coupling enforced in the ops layer not the app; the remote guard running the SAME script over SSH; the nginx healthcheck finding handled by probing a real vhost + logging to deferred-items.md rather than rewriting the nginx templates (which `<the_whole_point_of_this_plan>` forbids and which are correct).

## Deviations from Plan

### Auto-fixed / adapted (all documented, none changed product behavior)

**1. [Rule 3 - Blocking] nginx Docker healthcheck cannot go `healthy` in any isolated environment**
- **Found during:** Task 2, check (f).
- **Issue:** `xbrain-nginx`'s Docker healthcheck probes `http://127.0.0.1/nginx-health` (Host: 127.0.0.1), which the pre-existing `default_server` catch-all 302-redirects to `https://chat.$XBRAIN_BASE_DOMAIN/...`; nginx never listens on 443 here (TLS terminates externally), so `.State.Health.Status` never leaves `starting`. A naive `wait-for-healthy` check would FAIL forever. This is NOT a Phase 15 change (no Phase 15 plan touches `infrastructure/nginx/`).
- **Fix:** Check (f) proves SC#4 (ingress resilience to absent upstreams) by probing a REAL named vhost with its own `Host: chat.p15.test` header — which bypasses the catch-all exactly as a real client via Cloudflare would — and reports the Docker health status as an informational NOTE, never a FAIL. Logged the underlying finding to `deferred-items.md`. Did NOT rewrite the nginx templates (out of scope; forbidden by the plan; the templates are correct).
- **Files modified:** `infrastructure/scripts/verify-phase15.sh`, `.planning/phases/15-edition-mechanics/deferred-items.md`
- **Committed in:** `2bf6799`

**2. [Rule 1 - Bug] media/upload & external-sessions "no auth" → 422, not the plan's 401/403**
- **Found during:** Task 3, check (g).
- **Issue:** The plan's table expects `POST /v1/media/upload` (no auth) → 401/403 and `GET /v1/me/external-sessions` → 401/403. In reality the `authorization` header is a REQUIRED FastAPI `Header(...)`, so a no-auth request yields 422 (missing required header), not 401/403 — which would have made the check flaky against its own expectation.
- **Fix:** Probe these with an INVALID Bearer token + `X-Team-Scope` header. That drives the auth dependency to the intended 401 while still proving the router is mounted (not 404). The negative case is unaffected: under EDITION=oss the SaaS routes 404 even WITH a Bearer, because routing precedes auth.
- **Files modified:** `infrastructure/scripts/verify-phase15.sh`
- **Committed in:** `df7466d`

**3. [Rule 3 - Blocking] no `pkill` in python:3.12-slim; uvicorn survives SIGTERM**
- **Found during:** Task 3, `boot_edition` (the EDITION flip on the same container).
- **Issue:** The plan's `boot_edition` uses `pkill -f uvicorn`; procps is absent in the slim image. Worse, uvicorn's graceful SIGTERM shutdown hangs on the background reconnect/outbox tasks, so the old process keeps port 8000 bound and the new-edition uvicorn silently fails to bind — curl then keeps hitting the OLD edition (the saas flip appeared not to work).
- **Fix:** `boot_edition` writes the uvicorn PID to a file (`& echo $! > /tmp/uvicorn.pid; wait`), kills it with SIGKILL, and waits for the port to actually release before starting the next edition — then polls healthz. Confirmed the saas flip then reaches 422/401.
- **Files modified:** `infrastructure/scripts/verify-phase15.sh`
- **Committed in:** `df7466d`

**4. [Rule 3 - Blocking] Git-Bash path translation defeats both the mount AND the --env-file**
- **Found during:** Task 3, harness setup.
- **Issue:** A POSIX `$PWD` mount silently resolves to nothing (the documented trap); but naively setting `MSYS_NO_PATHCONV=1` then stops the `--env-file` host path (a `/tmp/...` mktemp) from being translated to a Windows path docker can open, and stops `docker exec ... /repo/...` path arguments from being usable.
- **Fix:** Use `cygpath -w` for BOTH host paths (`--env-file` and the mount source) AND `MSYS_NO_PATHCONV=1` on the `docker run`/`docker exec` calls that carry container-internal absolute paths. The hard mount guard then confirms `/repo/apps/memory-api/app/main.py` is visible; injected-regression #10 proves it FAILs loudly on a broken mount.
- **Files modified:** `infrastructure/scripts/verify-phase15.sh`
- **Committed in:** `df7466d`

---

**Total deviations:** 4 adaptations (1 pre-existing blocking finding worked around + logged, 1 plan-expectation bug, 2 environment blockers). No product/source behavior changed — all four are confined to the gate script (plus the deferred-items.md log). The plan's intent is fully met; these are how the gate had to be written to actually traverse the real deployment path on this host.

## Issues Encountered

- **`make` is not installed on this ARM64 dev host.** The `make verify-phase15` target is present and correct (recipe = `@bash infrastructure/scripts/verify-phase15.sh`, verified by check (h)'s static Makefile assertions), but was executed here via `bash infrastructure/scripts/verify-phase15.sh` directly — the exact command the target invokes. Prod/CI Ubuntu has `make`.
- All other issues are covered under Deviations — environment traps, not design ambiguities, all resolved without a checkpoint.

## Known Stubs

None — the gate wires real assertions to real compose output and real containers.

## Threat Flags

None. The plan's threat register (T-15-04-01 through T-15-04-05) is fully addressed: the mount guard FAILs (never SKIPs); `EDITION == null` is an explicit FAIL; membership is diffed by name; T-15-04-02 (saas+oss silent-404 DoS) is rejected by preflight on both the local and the VM's `.env`; the harness binds only `127.0.0.1:18000` under the isolated `-p xbrain-p15` project and is torn down by an EXIT trap. No new network endpoint, auth path, or schema change was introduced by this plan.

## Verbatim final gate output (`bash infrastructure/scripts/verify-phase15.sh`, exit 0)

```
=== Phase 15 Verification (EDIT-01 + EDIT-02) ===

(a) EDIT-01 — exactly three profiles (no pro), and no dangling depends_on
  PASS: profiles == 'integrations ops saas' (no pro); no 'depends on undefined service'

(b) EDIT-01 — bare (no-profile) OSS-light core is exactly 10 services, BY NAME
  PASS: bare-core diff empty (10/10, by name): brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant

(c) EDIT-01 — independent leak assertion: none of the 22 opt-in names in the bare core
  PASS: 0 of 22 opt-in service names present in the bare core (independent of check b)

(d) EDIT-01 — each opt-in profile's membership, BY NAME (diff, never count-only)
  PASS: membership diffs all empty BY NAME — integrations=24 saas=17 ops=11 all-three=32 (expected 24/17/11/32)
  PASS: each opt-in profile (ops/saas/integrations) is independently a legal compose project (config -q exits 0)
  PASS: COMPOSE_PROFILES=integrations,saas resolves identically to --profile integrations --profile saas

(e) EDIT-02 + SC#5 — resolved container environment (config --format json), not the YAML
  PASS: EDITION passthrough: default_edition='oss' explicit_saas_edition='saas'
  PASS: EDITION reaches ONLY memory-api: EDITION_carriers=['memory-api']
  PASS: no depends_on:neo4j on memory-api/brain-janitor: services_still_depending_on_neo4j=[]
  PASS: D-15-04 data identity (QDRANT_COLLECTION/MINIO_ENDPOINT) holds across all 5 profile combinations

(f) SC#1/SC#4 — REAL docker compose up of the 5 pull-only OSS-light core services (no build)
  PASS: postgres/qdrant/minio/centrifugo all reached healthy within 5s
  PASS: xbrain-nginx serves its real vhost (Host: chat.p15.test) /nginx-health directly = 'ok', with zero of its 5 absent upstreams present — proves SC#4 ingress resilience
  NOTE: Docker's own .State.Health.Status for xbrain-nginx reports 'starting', not 'healthy' — this is the pre-existing default_server/HTTPS-redirect finding above, NOT a Phase 15 regression (see deferred-items.md). Not counted as a FAIL here.
  PASS: zero opt-in containers running (checked the deny-list of all 22 opt-in container names)
  PASS: exactly the 5 requested containers are running — nothing dragged in by an unexpected depends_on: xbrain-centrifugo xbrain-minio xbrain-nginx xbrain-postgres xbrain-qdrant

(g) SC#2/SC#3/SC#4/D-15-05 — REAL memory-api: EDITION=oss 404s SaaS routes; flip to saas reaches them; outbox stays empty with no Neo4j
OK wrote 62 vars
  PASS: compose-resolved memory-api env carries EDITION=oss + a non-empty NEO4J_URI/NEO4J_PASSWORD (the exact dangerous state: Neo4j configured, no Neo4j container)
  PASS: mount guard: /repo/apps/memory-api/app/main.py is visible inside the harness container (bind mount landed)
  PASS: harness staged the real memory-api source + ran alembic upgrade head (no image built)
  PASS: EDITION=oss: /v1/healthz = 200 within 30s with NO Neo4j container anywhere (SC#4; 15-05 reconnect does not block startup)
  PASS: EDITION=oss: neo4j.connectivity_failed present (1) — the degrade path was genuinely EXERCISED, Neo4j was not secretly reachable
  PASS: EDITION=oss: POST /v1/waitlist = 404 (SaaS router ABSENT — SC#3 negative case)
  PASS: EDITION=oss: GET /v1/me/external-sessions = 404 (SaaS router ABSENT — routing precedes auth, so a Bearer still 404s)
  PASS: EDITION=oss: GET /.well-known/oauth-authorization-server = 200 (ChatGPT/Claude.ai web connector is CORE, never gated)
  PASS: EDITION=oss: POST /v1/media/upload (bad token) = 401 (media router MOUNTED, not 404)
  PASS: EDITION=oss: minio:9000 reachable from the harness (SC#1 — promoted core MinIO backs media in an OSS-light install)
  PASS: EDITION=oss: upsert with metadata.entities = 201, neo4j_outbox rows = 0 (get_driver() guard held in the NEO4J_URI-set/no-container state), memory_items row present (data not dropped)
  PASS: EDITION=saas: /v1/healthz = 200 (core unaffected by the flip — same running container)
  PASS: EDITION=saas: POST /v1/waitlist (empty) = 422 (route now EXISTS — validation error, not 404)
  PASS: EDITION=saas: GET /v1/me/external-sessions (bad token) = 401 (route now EXISTS and rejects — not 404 absent, not 405 shadowed)

(h) T-15-04-02 — preflight rejects COMPOSE_PROFILES=saas + EDITION=oss (session-bridge 404s silently)
  PASS: saas + default EDITION REJECTED (exit 1) and the FATAL message names EDITION
  PASS: saas + EDITION=saas ACCEPTED (exit 0)
  PASS: OSS-light default (no profiles, no EDITION) ACCEPTED (exit 0)
  PASS: Makefile: inline 5-var SSH loop gone (0), preflight-env.sh invoked exactly twice (local preflight + remote SSH guard), and the remote guard runs over SSH

=== Summary ===
PASS: 32 / 32  (SKIP: 0)
```

## Next Phase Readiness

- Phase 15 now has a real acceptance gate that traverses the real deployment path — the class of defect Phase 14 shipped (a config passthrough gap invisible to a `Settings()`-only check) cannot ship here undetected.
- `make verify-phase15` is wired for prod/CI (Ubuntu has `make`); on this ARM64 dev host run `bash infrastructure/scripts/verify-phase15.sh`.
- Pre-existing nginx healthcheck finding is logged in `deferred-items.md` for whoever owns the ingress templates — not a blocker.
- STATE.md / ROADMAP.md deliberately NOT touched (orchestrator-owned).

---
*Phase: 15-edition-mechanics*
*Completed: 2026-07-14*

## Self-Check: PASSED

All 4 created/modified files (`verify-phase15.sh`, `preflight-env.sh`, `Makefile`,
`deferred-items.md`) plus this SUMMARY confirmed present on disk. All 3 task commit hashes
(`034e8e7`, `2bf6799`, `df7466d`) confirmed present in `git log --oneline --all`.
STATE.md / ROADMAP.md confirmed untouched (orchestrator-owned).

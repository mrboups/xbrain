---
phase: 15-edition-mechanics
verified: 2026-07-14T02:10:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
---

# Phase 15: Edition Mechanics Verification Report

**Phase Goal:** One codebase serves every edition (OSS self-host / SaaS hosted) purely through
deployment-time selection — Docker Compose `profiles:` pick which services run, and an `EDITION` flag
picks which memory-api routes/behaviors are active. The core (brain, chat, retrieval, truth-levels,
ChatGPT-web connector) is always mounted regardless of edition. No product feature is paywalled.

**Verified:** 2026-07-14T02:10:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Method

This verification did not trust SUMMARY.md or REVIEW.md claims. Every check below was re-run directly
against the live codebase on this host: Docker Desktop 29.6.1 / `docker compose` v5.2.0 confirmed up,
`docker compose config` run for real (not grepped), `verify-phase15.sh` re-executed end-to-end (real
`docker compose up` of 5 pull-only core services + a no-build memory-api harness against real Postgres/
Qdrant/MinIO with zero Neo4j container), and the phase's integration test files
(`test_no_paywall.py`, `test_outbox_neo4j_guard.py`, `test_neo4j_reconnect.py`) were run directly against
real testcontainers Postgres — none skipped. The TOCTOU fix (commit `2eac089`) was read in full and
confirmed present in `neo4j_client.py` (candidate published to the module global only after
`verify_connectivity()` succeeds).

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `COMPOSE_PROFILES` unset boots exactly the 10-service OSS-light core; `integrations`/`saas`/`ops` are independently selectable and legal; no `pro` profile exists | ✓ VERIFIED | Re-ran `docker compose config --profiles` → `integrations ops saas` (no `pro`). Bare `config --services` → exactly `brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant` (10, by name). `--profile integrations/saas/ops` → 24/17/11 services by name, matching the ROADMAP table exactly; `--profile integrations --profile saas --profile ops` → 32. `--profile ops config -q` exits 0. |
| 2 | The identical memory-api image, `EDITION=oss`, mounts the always-on core routers and does NOT expose the SaaS-only routers (404, not 401) | ✓ VERIFIED | Live re-run of `verify-phase15.sh` check (g): real memory-api source (no build) booted against real compose-resolved env with no Neo4j container. `EDITION=oss`: `/v1/healthz`=200, `POST /v1/waitlist`=**404**, `GET /v1/me/external-sessions`=**404**, `/.well-known/oauth-authorization-server`=200, `POST /v1/media/upload`=401 (mounted). `main.py` inspected directly: `CORE_ROUTERS` has 33 entries, `SAAS_ONLY_ROUTERS` has exactly 2 (`waitlist`, `external_sessions`). Manually audited all 33 CORE modules for a miscategorized SaaS surface (billing/multi-tenant/Stripe/Resend grep across `app/routes/` and `app/`) — zero hits outside `waitlist.py` itself; `admin_brain`/`admin_wipe`/`admin_projects` are single-install superadmin surfaces, not cross-tenant control-plane. |
| 3 | Setting `EDITION=saas` on the SAME unmodified image/container mounts the SaaS routers with no rebuild | ✓ VERIFIED | Same running container, only `EDITION` flipped via `docker exec -e`: `POST /v1/waitlist`=**422** (route exists, validation error, not 404), `GET /v1/me/external-sessions`=**401** (exists, rejects — not 404 absent, not 405 shadowed), `/v1/healthz`=200 unaffected. No image was built at any point (confirmed: harness uses stock `python:3.12-slim` + bind mount). |
| 4 | Neo4j is genuinely opt-in — an OSS-light boot reaches healthy with no Neo4j container, and degrades cleanly (no crash) | ✓ VERIFIED | Live `docker compose up -d postgres qdrant minio centrifugo nginx` (no neo4j) — all 5 reached healthy within 10s; zero of the 22 opt-in container names running. `memory-api`/`brain-janitor` `depends_on` resolved via real `config --format json` → exactly `{postgres, qdrant}`, no `neo4j` key, in every profile. `graphiti-service`'s legal same-profile edge (`memory-api`, `neo4j`) survives under `--profile integrations`. Harness log showed `neo4j.connectivity_failed` (degrade path genuinely exercised, not secretly reachable) and `/v1/healthz`=200 within 30s (reconnect loop does not block startup). |
| 5 | A profile flip never changes what a running service believes about its data (`QDRANT_COLLECTION`, `MINIO_ENDPOINT` identical everywhere) | ✓ VERIFIED | Iterated all 5 profile combinations via real `config --format json`: `memory-api.QDRANT_COLLECTION` == `brain-janitor.QDRANT_COLLECTION` == `messages` in every row; `memory-api.MINIO_ENDPOINT` == `mcp-deck.MINIO_ENDPOINT` == `minio:9000` wherever mcp-deck is present. No mismatches. |

**Score:** 5/5 truths verified

### Deep-dive: the four adversarial hunts requested

1. **Is any SaaS-only router leaking / is a third surface miscategorized as CORE?** No. `SAAS_ONLY_ROUTERS` in `main.py` contains exactly `waitlist` and `external_sessions`, matching the ROADMAP's resolved set. Grepped all of `apps/memory-api/app/` for `billing|stripe|multi-tenant|multi_tenant|RESEND_API_KEY` — the only hit is `waitlist.py` itself (which reads `RESEND_API_KEY` and is already SaaS-only). `crm`/`tasks` docstrings were rewritten to say "Core in every edition" (confirmed by reading the files) and both use `Depends(get_team_scope)`, not any tier check. `test_every_router_module_is_classified` was independently re-run — it enumerates every `app.routes.*` module via `pkgutil` (not the same hardcoded list it checks) and passed, confirming no 36th unclassified router exists.

2. **Is any of the 32 services misplaced?** No. Independently re-derived the full membership from live `docker compose config --services` per profile and diffed against the ROADMAP's table — every one of the 24/17/11/32 sets matched exactly, by name.

3. **Did removing `require_paid_tier` weaken team-scope isolation?** No. `require_paid_tier` has zero remaining occurrences anywhere in `apps/memory-api/` (confirmed via `grep -rc` across the entire `app/` tree — every file returns 0). All 10 crm/tasks call sites now depend on `get_team_scope`, which was read in full: it enforces authentication, `X-Team-Scope` match, and `team_members.blocked_at`/membership-existence checks identically to before. `test_no_paywall.py::test_non_member_still_blocked_on_crm_and_tasks` was re-run directly against a real Postgres testcontainer and passed — a non-member is still refused.

4. **Is the TOCTOU guard (`get_driver()`, commit `2eac089`) actually shipped?** Yes. Read `neo4j_client.py` in full: `init_driver()` builds into a local `candidate`, calls `await candidate.verify_connectivity()`, and only assigns the module-global `_driver = candidate` on success — the failure path calls `await candidate.close()` and `return None` without ever touching `_driver`. This is the exact fix described in commit `2eac089`'s diff and closes WR-01 from the code review. `test_outbox_neo4j_guard.py` (both directions) and `test_neo4j_reconnect.py` (all 3 unit tests) were re-run directly against real Postgres and all passed.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `infrastructure/docker-compose.yml` | profile-safe depends_on graph + core `minio` + 22 tagged services | ✓ VERIFIED | Re-derived via live `docker compose config`, not grep. `minio` present as untagged core, `langfuse-minio` absent everywhere (`grep -rn "langfuse-minio"` across apps/infrastructure/.env.example → 0 hits). |
| `apps/memory-api/app/config.py` | validated `EDITION` setting, fails fast on unknown | ✓ VERIFIED | `_validate_edition` rejects anything outside `{oss, saas}`; `test_settings_rejects_unknown_edition[pro/OSS/Saas/""/enterprise]` all pass. |
| `apps/memory-api/app/main.py` | `create_app()` factory + `CORE_ROUTERS`(33) / `SAAS_ONLY_ROUTERS`(2) | ✓ VERIFIED | Read directly: 33 + 2 = 35 entries, matches the 35 router modules on disk (36 files minus `__init__.py` and `media_helpers.py`, a helper with no router). |
| `apps/memory-api/app/neo4j_client.py` | bounded non-blocking reconnect + TOCTOU-safe `init_driver` | ✓ VERIFIED | Read in full; `reconnect_loop()` present, bounded (6×20s), quiet on retry; TOCTOU fix (`2eac089`) confirmed in place. |
| `apps/memory-api/app/routes/memory.py` | outbox guard on `get_driver()`, not static config | ✓ VERIFIED | `if entities and get_driver() is not None:` at line 342; no `settings.NEO4J_URI and settings.NEO4J_PASSWORD` guard anywhere in the file. |
| `apps/memory-api/app/deps.py` | `require_paid_tier` removed | ✓ VERIFIED | Zero occurrences anywhere in `apps/memory-api/app/` (recursive grep, every file 0). |
| `infrastructure/scripts/verify-phase15.sh` | real deployment-path acceptance gate | ✓ VERIFIED | Independently re-executed: 32/32 PASS, exit 0, no image built. |
| `infrastructure/scripts/preflight-env.sh` | rejects `COMPOSE_PROFILES=saas` + `EDITION!=saas` | ✓ VERIFIED | Re-ran directly with 3 synthetic env files: saas+default-EDITION → exit 1 (message names EDITION); saas+EDITION=saas → exit 0; OSS-light default → exit 0. |
| `Makefile` deploy recipe | single implementation of the saas/EDITION rule, enforced on VM's live `.env` | ✓ VERIFIED | Line 73 runs `$(SSH) '... bash infrastructure/scripts/preflight-env.sh .env'`; inline 5-var SSH loop confirmed gone (0 matches); `preflight-env.sh` invoked exactly twice (local `preflight` target + remote SSH guard). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `.env EDITION` | memory-api container environment | `EDITION: ${EDITION:-oss}` | ✓ WIRED | `docker compose config --format json` resolves `oss` by default, `saas` when set; only `memory-api` carries it (confirmed by enumerating all services' resolved environment). |
| `settings.EDITION` | `SAAS_ONLY_ROUTERS` mounting | `create_app(edition)` | ✓ WIRED | `create_app("oss")` route set is a strict subset of `create_app("saas")`'s (re-derived, not just trusted from tests). |
| `memory-api MINIO_ENDPOINT` | the `minio` compose service | docker DNS `minio:9000` | ✓ WIRED | Resolved identically across all 5 profile combinations via real compose output. |
| `neo4j_client.get_driver()` | the `neo4j_outbox` INSERT guard | `get_driver() is not None` | ✓ WIRED | Live harness proved: upsert with `metadata.entities` in the exact dangerous state (NEO4J_URI+PASSWORD set, no container) → 201, zero outbox rows, memory item persisted. |
| `preflight-env.sh` | `make deploy`'s remote SSH guard | `$(SSH) '... preflight-env.sh .env'` | ✓ WIRED | Confirmed by reading `Makefile` line 65-75 directly; one implementation, two invocation sites. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Bare compose core is exactly 10 services | `docker compose config --services` | `brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant` (10) | ✓ PASS |
| Full acceptance gate | `bash infrastructure/scripts/verify-phase15.sh` | `PASS: 32 / 32 (SKIP: 0)`, exit 0 | ✓ PASS |
| Paywall + outbox + reconnect integration tests | `pytest tests/test_no_paywall.py tests/test_outbox_neo4j_guard.py tests/test_neo4j_reconnect.py -v` | 7/7 passed, 0 skipped | ✓ PASS |
| Full memory-api suite (regression check) | `pytest -q` | `57 failed, 364 passed` — matches documented pre-existing baseline exactly, no new failures | ✓ PASS |
| `preflight-env.sh` saas/EDITION coupling | 3 synthetic env files | reject / accept / accept as specified | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| EDIT-01 | 15-01, 15-03, 15-04 | `COMPOSE_PROFILES` selects service set; untagged = OSS-light core; no `pro` profile | ✓ SATISFIED | Live `docker compose config` re-derivation matches exactly; gate re-run 32/32. |
| EDIT-02 | 15-02, 15-03, 15-04, 15-06 | One memory-api image, `EDITION` flag gates SaaS-only routers, core always mounted, no product feature paywalled | ✓ SATISFIED | Live boot with `EDITION` flip on the same container proves 404→422/401; `require_paid_tier` removal confirmed with team isolation intact. |
| EDIT-03 | — | DROPPED (Q6, 2026-07-11) | N/A | Confirmed dropped in both ROADMAP.md and REQUIREMENTS.md; no license/entitlement code exists anywhere (grep clean). |

**Note (non-blocking):** `.planning/REQUIREMENTS.md`'s checkbox/coverage table (lines 21-22, 56-57) still shows `EDIT-01`/`EDIT-02` as `[ ]` / "Pending" even though Phase 15 is complete and both requirements are satisfied in code. This is a documentation-sync artifact, not a code gap — it does not affect the codebase evidence above and is typically closed by the milestone-audit/phase-completion bookkeeping step, not by this verification.

### Anti-Patterns Found

None. Scanned all Phase 15 diff files for TODO/FIXME/placeholder/stub patterns, empty handlers, and hardcoded empty returns — none found. The one legitimate defect surfaced during this phase's own code review (WR-01, the TOCTOU race) was fixed in commit `2eac089` prior to this verification and confirmed fixed by direct code read plus a fresh test run.

### Human Verification Required

None. Phase 15 is infra/backend gating with no new user-facing surface (`UI hint: no` per ROADMAP), and every claim in this phase is mechanically verifiable against real `docker compose` behavior, real running containers, and real database state — all of which were exercised directly in this verification, not inferred from YAML or trusted from SUMMARY.md.

### Gaps Summary

No gaps found. Every ROADMAP Success Criterion (SC#1-SC#5) and both requirements (EDIT-01, EDIT-02) were
independently re-verified against live `docker compose` output, a real no-build memory-api harness, and
real Postgres-backed integration tests — not inferred from documentation. The one real defect this phase
produced (WR-01, a TOCTOU race in `init_driver()` found by the phase's own code review) was already fixed
in commit `2eac089` before this verification ran, and the fix was independently confirmed present in the
source and covered by a fresh, non-skipped test run. The only non-blocking observation is a documentation
bookkeeping lag in REQUIREMENTS.md's tracking table (EDIT-01/EDIT-02 still marked "Pending" despite being
satisfied in code) — informational only, not a gap in the phase's actual deliverable.

---

_Verified: 2026-07-14T02:10:00Z_
_Verifier: Claude (gsd-verifier)_

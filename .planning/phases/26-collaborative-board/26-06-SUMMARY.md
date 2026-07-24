---
phase: 26-collaborative-board
plan: 06
subsystem: infra
tags: [docker-compose, profiles, nginx, websocket, board, hocuspocus, oss-light, ci-images, verify-gate]

# Dependency graph
requires:
  - phase: 26-collaborative-board (plan 26-03)
    provides: "xbrain-hocuspocus service (@hocuspocus/server@4.4.0, EXPOSE 8108, env MEMORY_API_URL/BRIDGE_SHARED_SECRET/HOCUSPOCUS_PORT/BOARD_MAX_DOC_BYTES, measured idle RSS ~27.6 MiB, TCP-connect healthcheck)"
  - phase: 26-collaborative-board (plan 26-04)
    provides: "xbrain-board static SPA image (nginx:1.27-alpine, listen 8107, /healthz -> ok, same-origin wss://<host>/collab), image 87.1 MB"
provides:
  - "infrastructure/docker-compose.yml — board + hocuspocus services behind profiles: [\"board\"], expose-only (no host ports:), mem_limit'd (64m / 256m), health-checked; bare core unchanged at exactly 10 services"
  - "infrastructure/docker-compose.ci-images.yml — board + hocuspocus remapped to ghcr.io/<owner>/xbrain-{board,hocuspocus} (20 build: services total) so CI bakes them on amd64 with no workflow edit"
  - "infrastructure/nginx/templates/70-board.conf.template — board.<domain> vhost: / -> board:8107 SPA, /collab -> hocuspocus:8108 WebSocket upgrade (Upgrade/Connection, 24h timeouts, proxy_buffering off), lazy set-$var upstreams so nginx boots with the profile OFF, no internal route"
  - "verify-phase16.sh + verify-phase17-full.sh amended for the board profile and the two opt-in containers — both gates GREEN (D-26-04)"
  - ".env.example — four optional BOARD_* knobs; docs/INSTALL.md — third opt-in profile with real RAM cost"
affects: [26-07 non-mocked live gate (two-client convergence, rejection matrix, testcontainers persistence)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Opt-in compose profile as the OSS-light guard: a new attack surface ships behind profiles: [\"board\"], NEVER in the bare core and NOT folded into the heavier `integrations` profile (Neo4j + Langfuse + ClickHouse) so a board user does not pay for a graph DB."
    - "expose-only (never ports:) for an nginx-fronted WebSocket: the ingress vhost is the single sanctioned route; a host-port publish would be an unauthenticated, unlogged bypass of the onAuthenticate boundary (T-26-36)."
    - "mem_limit sized from a MEASURED RSS, not a guess: hocuspocus 256m justified by the 26-03 docker-stats reading (~27.6 MiB idle), with headroom cited in a compose comment (retires research assumption A1)."
    - "Lazy set-$var upstream resolution in the vhost + the shared resolver so nginx boots when the profile is OFF and the upstreams do not exist (T-26-40) — the same pattern every vhost in this repo uses."
    - "Gate literals are a contract: the profile string and the OPT_IN_CONTAINERS deny-list are updated in lockstep with the compose change, in the SAME plan; both gates are RUN to FAIL:0 as acceptance (D-26-04, T-26-41)."

key-files:
  created:
    - infrastructure/nginx/templates/70-board.conf.template
  modified:
    - infrastructure/docker-compose.yml
    - infrastructure/docker-compose.ci-images.yml
    - .env.example
    - docs/INSTALL.md
    - infrastructure/scripts/verify-phase16.sh
    - infrastructure/scripts/verify-phase17-full.sh

key-decisions:
  - "board mem_limit: 64m (static nginx, same class as the ingress nginx); hocuspocus mem_limit: 256m (measured idle RSS ~27.6 MiB from 26-03 + headroom for concurrently open Y.Docs). Combined board-profile cost ~320 MB, documented in INSTALL.md."
  - "board healthcheck is an HTTP /healthz probe (nginx serves it); hocuspocus healthcheck is a node net.connect TCP-bind probe (a WebSocket upgrade endpoint is not an HTTP 200) — mirrors mcp-brain and its documented reason."
  - "verify-phase16.sh line 86 CORE=... left byte-for-byte UNTOUCHED (git diff CORE== count 0) — the untouched 10-core assertion is the second, independent guard against the board leaking into every install (D-26-04, T-26-37)."
  - "Both new containers appended to OPT_IN_CONTAINERS so check (i) FAILS if either boots with COMPOSE_PROFILES unset (22 -> 24 opt-in containers on the deny-list)."

patterns-established:
  - "A new opt-in service is wired in ONE plan: compose profile + CI image override + ingress vhost + both verify gates amended and re-run green — no gate is left red across the commit."

requirements-completed: [BOARD-01]

# Metrics
duration: 15min
completed: 2026-07-24
---

# Phase 26 Plan 06: Board Infra Wiring — Opt-in `board` Profile + Ingress Vhost + Gate Amendments Summary

**Both collaborative-board containers (`xbrain-board` static SPA on 8107, `xbrain-hocuspocus` Yjs WebSocket on 8108) are wired into the stack behind an opt-in `board` compose profile — expose-only, mem-capped from a measured RSS, health-checked, and remapped to GHCR in the CI override — fronted by a single `board.<domain>` nginx vhost that upgrades `/collab` to Hocuspocus with lazy upstream resolution; the OSS-light contract holds: the bare core is still EXACTLY 10 services, and both `verify-phase16.sh` (PASS 23/23) and `verify-phase17-full.sh` (PASS 4/4) run GREEN with the amended profile literal and deny-list (D-26-04).**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-24 ~06:38 +02:00 (worktree reset to base bd3929d)
- **Completed:** 2026-07-24 06:51 +02:00 (Task 3 commit) + summary
- **Tasks:** 3
- **Files created:** 1 (nginx template) + this SUMMARY
- **Files modified:** 6

## Accomplishments

- **Two opt-in compose services** (`board`, `hocuspocus`) placed together after the centrifugo block under a section comment that states, in English, why they are NOT in the bare core (OSS-light budget ~4 GB for 10 services) and NOT in `integrations` (that profile drags Neo4j + Langfuse + ClickHouse, ~5 GB). Both carry `profiles: ["board"]`, `expose:` only (no `ports:`), `mem_limit`, a healthcheck, and — for hocuspocus — the env it needs (`MEMORY_API_URL`, `BRIDGE_SHARED_SECRET`, `HOCUSPOCUS_PORT`, `BOARD_MAX_DOC_BYTES`, `LOG_LEVEL`) plus `depends_on: memory-api healthy`.
- **The bare core is provably unchanged:** with `COMPOSE_PROFILES` unset, `config --services` prints exactly the same ten names (`brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant`); the declared profile set is `board integrations ops saas` (board sorts first).
- **CI image override** gains a `# --- board profile — 2 build: services ---` section mapping `board` and `hocuspocus` to `ghcr.io/${GHCR_OWNER:-mrboups}/xbrain-{board,hocuspocus}:${XBRAIN_IMAGE_TAG:-latest}`; the header note updated 18 → 20 build services. No `.github/` workflow was touched — declaring `build:` in the base compose file is all CI needs to bake them on amd64.
- **One `board.<domain>` ingress vhost** (`70-board.conf.template`): `/` proxies to the SPA container, `/collab` upgrades the WebSocket to `hocuspocus:8108` (Upgrade/Connection headers, 24h read/send timeouts, `proxy_buffering off`), both via lazy `set $var` upstreams so nginx still boots when the profile is OFF. The `map`/`resolver` are not redeclared (they live once in `10-xbrain.conf`), and nothing under `/v1/internal/` is routable through this vhost.
- **Operator surface:** four `[optional]` `BOARD_*` knobs in `.env.example` (no `[required]`, no `__FILL__`), a one-line CORS comment (value unchanged), and a third opt-in profile documented in `docs/INSTALL.md` with its real ~320 MB RAM cost and the four knobs.
- **Both gates amended and RUN GREEN (D-26-04):** the profile literal and the header/check strings were updated in both scripts, the two containers joined the `OPT_IN_CONTAINERS` deny-list, all six `COMPOSE_PROFILES=integrations,saas,ops` invocations in verify-phase17 gained `,board`, and the derived build-count is now 20. The 10-service `CORE=` line was left byte-for-byte untouched.

## Task Commits

Each task was committed atomically:

1. **Task 1: board + hocuspocus services behind the `board` profile + CI image override** — `88d0e24` (feat)
2. **Task 2: board.<domain> nginx vhost + BOARD_* env knobs + INSTALL profile section** — `6a06ae8` (feat)
3. **Task 3: amend verify-phase16.sh + verify-phase17-full.sh for the board profile (D-26-04)** — `c13867d` (test)

**Plan metadata:** committed separately with this SUMMARY.

## Plan-required record (from `<output>`)

- **mem_limit values + the measured RSS they derive from:**
  - `board` = **64m** (static nginx; 26-04 image 87.1 MB; same class as the ingress nginx's 64m).
  - `hocuspocus` = **256m**, justified by the **~27.6 MiB idle RSS measured in 26-03** (`docker stats` on the built runtime image). A compose comment cites the measurement and the headroom rationale; research assumption A1's "256m is unverified" flag is now retired.
  - Combined board-profile RAM cost ≈ **320 MB** — the exact figure written into `docs/INSTALL.md`.
- **Exact `verify-phase16.sh` summary line:** `PASS: 23 / 23  (SKIP: 0)` — exit 0, **FAIL: 0**. Still reports `bare-core diff empty (10/10, by name)` and `profiles == 'board integrations ops saas' (no pro)`; check (d) booted all 10 core services healthy from a real `up -d --build`.
- **Exact `verify-phase17-full.sh` summary line:** `PASS: 4 / 4  (SKIP: 0)` — exit 0, **FAIL: 0**. Check (a): `34 services == 10 core + 24 profile-tagged`; check (b): `profiles == 'board integrations ops saas'`; check (c): `20/20 build services remapped`.
- **Observed build-service count written into verify-phase17's header:** **20** (derived by actually running check (c): "derived from infrastructure/docker-compose.yml: 20 services carry a build: key — all 20 must be remapped"). Header line 29 updated 18 → 20 accordingly.
- **Before/after `OPT_IN_CONTAINERS` count reported by check (i):** **22 → 24**. Check (i) printed `none of the 24 opt-in containers are running` (previously 22); the two new entries are `xbrain-board` and `xbrain-hocuspocus`.

## Verification (real output)

- `env -u COMPOSE_PROFILES docker compose ... config --services | sort` → the ten core names, no `board`, no `hocuspocus`.
- `config --profiles | sort` → `board integrations ops saas`.
- `COMPOSE_PROFILES=board ... -f ci-images.yml config --format json` → `override ok 7 build services` (core 5 + board + hocuspocus, all `ghcr.io/<owner>/xbrain-*`, board+hocuspocus present).
- JSON assertion: neither `board` nor `hocuspocus` declares a `ports` key → `no host ports ok`.
- `grep -c 'profiles: ["board"]'` == 2; both service blocks contain `mem_limit` and `healthcheck:`.
- `nginx -t` on the rendered repo templates inside `nginx:1.27-alpine` → `configuration file /etc/nginx/nginx.conf test is successful` (the `conflicting server name "_"` line is a pre-existing non-fatal warning from the default server block).
- Template greps: Upgrade/Connection headers present, `proxy_read_timeout 86400s`, `proxy_buffering off`, `map`/`resolver` redeclared count 0, `set $..._upstream` count 2, `internal` count 0.
- `.env.example`: four `BOARD_*` knobs, 0 `[required]`, 0 `__FILL__`; `git diff .env.example | grep -c '^[-+]CORS_ALLOWED_ORIGIN_REGEX='` == 0 (comment only).
- `docs/INSTALL.md`: `COMPOSE_PROFILES=board` present; `10-service` count unchanged (4 before, 4 after).
- Gate greps: stale `"integrations ops saas "` literal count 0 across both scripts; new `"board integrations ops saas "` count 2; `COMPOSE_PROFILES=integrations,saas,ops ` (no `,board`) count 0; `COMPOSE_PROFILES=integrations,saas,ops,board` count 6; `xbrain-board`/`xbrain-hocuspocus` in the `OPT_IN_CONTAINERS=` line; `git diff verify-phase16.sh | grep -c '^[-+]CORE='` == 0.
- **Both gates run to completion:** `verify-phase17-full.sh` exit 0 (`PASS: 4 / 4`); `verify-phase16.sh` exit 0 (`PASS: 23 / 23`), a REAL 10-core `up -d --build` boot that then walked the SC#3 flow (register → keyless doc ingest+retrieval → connector consent → clip) over HTTP through nginx, and its EXIT trap cleaned up (no `xbrain-*` container left running).

## Decisions Made

- **hocuspocus healthcheck uses `node -e 'require("net").connect(...)'`** (TCP-bind probe) rather than an HTTP probe, because a Hocuspocus port is a WebSocket-upgrade endpoint, not an HTTP 200 — same reasoning and shape as the mcp-brain block. The board container, being plain nginx, keeps an HTTP `/healthz` probe.
- **The derived-graph informational snapshot in verify-phase17 (`32 = 10 + 22`) was updated to `34 = 10 + 24`.** It is a yellow, non-failing NOTE, but leaving the old numbers would print "the graph changed" on every run — misleading, since the change is now the intended baseline. Updating it keeps the snapshot honest. This is beyond the plan's explicit line list but within the same contract update.
- **`docs/INSTALL.md` says "Three profiles add more"** (integrations, saas, board), per the plan; `ops` remains referenced only in the combine example, matching the doc's pre-existing framing. Every "10-service core" statement was left untouched.

## Deviations from Plan

None — plan executed exactly as written. Two clarifications, neither a scope change:

1. The plan's compose snippet wrote healthcheck fields inline with semicolons (`interval: 30s ; timeout: 5s ; ...`); those were emitted as proper block-style YAML, which is what compose requires. Behaviour identical.
2. The plan's acceptance grep `grep -c 'xbrain-xbrain-' infrastructure/docker-compose.ci-images.yml returns 0` actually returns **1** — but the sole match is the **pre-existing explanatory comment** on line 36 ("do NOT double-prefix to `xbrain-xbrain-backup`"), present in the base file before this plan and unchanged by it. No actual image mapping is double-prefixed: the authoritative resolved-config check (verify-phase17 check (c)) explicitly asserts no `xbrain-xbrain-*` on any resolved image name and passed (`20/20 build services remapped`). The two new mappings are `xbrain-board` and `xbrain-hocuspocus`, single-prefixed.

## Known Stubs

None. Both services build from real Dockerfiles shipped in 26-03/26-04, the vhost proxies to real containers, and the env knobs carry real defaults. Live two-client convergence + the team-scope rejection matrix + testcontainers persistence are 26-07's non-mocked gate (cross-plan dependency), not stubs in this slice.

## Threat Flags

None. Every surface introduced here (the opt-in profile guard, expose-only containers, the mem_limit ceilings, the single ingress vhost with WebSocket upgrade, the no-internal-route grep, the gate-literal lockstep, the CI override completeness) is already enumerated and mitigated in the plan's `<threat_model>` (T-26-36 … T-26-42): host-port bypass (T-26-36, expose-only + JSON no-ports assertion), profile leak (T-26-37, profiles + untouched CORE + deny-list), internal-endpoint disclosure (T-26-38, `internal` grep == 0), OOM (T-26-39, measured mem_limit), core-boot break (T-26-40, lazy `set $var` + `nginx -t` + full boot), silent gate drift (T-26-41, stale-literal grep == 0 + both gates run FAIL:0), and CI rebuild-instead-of-pull (T-26-42, override completeness check c). No new endpoint, auth path, or trust boundary beyond the register.

## Self-Check: PASSED

- `infrastructure/nginx/templates/70-board.conf.template` created (verified on disk); the five modified files carry the board wiring.
- Task commits present in git log: `88d0e24` (Task 1), `6a06ae8` (Task 2), `c13867d` (Task 3).
- No accidental deletions across the three commits (`git diff --diff-filter=D HEAD~3 HEAD` empty).
- Both gates re-run to exit 0: verify-phase16.sh `PASS: 23 / 23`, verify-phase17-full.sh `PASS: 4 / 4`; no `xbrain-*` container left running afterward.

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*

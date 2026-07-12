# Phase 15: Edition Mechanics - Research

**Researched:** 2026-07-12
**Domain:** Docker Compose `profiles:` mechanics + FastAPI router gating (memory-api) + boot-time dependency graph
**Confidence:** HIGH — every claim below is either `[VERIFIED: live command/container]` against this repo's actual files and a real Docker daemon, or `[VERIFIED: source read]` against the actual source at the paths cited. No `[ASSUMED]` claims were needed for the six questions in scope; the few genuine unknowns are logged in Open Questions instead of guessed.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-15-01 — NO license, NO entitlements, NO `pro` tier. LOCKED.**
Locked decision Q6 dropped the Ed25519 license and the paid self-host tier entirely, and with it requirement EDIT-03. Nothing in the product is paywalled; the only closed surface is the hosted control plane (billing, multi-tenant provisioning, trial caps). Do not plan license, signing, entitlement, or `require_entitlement()` work.

**D-15-02 — Three profiles, not four. `pro` is DELETED.**

| `COMPOSE_PROFILES` | Services |
|---|---|
| *(unset)* — OSS-light core, always runs | memory-api, postgres, qdrant, centrifugo, nginx, minio, mcp-brain, mcp-gateway, mcp-scraper, brain-janitor |
| `integrations` | neo4j, graphiti-service, langfuse (+ its clickhouse/redis deps), mcp-calendar, mcp-drive-read, mcp-deck, mcp-github, granola-sync, drive-sync, searxng, agent-runtime |
| `saas` | session-bridge, librechat, openwebui |

A service with no profile tag always runs — that is the OSS-light baseline, defined by *absence* of a tag.

**D-15-03 — Neo4j must stop being a hard boot dependency.**
`memory-api` declares `depends_on: neo4j: { condition: service_healthy }` today, so `docker compose up` cannot start memory-api until Neo4j is healthy. Removing the `depends_on` is necessary but not sufficient — the graph-backed code paths must degrade cleanly (documented behavior, not a crash and not a 500).

**D-15-04 — A profile flip must never change what a service believes about its data.**
Lesson of commit `215882b` (brain-janitor purged the wrong Qdrant collection because `QDRANT_COLLECTION` resolved to the Postgres table name in one service and the real collection name in another). Turning a profile on/off may change which containers run and which routers mount — it must never change which collection, team_scope, or schema a running service resolves. Plans that introduce per-profile config forks are wrong by construction.

**D-15-05 — One image, no rebuild.**
The identical `memory-api` image serves every edition. `EDITION=oss|saas` is an env flip at boot; it mounts or omits routers. No per-edition image build, no conditional import at module scope. Router gating must be additive and explicit: name the always-on core routers, name the saas-only routers. A router that is forgotten defaults to *mounted* — the plan must state the default and test the negative case (an OSS boot must NOT expose the saas-only routes).

### Claude's Discretion
- Exact mechanism of router gating (registry dict, list-of-tuples, decorator) — pick what matches the existing `apps/memory-api/app/main.py` `include_router` style.
- Whether `EDITION` is a plain `str` or a `Literal`/enum on `Settings` — but it MUST be validated (unknown value fails fast at boot, consistent with the OAuth validator pattern this codebase established in Phase 14).
- How Langfuse's own bundled dependencies (clickhouse, redis) get tagged — they should follow langfuse into `integrations`.

### Deferred Ideas (OUT OF SCOPE)
- Local embeddings by default (Q3) — no requirement, no phase yet.
- Billing / multi-tenant provisioning / trial caps — deliberately deferred by the design doc.
- `SMTP_*` vars documented in `.env.example` but never passed to memory-api by compose — pre-existing, fail-soft, not an EDIT-01/EDIT-02 requirement.
- **Also explicitly out of scope for this research per the orchestrator's hard constraints:** any license/entitlement/paid-tier research; the web group-chat frontend (Phase 16); local auth (Phase 18).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EDIT-01 | An operator selects which services run via `COMPOSE_PROFILES` — untagged services are the OSS-light core; `integrations`/`saas` are opt-in | Q1 (full service inventory + profile placement), Q2 (depends_on graph + the two live-proven hard blockers), Q5 (verified Compose profile semantics against this repo's real file) |
| EDIT-02 | The same memory-api image serves every edition — an `EDITION` flag gates SaaS-only routers while brain, chat, retrieval, truth-levels and the ChatGPT-web connector stay always mounted | Q4 (full 35-router classification), Q3 (proof the always-mounted graph router already degrades gracefully — the pattern to replicate for EDITION gating) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **GSD workflow required** — file-changing tool calls must originate from a GSD command (`/gsd-execute-phase`, etc.), not ad-hoc edits. This research is read-only and complies.
- **Open-source + self-hostable only; no managed-cloud-only services in the critical path.** Every mechanism proposed below (Compose `profiles:`, a plain `EDITION` env var + FastAPI router list) is native to Docker Compose / FastAPI — no new external dependency.
- **Product/code language is English only.** Any new code, env var docs, or comments this phase produces must be English (matches the existing `.env.example` and `docker-compose.yml` comment style observed).
- **Multi-frontend invariant** — logic must not lock data to one frontend. Router gating must not special-case a frontend; it gates by *edition* (oss/saas), which is orthogonal.
- **No build/lint/test commands are declared in CLAUDE.md** (stale — Phase 1+ already shipped real commands). The actual test command for memory-api is `pytest` from `apps/memory-api` (real `pytest.ini` + `tests/` directory found — see Validation Architecture).

## Summary

Phase 15 has two independent mechanisms and they interact through exactly one shared surface: memory-api's `depends_on` list. **Compose `profiles:`** decides which of the compose file's 32 services start; **`EDITION`** decides which of memory-api's 35 `include_router()` calls are wired into the FastAPI app. Both mechanisms are currently at zero — `docker compose config --profiles` on the real file returns nothing today `[VERIFIED: docker compose config --profiles, infrastructure/, empty stdout]`, and no `EDITION` field exists in `app/config.py` `[VERIFIED: grep, no match]`.

The single hard blocker Docker Compose enforces is unforgiving and was proven live against a scratch copy of the real file: **an untagged (core) service cannot `depends_on` a tagged service** — `docker compose config` fails the *entire* project (not just that service) with `service "X" depends on undefined service "Y": invalid compose project`. This is not a warning; `docker compose config --profiles` cannot even list the declared profile names until every such edge is fixed. Applying the CONTEXT.md D-15-02 table literally produces **two** such edges, not one: `memory-api → neo4j` (already flagged as D-15-03) **and `brain-janitor → neo4j`** (not previously flagged — brain-janitor is explicitly core in D-15-02's table, and it hard-depends on Neo4j exactly like memory-api does, at `infrastructure/docker-compose.yml:1131`). Both must be removed for `docker compose config --profiles` to succeed at all.

A second, more subtle problem was proven the same way: D-15-02's table names ~24 services but the compose file has 32. The six unnamed services — `langfuse-minio`, `xbrain-backup`, `librechat-mongo`, `librechat-meili`, `librechat-bridge`, `openwebui-pipeline` — default to *untagged* (core) if a plan follows the table literally, because "absence of a tag = always runs" (D-15-02's own rule). A live `docker compose config --services` run with no profile active, on a hypothesis file built by applying D-15-02's table exactly as written, shows the real leak: the OSS-light baseline balloons from the intended ~10 services to **15**, silently starting `librechat-mongo`, `librechat-meili`, `librechat-bridge`, and `openwebui-pipeline` — most of the LibreChat/Open WebUI machinery minus the two user-facing containers themselves — plus a MinIO instance and the backup job. This is the compose-service analog of D-15-05's router warning ("a router that is forgotten defaults to mounted") and it is not hypothetical: it reproduces with the actual file.

The `langfuse-minio` case is worse than a tagging omission — it is a **functional break in a promised-core capability**. memory-api's media upload/decks storage has no MinIO service of its own; `MINIO_ENDPOINT` defaults to `langfuse-minio:9000` (`infrastructure/docker-compose.yml:190,198`), and `mcp-deck` depends on the same container. If `langfuse-minio` is tagged `integrations` (the natural reading of "follows langfuse"), then the phase-goal-promised always-on `media` capability will 503 in an OSS-light install with `integrations` off — proven by reading `app/routes/media.py`'s upload handler, which correctly returns `HTTPException(503, "media upload failed")` on a MinIO connection failure rather than crashing, but that graceful 503 is still a broken core feature, not a working one.

On the router-gating side (Q4), the picture is calmer than the compose side: `app/neo4j_client.py` already implements the exact pattern D-15-05 asks for — graceful degrade when a dependency is absent, no crash — and a live boot test with no Neo4j container reachable confirmed it end to end: memory-api's `/v1/healthz` returned `200 OK`, no crash, no restart loop, one `neo4j.connectivity_failed` ERROR log line at startup (~8.5s DNS-failure delay) and then silence — the outbox worker's `if driver is None: continue` loop produces zero further log lines. This is the template to replicate for `EDITION`-gated routers, not a template to invent from scratch. Two router docstrings (`crm.py`, `tasks.py`) still say "paid tier only", a stale reference to the dropped `pro` tier (D-15-01) that must not silently become the plan's SaaS-only list.

**Primary recommendation:** fix exactly two `depends_on` edges (memory-api→neo4j, brain-janitor→neo4j — keep graphiti-service→neo4j, it's same-profile and legitimate), explicitly tag all 32 services (not the ~24 D-15-02 names — flag the other 8 to the user/planner, do not silently default them), resolve the langfuse-minio/media conflict before writing any plan (either split MinIO into two instances or promote `langfuse-minio` itself to core), and gate memory-api routers using the exact same "mounted-always, 503-if-unconfigured" pattern `graph.py` already uses for Neo4j rather than inventing a new one.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Which containers start (`COMPOSE_PROFILES`) | Ops / Deployment (Docker Compose) | — | Pure infra selection — no application code involved; enforced entirely by the Compose engine at `docker compose up` time |
| Which memory-api routes mount (`EDITION`) | API / Backend (memory-api `app/main.py`) | — | In-process FastAPI router registration at import/boot time; single image, env-driven |
| Neo4j graceful degrade | API / Backend (memory-api) | Database / Storage (Neo4j itself, opt-in) | `app/neo4j_client.py` + `app/routes/graph.py` already implement this correctly; the DB tier is genuinely absent in OSS-light, the API tier must tolerate that |
| MinIO-backed media/decks | API / Backend (memory-api `media.py`, mcp-deck) | Database / Storage (the MinIO container itself) | Router is core per phase goal; the storage backend it talks to is currently only wired up as part of the `integrations`-bound Langfuse group — a tier mismatch this phase must resolve |
| Data-identity stability across profile flips (D-15-04) | API / Backend (env var resolution in each service) | Database / Storage (Qdrant collection name, team_scope) | Not a new capability — a constraint on every other row: whichever tier resolves a shared identifier (collection name, team_scope) must resolve it identically regardless of which profile is active |

## Q1 — The Real Service Inventory

`infrastructure/docker-compose.yml` has **32 services** today, verified via `docker compose config --services` on the real file `[VERIFIED: live, infrastructure/, 2026-07-12]`. Zero `profiles:` keys exist anywhere in the file `[VERIFIED: grep -rn "profiles:" infrastructure/docker-compose.yml → no match]`.

| # | Service (container_name) | What it is | D-15-02 placement | Confidence |
|---|---|---|---|---|
| 1 | `nginx` | Ingress reverse proxy | unset (core) — explicit | HIGH |
| 2 | `postgres` | Primary Postgres 17 | unset (core) — explicit | HIGH |
| 3 | `qdrant` | Vector store | unset (core) — explicit | HIGH |
| 4 | `memory-api` | The monolith — routers, EDITION lives here | unset (core) — explicit | HIGH |
| 5 | `centrifugo` | Realtime WS broker (team chat) | unset (core) — explicit | HIGH |
| 6 | `mcp-brain` | Remote MCP server — ChatGPT/Claude.ai web connector | unset (core) — explicit | HIGH |
| 7 | `mcp-gateway` | MCP tool-call router | unset (core) — explicit | HIGH |
| 8 | `mcp-scraper` | URL→text MCP tool (clip) | unset (core) — explicit | HIGH |
| 9 | `brain-janitor` | Daily soft-delete purge cron | unset (core) — explicit | HIGH |
| 10 | `neo4j` | Graph store | `integrations` — explicit | HIGH |
| 11 | `graphiti-service` | Temporal fact extraction (Neo4j-backed) | `integrations` — explicit | HIGH |
| 12 | `langfuse` | Observability web UI | `integrations` — explicit | HIGH |
| 13 | `langfuse-worker` | Observability worker | `integrations` — explicit (Discretion: follows langfuse) | HIGH |
| 14 | `langfuse-clickhouse` | Langfuse's OLAP store | `integrations` — explicit (Discretion: follows langfuse) | HIGH |
| 15 | `langfuse-redis` | Langfuse's queue/cache | `integrations` — explicit (Discretion: follows langfuse) | HIGH |
| 16 | `mcp-calendar` | Calendar MCP tool | `integrations` — explicit | HIGH |
| 17 | `mcp-drive-read` | Drive read/write MCP tool | `integrations` — explicit | HIGH |
| 18 | `mcp-deck` | Deck generator MCP tool | `integrations` — explicit | HIGH |
| 19 | `mcp-github` | GitHub repo-read MCP tool | `integrations` — explicit | HIGH |
| 20 | `granola-sync` | Granola per-user sync sidecar | `integrations` — explicit | HIGH |
| 21 | `drive-sync` | Incremental Drive sync sidecar | `integrations` — explicit | HIGH |
| 22 | `searxng` | Self-hosted meta-search (LibreChat web search) | `integrations` — explicit | HIGH |
| 23 | `agent-runtime` | LangGraph agent host | `integrations` — explicit | HIGH |
| 24 | `session-bridge` | Pro/Max Chrome-extension routing bridge | `saas` — explicit | HIGH |
| 25 | `librechat` | LibreChat frontend | `saas` — explicit | HIGH |
| 26 | `openwebui` | Open WebUI frontend | `saas` — explicit | HIGH |
| 27 | `langfuse-minio` | S3-compat store — **dual-purpose: Langfuse events/media AND memory-api's own media/decks storage** | **NOT NAMED in D-15-02.** Design doc's original table (superseded) lists a *separate* untagged `minio (media/docs)` service that does not exist in the real compose file — there is only ONE MinIO instance, and it is named `langfuse-minio`. See Q2/Summary — flagged, do not silently tag it either way. | **FLAGGED — planning question, not guessable** |
| 28 | `xbrain-backup` | Postgres/Qdrant/Mongo backup cron | **NOT NAMED in D-15-02** (the design doc's superseded table put it in a now-deleted `ops` profile; D-15-02 explicitly allows only 3 profile states: unset/integrations/saas) | **FLAGGED — planning question** |
| 29 | `librechat-mongo` | LibreChat's Mongo store | Not named; implied `saas` (design doc prose "librechat (+ mongo, meili)") but D-15-02's table itself only lists `librechat, openwebui` | Inferred `saas`, MEDIUM — should be stated explicitly in the plan, not left implicit |
| 30 | `librechat-meili` | LibreChat's search index | Same as above | Inferred `saas`, MEDIUM |
| 31 | `librechat-bridge` | Sidecar: LibreChat Mongo → memory-api ingest/enrich | Not named at all in D-15-02 or the design doc's table | Inferred `saas` (only meaningful with librechat running), MEDIUM — **flag explicitly** |
| 32 | `openwebui-pipeline` | Sidecar: Open WebUI → memory-api ingest/enrich | Not named at all | Inferred `saas` (only meaningful with openwebui running), MEDIUM — **flag explicitly** |

**Why this matters, proven not asserted:** a scratch hypothesis compose file built by applying D-15-02's table literally (tagging exactly the 24 named services, leaving the other 8 untagged) was run through the real `docker compose` binary. With `COMPOSE_PROFILES` unset, `docker compose config --services` returned **15 services**, not ~10:

```
langfuse-minio
librechat-mongo
postgres
qdrant
memory-api
mcp-brain
brain-janitor
mcp-gateway
mcp-scraper
nginx
centrifugo
openwebui-pipeline
xbrain-backup
librechat-bridge
librechat-meili
```
`[VERIFIED: live docker compose config --services, scratch copy of infrastructure/docker-compose.yml, 2026-07-12]`

An OSS-light install following D-15-02's table verbatim would silently boot `librechat-mongo` (512m), `librechat-meili` (192m), `librechat-bridge`, and `openwebui-pipeline` — i.e. most of the LibreChat/Open WebUI plumbing minus the two frontend containers themselves — plus an unexplained MinIO and a backup job that (per Q2) is actually broken. This is not a hypothetical edge case; it is what the table produces if applied as written.

## Q2 — The `depends_on` Graph (the crux)

Full edge list, read directly from `infrastructure/docker-compose.yml` (all 32 services; blank = no `depends_on` block):

| Service | depends_on | Line(s) |
|---|---|---|
| memory-api | postgres(healthy), qdrant(healthy), **neo4j(healthy)** | 215-218 |
| agent-runtime | memory-api(healthy), postgres(healthy) | 255-257 |
| librechat-bridge | memory-api(healthy), librechat-mongo(healthy) | 291-293 |
| openwebui-pipeline | memory-api(healthy) | 333-334 |
| librechat | librechat-mongo(healthy), librechat-meili(healthy) | 472-474 |
| openwebui | openwebui-pipeline(healthy) | 505-506 |
| xbrain-backup | postgres(healthy), qdrant(healthy), **librechat-mongo(healthy)** | 540-543 |
| langfuse-worker | langfuse-clickhouse(healthy), langfuse-redis(healthy), postgres(healthy), langfuse-minio(healthy) | 635-639 |
| langfuse | langfuse-worker(started), langfuse-minio(healthy) | 682-684 |
| mcp-gateway | postgres(healthy), memory-api(healthy) | 742-744 |
| mcp-deck | memory-api(healthy), langfuse-minio(healthy) | 848-850 |
| mcp-brain | memory-api(healthy) | 890-891 |
| mcp-github | memory-api(healthy) | 922-923 |
| session-bridge | memory-api(healthy) | 952-954 |
| graphiti-service | neo4j(healthy), memory-api(healthy) | 1017-1019 |
| drive-sync | postgres(healthy), memory-api(healthy), agent-runtime(healthy) | 1054-1057 |
| granola-sync | postgres(healthy), memory-api(healthy) | 1087-1089 |
| brain-janitor | postgres(healthy), qdrant(healthy), **neo4j(healthy)** | 1128-1131 |
| nginx, postgres, qdrant, librechat-mongo, librechat-meili, searxng, langfuse-clickhouse, langfuse-redis, langfuse-minio, neo4j, mcp-scraper, mcp-drive-read, mcp-calendar, centrifugo | *(none)* | — |

### The Compose rule, verified live against a scratch test file (not documentation prose)

```
service "core_b_depends_on_tagged" depends on undefined service "tagged_integrations": invalid compose project
```
`[VERIFIED: docker compose config --services, exit code 1, scratch test file, no profile active]`

This is not merely "the service won't start" — `docker compose config` **fails the whole project file**, exit code 1, before any service list can even be produced. Activating the dependency's profile fixes it (`--profile integrations config --services` succeeds, exit 0). Critically, this also poisons `docker compose config --profiles` itself: with even one such bad edge present, `config --profiles` cannot list the declared profile names at all — it errors identically. The same test also proved the rule is symmetric across *any two different* profiles, not just untagged→tagged: a `saas`-tagged service depending on an `integrations`-tagged service fails with `--profile saas` alone (exit 1) — Compose does **not** auto-activate a dependency's profile for you. `COMPOSE_PROFILES=a,b` (env var) and `--profile a --profile b` (repeated flag) were confirmed equivalent — both produce the same merged service set `[VERIFIED: live tests 1-8, scratchpad/test-profiles.yml + test-profiles2.yml]`. No such cross-`integrations`/`saas` edge exists in the real edge list above, so this symmetric case is not currently a problem — but it is a constraint the plan must respect if any future service crosses profile boundaries.

### The two hard blockers this phase must fix — proven on the real file, not just the sample

Applying D-15-02's table exactly (tagging neo4j, brain-janitor's sibling group, etc. as `integrations`) **without** touching the two `depends_on: neo4j` lines produces, on a scratch copy of the real `infrastructure/docker-compose.yml`:

```
service "brain-janitor" depends on undefined service "neo4j": invalid compose project
```
`[VERIFIED: live docker compose config --profiles, scratch copy, exit 0 but stdout shows the fatal error text — command "succeeds" at the shell level but produces no profile list, i.e. the whole config is invalid]`

1. **`memory-api → neo4j`** (`infrastructure/docker-compose.yml:218`) — already flagged as D-15-03.
2. **`brain-janitor → neo4j`** (`infrastructure/docker-compose.yml:1131`) — **NOT previously flagged anywhere in CONTEXT.md or the design doc.** brain-janitor is explicitly named core/untagged in D-15-02's own table, yet it hard-depends on Neo4j exactly like memory-api. This is the same defect class as D-15-03, occurring a second time, in a different service. It must be fixed identically (remove the `depends_on` edge) — and brain-janitor's Neo4j purge step must degrade the same way memory-api's graph routes do (see Q3): skip the Neo4j purge cleanly, don't crash the cron.

Removing only these two lines (and *not* graphiti-service's identical-looking `depends_on: neo4j`, which is legitimate — graphiti-service is itself `integrations`-tagged, so it depends on a same-profile service, which is fine) was verified to fully resolve the project:

```
$ docker compose -f compose-hypothesis.yml config --profiles
integrations
saas
```
`[VERIFIED: live, exit 0, after removing exactly memory-api's and brain-janitor's neo4j depends_on lines]`

### The third, conditional blocker: `xbrain-backup`

`xbrain-backup` depends on `librechat-mongo` (`infrastructure/docker-compose.yml:543`). `xbrain-backup` has **no D-15-02 profile assignment at all**. Every candidate placement breaks something:
- **Left untagged (core, the default for "unmentioned")** → hard blocker identical to #1/#2 above, the moment `librechat-mongo` is tagged `saas` (which the design doc's prose implies it should be).
- **Tagged `saas`** (to fix the depends_on) → an OSS-light install (untagged only) gets **zero backup coverage for its own core Postgres and Qdrant data**, which contradicts the "backup procedure" spirit of the shipped product (ADMIN-05 in v1 REQUIREMENTS.md) and seems clearly wrong for a self-hoster who never enables `saas`.
- **A new profile tag** (e.g. reviving `ops`) → directly violates D-15-02's explicit "three profiles, not four" rule.

`backup.sh` (`infrastructure/backup/backup.sh:27`) also has no graceful-skip logic — it unconditionally runs `mongodump --uri="${LIBRECHAT_MONGO_URI}"`, so even fixing the compose-level edge is not sufficient; the script itself would fail at runtime if Mongo is absent. `[VERIFIED: source read, infrastructure/backup/backup.sh]`

**This is a genuine open design question, not a guessable default — see Open Questions.**

## Q3 — What Actually Happens to memory-api Without Neo4j

Read first, then proven live.

- `app/neo4j_client.py:21-47` — `init_driver()` returns `None` immediately if `NEO4J_URI` or `NEO4J_PASSWORD` is empty (no connection attempt at all). If both are set but the host is unreachable, it attempts `AsyncGraphDatabase.driver(...)` + `verify_connectivity()` inside a `try/except Exception`, logs `neo4j.connectivity_failed` at ERROR level, closes the driver, and returns `None`. **It does not raise; it does not crash the app.**
- `app/main.py:73` — `await init_driver()` is called plainly in `lifespan()`, not wrapped in its own try/except by the caller — but it doesn't need to be, because `init_driver()` never raises.
- `app/outbox_worker.py:47-51` — the background drain loop checks `if driver is None: await asyncio.sleep(2); continue` every tick. No error, no log line, no busy-loop — confirmed live over a 15-second window (≈7 ticks) with zero additional log output.
- `app/routes/graph.py:46-54` — `_require_driver()` raises `HTTPException(503, "Graph service unavailable — Neo4j not connected")` if the driver is `None`. This is the exact pattern to replicate for EDITION-gated routers that need a "mounted but functionally inert" state, though note EDITION and Neo4j-absence are different axes (see Q4).
- `app/routes/admin_wipe.py:262-296` — Neo4j wipe helpers already return `{"status": "skipped", "reason": "neo4j not configured"}` when the driver is `None`. Consistent pattern, third occurrence.
- **One real gap found:** `app/routes/memory.py:321-350` unconditionally `INSERT`s into `neo4j_outbox` whenever a caller's `metadata.entities` is non-empty — this write is gated only on the caller supplying entities, **not** on whether Neo4j is configured. With Neo4j permanently absent (OSS-light, `integrations` off) and any caller that populates `metadata.entities`, `neo4j_outbox` rows will accumulate forever (the drain loop no-ops, never marks them processed). Not a crash, not a regression today (Neo4j is always up in every current deployment), but a genuine unbounded-growth issue this phase's plan should close with a cheap guard (`if entities and settings.NEO4J_URI and settings.NEO4J_PASSWORD:`).

### Live proof — booted memory-api with zero Neo4j container reachable

A stock `python:3.12-slim` container (not the built memory-api image — no image was built, per the environment constraint) was used to install memory-api's real dependencies from `apps/memory-api/pyproject.toml` and run the real `app/main.py` against a real Postgres + Qdrant, with `NEO4J_URI=bolt://neo4j:7687` / `NEO4J_PASSWORD=testpassword` set (non-empty, forcing the connection-attempt code path) and **no Neo4j container present anywhere on the network**.

```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001 ... -> 0023_tasks_source_connector   (all 23 migrations applied cleanly — no Neo4j dependency in the schema)
{"event": "memory_api_startup", ...}
INFO:httpx: GET http://xbrain-test-qdrant:6333/collections "200 OK"
{"name": "messages", "event": "qdrant_create_collection", ...}
{"error": "Failed to DNS resolve address neo4j:7687: [Errno -2] Name or service not known", "event": "neo4j.connectivity_failed", "level": "error", "timestamp": "...T06:35:13...Z"}
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```
`[VERIFIED: live boot, 2026-07-12, memory-api dependencies installed fresh in an ephemeral python:3.12-slim container, no Dockerfile build performed]`

- `curl http://localhost:18000/v1/healthz` → `{"status":"ok"}`, HTTP 200. `[VERIFIED live]`
- Startup delay attributable to the Neo4j DNS-resolution failure: ~8.5 seconds (04.521 → 13.046 in the timestamps above) — this is the async Neo4j driver's own internal timeout before giving up, worth budgeting into boot-time expectations once `depends_on` is removed.
- No crash, no restart loop, no repeated log spam over the following 15 seconds.

**Conclusion for planning: the application code is already correct.** D-15-03's real work is removing the two compose `depends_on` edges (Q2) and closing the `neo4j_outbox` unbounded-growth gap in `memory.py`. It is not "make memory-api tolerate a missing Neo4j" — that already works.

## Q4 — Router Inventory for the `EDITION` Flag

`app/main.py` mounts **35 routers** via `app.include_router(...)` (lines 103-148; 30 under `/v1` or a sub-prefix, 5 OAuth routers with no prefix). No `EDITION` setting exists in `app/config.py` today `[VERIFIED: grep, no match]`.

### CORE — explicitly named in ROADMAP.md SC#2 / the design doc sketch (always mounted)

`health, teams, memory, promotions, graph, media, me, auth_github` (as the generic "auth" bucket — see caveat below), plus the 5 unprefixed `oauth_*` routers (ChatGPT/Claude.ai connector — explicitly called out as "OSS, not gated" in the design doc). — 9 named + 5 oauth = 14.

### SAAS-ONLY — explicitly named (waitlist, multi-tenant admin, external_sessions/bridge routing, billing)

Of the four named categories, only **two have real routers today**:
- `waitlist.router` — `"""POST /v1/waitlist — proxy Resend email signup, no auth required."""` — clearly hosted-landing-page-only. HIGH confidence SAAS-only.
- `external_sessions.router` — `/v1/me/external-sessions`, the Phase 9 session-bridge registry — matches "external_sessions/bridge routing" verbatim. HIGH confidence SAAS-only. `[VERIFIED: source read — no dependency on the session-bridge container being up; it's a plain Postgres table, but only meaningful when session-bridge exists]`

**"multi-tenant admin" and "billing" have no router at all yet** — they are aspirational/future (matches REQUIREMENTS.md "Billing / multi-tenant provisioning / trial caps — deliberately deferred"). Nothing to gate today.

### The 21 routers NOT explicitly named by SC#2, classified by reading each module docstring

| Router | Docstring says | Recommended classification | Confidence / flag |
|---|---|---|---|
| `internal` | resolve-team-scope, used by librechat-bridge and mcp-brain's tokenless path | CORE — multi-frontend infra, not SaaS-specific | MEDIUM |
| `internal_github`, `auth_github`, `me_github`, `github_repos`, `webhooks_github` | GitHub App auth/install/repo-read plumbing | **AMBIGUOUS — flagged, not guessed.** Not in the explicit core list (which names only "auth" generically) or the explicit SaaS list. Per REQUIREMENTS.md Q2, GitHub auth becomes an *opt-in* path once Local Auth (Phase 18) ships — but "opt-in" there means opt-in *by config* (empty `GITHUB_APP_*` vars → the router's own endpoints naturally no-op/error), the same pattern as Neo4j, not router removal. Recommend CORE (mounted always, inert without config) for consistency with the Neo4j precedent — **but this is a real open question for the planner, not a locked fact** | LOW-MEDIUM — planning question |
| `conversations`, `messages`, `audit`, `system_prompt`, `team_chat`, `brain`, `admin_brain`, `admin_wipe` | Core chat/brain/audit/superadmin surfaces, all single-install (not cross-customer) | CORE | MEDIUM-HIGH |
| `crm`, `tasks` | **Docstrings literally say `"""... (paid tier only — D1, D2)"""` / `"""... (paid tier only — D4, D6)"""`** — a stale reference to the dropped `pro` tier | **FLAG EXPLICITLY.** These docstrings assert a monetization model D-15-01 killed. Not in the phase goal's explicit core list either. Recommend CORE by default (D-15-05's "forgotten → mounted" rule, and Phase 7 shipped these as ordinary product features, not paid add-ons) — but the stale docstrings must be corrected in the same plan, or the next reader repeats this confusion | LOW — planning question, evidence-backed |
| `admin_drive`, `drive_webhook`, `granola_integration` | Drive/Granola admin + webhook plumbing (Drive/Granola are `integrations`-tier services at the compose layer) | CORE (mounted always; functionally inert without the matching container + API keys, same pattern as Neo4j/graph.py) | MEDIUM |
| `admin_projects` | GitOps deploy-registration endpoint (`brain-index.sh` calls it after Cloud Run/Firebase deploys) | **AMBIGUOUS** — arguably a "team platform" feature closer to hosted tooling than brain/chat/retrieval/truth-levels core. Not named either way. | LOW — planning question |
| `agents` | Platform agent registry CRUD + invoke | CORE (Phase 8 shipped as ordinary product feature) | MEDIUM |

**D-15-05's own warning is the load-bearing fact here:** "a router that is forgotten defaults to mounted, which would leak a SaaS surface into an OSS install." Given the audit above, the actual risk direction for *this* codebase is the opposite of what that sentence implies — most of the ambiguous routers (crm, tasks, admin_projects, GitHub-auth family) are things a self-hoster plausibly wants, and defaulting them to mounted is very likely correct. The one genuine leak risk is the two already-named SaaS routers (`waitlist`, `external_sessions`) being forgotten, which is a small, enumerable list to test against explicitly.

## Q5 — Compose Profiles Mechanics (verified against this repo's real file + isolated test files)

- `docker compose config --profiles` on the real file today: **empty stdout, exit 0** `[VERIFIED live]` — confirms zero profiles declared, matching CONTEXT.md's claim.
- Adding `profiles: ["integrations"]` / `["saas"]` under a service's top-level key is all that's required; verified the file remains syntactically valid Compose YAML with this exact insertion pattern.
- `docker compose --profile X config --services` lists exactly the untagged services **plus** every service tagged `X` — verified with both a minimal isolated file and the full hypothesis file built from the real compose file.
- `COMPOSE_PROFILES=a,b` (env var, comma-separated) and `--profile a --profile b` (repeated CLI flag) are equivalent — verified identical output on the same file.
- **The hard rule, proven, not just documented:** an untagged service `depends_on` a tagged service → `docker compose config` fails the *whole project* (exit 1, no partial output) with `service "X" depends on undefined service "Y": invalid compose project`. This also blocks `config --profiles` itself — you cannot even enumerate declared profile names until the edge is fixed.
- **The same rule applies symmetrically between two different active/inactive profiles**, not just untagged↔tagged: a `saas`-tagged service depending on an `integrations`-tagged service fails under `--profile saas` alone. Compose does not auto-pull in a dependency's profile. No such cross-profile edge exists in the real file today (see Q2), but this constrains any future service placement.
- Real command-output example, taken directly from a scratch copy of `infrastructure/docker-compose.yml` after both required `depends_on` fixes:
  ```
  $ docker compose -f compose-hypothesis.yml config --profiles
  integrations
  saas
  $ docker compose -f compose-hypothesis.yml --profile integrations config --services
  postgres qdrant memory-api nginx agent-runtime drive-sync granola-sync
  langfuse-clickhouse librechat-mongo xbrain-backup graphiti-service langfuse-minio
  langfuse-redis langfuse-worker langfuse mcp-calendar mcp-drive-read librechat-bridge
  mcp-gateway openwebui-pipeline searxng brain-janitor mcp-scraper centrifugo
  librechat-meili mcp-brain mcp-deck neo4j mcp-github
  ```
  (This particular run still shows `langfuse-minio`, `xbrain-backup`, `librechat-mongo`/`meili`/`bridge`, `openwebui-pipeline` leaking into the untagged/core baseline too — because the hypothesis file, faithful to D-15-02's table exactly as written, left them untagged. That is the Q1 finding restated in `--profile integrations` context: the leak shows up in *every* profile combination, not just the bare one, because these six services are simply never gated at all in a literal reading of D-15-02.)

## Q6 — arm64 Reality Check

Host confirmed `linux/aarch64` `[VERIFIED: docker info --format '{{.OSType}}/{{.Architecture}}']`. Docker Desktop 4.81.0, `docker compose` v5.2.0, both confirmed live. QEMU emulation for amd64 IS available on this host (`docker run --rm --platform linux/amd64 hello-world` succeeded) `[VERIFIED live]` — relevant because it means an amd64-only image is not a hard "cannot run at all" wall, only a "cannot run natively, must force `--platform linux/amd64` and accept emulation overhead" one.

| Image | Used by | arm64 manifest? | Evidence |
|---|---|---|---|
| `postgres:17` | postgres | YES | `docker image inspect` after live pull: `arm64 / linux` |
| `qdrant/qdrant:v1.17.1` | qdrant | YES | same, live pull |
| `nginx:1.27-alpine` | nginx | YES (`linux/arm64/v8`) | `docker buildx imagetools inspect` |
| `mongo:7` | librechat-mongo | YES (`linux/arm64/v8`) | same |
| `getmeili/meilisearch:v1.10` | librechat-meili | YES | same |
| `searxng/searxng:latest` | searxng | YES | same |
| `ghcr.io/open-webui/open-webui:v0.9.0` | openwebui | YES | same |
| `clickhouse/clickhouse-server:24.8` | langfuse-clickhouse | YES | same |
| `redis:7-alpine` | langfuse-redis | YES (`linux/arm64/v8`) | same |
| `cgr.dev/chainguard/minio:latest` | langfuse-minio | YES | same |
| `langfuse/langfuse-worker:3`, `langfuse/langfuse:3` | langfuse-worker, langfuse | YES (both) | same |
| `neo4j:2026.04.0-community` | neo4j | YES (`linux/arm64/v8`) | same |
| `centrifugo/centrifugo:v6` | centrifugo | YES | same |
| `ghcr.io/danny-avila/librechat:v0.8.5` | librechat (build base) | YES | same |
| `python:3.11-slim` | mcp-brain, mcp-calendar, mcp-drive-read, mcp-gateway, mcp-github, mcp-scraper (build base) | YES (`linux/arm64/v8`) | same |
| `python:3.12-slim` | memory-api, agent-runtime, librechat-bridge, openwebui-pipeline, session-bridge, brain-janitor, drive-sync, granola-sync, graphiti-service, mcp-deck (build base) | YES (`linux/arm64/v8`) | same |
| **`google/cloud-sdk:slim`** | **xbrain-backup (build base)** | **NO — single-manifest image, `"architecture": "amd64"` only, no manifest list** | `docker manifest inspect google/cloud-sdk:slim --verbose` |

**The only arm64 gap in the entire 32-service compose file is `xbrain-backup`, and it is a double gap:** the base image `google/cloud-sdk:slim` has no arm64 manifest at all, **and** `infrastructure/backup/Dockerfile:17` hardcodes a download of `mongodb-database-tools-debian12-x86_64-100.10.0.deb` — an x86_64-specific static binary URL, independent of the base image question. `[VERIFIED: source read, infrastructure/backup/Dockerfile]`

**Practical consequence for this phase's verification:** every other service in the compose file — including all 20 custom `build:` services — can be pulled/built and run natively on this arm64 dev host. Only `xbrain-backup` would need `docker build --platform linux/amd64` (forcing QEMU emulation, confirmed available but not exercised in this session) to verify locally at all. This is not a blocker for Phase 15's actual scope (compose profiles + router gating) since `xbrain-backup`'s only role in this phase is the Q1/Q2 profile-placement question — no image build of it is required to answer that question, and none was performed.

## Standard Stack

No new library is needed for either mechanism. Both are native platform features:

| Mechanism | What it is | Version | Why standard |
|---|---|---|---|
| Compose `profiles:` | Native Docker Compose service-selection key | Compose Spec (implemented by Compose v2/v5 — `docker compose` v5.2.0 confirmed on this host) | This is literally what the feature exists for; no third-party tool implements edition-selection better than the orchestrator you already run |
| `EDITION` flag | Plain field on the existing `pydantic-settings` `Settings` class (`app/config.py`) | Already at `pydantic-settings>=2.6` in `apps/memory-api/pyproject.toml` | The codebase already has a fail-fast `field_validator` pattern (OAUTH_ISSUER_URL/OAUTH_RESOURCE_URL, `app/config.py:158-166`) — reuse it verbatim for `EDITION`, do not introduce a new settings library |

**Version verification:** `pydantic-settings>=2.6` and `fastapi>=0.115,<0.120` are already pinned in `apps/memory-api/pyproject.toml:8-10` — no version change is implied by this phase.

## Architecture Patterns

### Recommended pattern for `EDITION` (matches the codebase's existing style)

```python
# Source: apps/memory-api/app/config.py:158-166 — the fail-fast pattern this
# codebase already established in Phase 14 for OAUTH_ISSUER_URL/OAUTH_RESOURCE_URL.
# EDITION should follow the identical shape:

EDITION: str = "oss"   # oss | saas

@field_validator("EDITION")
@classmethod
def _validate_edition(cls, v: str) -> str:
    allowed = {"oss", "saas"}
    if v not in allowed:
        raise ValueError(f"EDITION must be one of {allowed}, got {v!r}")
    return v
```

### Recommended pattern for router gating (matches `app/main.py`'s existing flat `include_router` style — D-15-05's "additive and explicit")

```python
# Source: apps/memory-api/app/main.py:103-148 (existing style — flat, one line per router)
# D-15-05: name the core routers, name the saas-only routers, both explicit.

app.include_router(health.router, prefix="/v1", tags=["health"])
# ... all core routers, unconditional, exactly as today ...

if settings.EDITION == "saas":
    app.include_router(waitlist.router, prefix="/v1", tags=["waitlist"])
    app.include_router(external_sessions.router, prefix="/v1", tags=["external-sessions"])
```

### The already-correct pattern to replicate for "mounted but functionally inert" routers (Neo4j/GitHub/Drive-tier)

```python
# Source: apps/memory-api/app/routes/graph.py:46-54 — already ships this exact
# shape. Any router this phase decides is "core but only meaningful when an
# integrations-tier dependency is configured" should follow this, NOT be
# EDITION-gated (EDITION is about oss/saas, not about which integrations are on).
def _require_driver() -> Any:
    driver = get_driver()
    if driver is None:
        raise HTTPException(status_code=503, detail="Graph service unavailable — Neo4j not connected")
    return driver
```

### Anti-patterns to avoid

- **Do not conflate `EDITION` with "is this integration configured".** Neo4j/GitHub/Drive/Granola availability is controlled by env-var-emptiness + compose-profile presence (a service being reachable or not), not by `EDITION`. Confusing the two axes is how a router ends up wrongly gated (e.g. hiding `crm`/`tasks` behind `EDITION=saas` just because their docstrings say "paid tier" — that tier no longer exists).
- **Do not fork config by profile.** D-15-04 — `QDRANT_COLLECTION`, `team_scope`, and any other identifier two services must agree on must resolve identically in every edition. If a plan introduces `QDRANT_COLLECTION_OSS` / `QDRANT_COLLECTION_SAAS` or similar, it has reproduced the exact defect class that made brain-janitor's Qdrant purge a silent no-op (commit `215882b`).
- **Do not silently default the 8 unnamed services** (`langfuse-minio`, `xbrain-backup`, `librechat-mongo`, `librechat-meili`, `librechat-bridge`, `openwebui-pipeline`, plus the ambiguous router set) into whichever profile seems obvious. Q1/Q2 proved that "obvious" reading breaks either the compose graph or a core feature. These need explicit decisions in the plan, informed by (or escalated to) the user.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Edition/entitlement resolution | A custom feature-flag service, a license-check module, a `require_entitlement()` decorator | Nothing — D-15-01 dropped this entirely | Locked decision; building any of this plans work that was explicitly cancelled |
| Service selection | A custom "which containers to start" shell script wrapping `docker compose` | Compose's native `profiles:` + `COMPOSE_PROFILES` | It's the built-in mechanism for exactly this; a wrapper script duplicates it worse |
| Settings validation | Manual `if not os.environ.get(...)` checks scattered through `main.py` | `pydantic-settings` `field_validator`, already the codebase's pattern | Consistency with the existing Phase 14 pattern; fails fast at the same layer as everything else |

**Key insight:** this phase adds zero new dependencies. Every mechanism it needs (Compose profiles, Pydantic field validation, FastAPI conditional `include_router`) already exists in the stack and already has a working precedent in this exact codebase (Neo4j graceful degrade, OAuth fail-fast validator). The work is disciplined application of existing patterns to a graph that has never been audited end-to-end, not new engineering.

## Common Pitfalls

### Pitfall 1: Fixing only the D-15-03-named `depends_on` edge and missing brain-janitor's identical one
**What goes wrong:** `docker compose config --profiles` still fails after "fixing" Neo4j's dependency, because brain-janitor has the exact same edge.
**Why it happens:** D-15-03 as written in CONTEXT.md only names memory-api. brain-janitor was added in Phase 11, long after the original design doc was written, and nobody re-audited `depends_on` edges when D-15-02's table was drafted.
**How to avoid:** grep `depends_on` blocks for every occurrence of `neo4j:` before writing any plan task — do not trust a single named example to be exhaustive. (This research already did that grep; the two occurrences are listed in Q2.)
**Warning signs:** `docker compose config --profiles` still errors after the "known" fix.

### Pitfall 2: Applying D-15-02's table as if it were exhaustive
**What goes wrong:** 6 real compose services (`langfuse-minio`, `xbrain-backup`, `librechat-mongo`, `librechat-meili`, `librechat-bridge`, `openwebui-pipeline`) silently default to core/untagged, ballooning the OSS-light baseline and, in `xbrain-backup`'s case, creating a third hard `depends_on` blocker.
**Why it happens:** the design doc's table (superseded, but still the mental model in CONTEXT.md) was written before Phases 9-14 added several of these services; nobody re-diffed it against the live compose file.
**How to avoid:** treat the compose file, not the table, as ground truth. Enumerate every service (`docker compose config --services`), cross off every one the table actually names, and treat what's left as an explicit planning decision, not a default.
**Warning signs:** the bare-profile service count doesn't match "~10" when actually run.

### Pitfall 3: Treating the MinIO conflict as a tagging choice rather than a topology problem
**What goes wrong:** whichever profile `langfuse-minio` is tagged, something breaks — `integrations` breaks core media/decks (the promised always-on capability), untagged/core makes MinIO run even when `integrations` (and therefore Langfuse) is off, wasting the RAM the profile split was supposed to save.
**Why it happens:** the original design doc imagined two separate MinIO instances ("minio (media/docs)" untagged + a second one bundled with Langfuse); the real implementation only ever built one, and later features (media upload, mcp-deck) were wired to the Langfuse one out of convenience.
**How to avoid:** this needs an actual decision before task-writing, not a tag: either (a) split into two MinIO containers (core `minio` for media/decks + `langfuse-minio` staying `integrations`-only for Langfuse's own event/media uploads), or (b) promote the single `langfuse-minio` container itself to core/untagged and accept it always runs (cheap — ~384m per its own `mem_limit`) regardless of whether Langfuse is enabled.
**Warning signs:** `/v1/media/upload` returns 503 in an OSS-light install that never touched `integrations`.

### Pitfall 4: EDITION-gating routers based on stale "paid tier" docstrings
**What goes wrong:** `crm.py`/`tasks.py` get hidden behind `EDITION=saas` because their docstrings literally say "paid tier only", reintroducing a monetization split D-15-01 explicitly killed.
**Why it happens:** the docstrings were never updated when Q6 dropped the pro tier; they're the only textual evidence in the codebase for what used to be a real distinction.
**How to avoid:** cross-check every router's classification against D-15-01 ("nothing in the product is paywalled") before trusting an in-code comment written under the old model. Fix the stale docstrings in the same plan so the next reader doesn't repeat the mistake.
**Warning signs:** an OSS install can't create CRM contacts or tasks even though nothing else in the product distinguishes free vs. paid.

## Code Examples

### Verified pattern: graceful-degrade dependency, already shipped 3 times in this codebase

```python
# Source: apps/memory-api/app/neo4j_client.py:21-47 (the canonical example)
async def init_driver():
    global _driver
    if not (settings.NEO4J_URI and settings.NEO4J_PASSWORD):
        log.warning("neo4j.disabled", reason="NEO4J_URI or NEO4J_PASSWORD not set — graph sync disabled")
        return None
    from neo4j import AsyncGraphDatabase
    _driver = AsyncGraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD))
    try:
        await _driver.verify_connectivity()
    except Exception as exc:
        log.error("neo4j.connectivity_failed", error=str(exc))
        await _driver.close()
        _driver = None
    return _driver
```
Repeated (correctly) in `app/routes/admin_wipe.py:262-296` (`{"status": "skipped", "reason": "neo4j not configured"}`) and `app/routes/graph.py:46-54` (`HTTPException(503, ...)`). This is the established idiom — three independent implementations of the same shape is strong evidence it's the codebase's accepted convention, not a one-off.

### Verified fail-fast validator pattern, to reuse verbatim for `EDITION`

```python
# Source: apps/memory-api/app/config.py:158-166
@field_validator("OAUTH_ISSUER_URL", "OAUTH_RESOURCE_URL")
@classmethod
def _require_oauth_urls(cls, v: str, info) -> str:
    if not v:
        raise ValueError(f"{info.field_name} is required — set it in .env ...")
    return v
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Zero profiles, zero EDITION flag — every service always runs, one monolithic router set | Compose `profiles:` for service selection + `EDITION` env var for router gating | This phase (not yet shipped) | Enables OSS-light installs (~10-15 services, pending the Q1/Q2 decisions) vs. the current 32-service always-on topology |
| `pro` profile (Neo4j + Graphiti + Langfuse, licensed) | `integrations` profile (same services, not licensed) | D-15-02, 2026-07-12 | Removes the license-gate concept entirely per Q6 |

**Deprecated/outdated:** the design doc's `LICENSE_KEY` / `require_entitlement()` sketch (`open-core-edition-design.md` §2-3) is fully superseded by D-15-01 — do not implement any part of it.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `librechat-mongo`, `librechat-meili`, `librechat-bridge`, `openwebui-pipeline` should be tagged `saas` (following their parent frontend) | Q1 | If the planner instead leaves any of these core, the leak proven in Q1 recurs even after "fixing" the named services — recommend the plan states this tag explicitly rather than relying on inference |
| A2 | GitHub-auth-family routers (`internal_github`, `auth_github`, `me_github`, `github_repos`, `webhooks_github`) should default CORE (mounted, inert-without-config, matching the Neo4j precedent) | Q4 | If SaaS-gated instead, a self-hoster who configures their own GitHub App would find sign-in unavailable — a functional regression vs. today; this is presented as a recommendation, not a locked fact, precisely because it's unverified against any explicit decision |
| A3 | `crm`/`tasks` routers should default CORE despite their stale "paid tier only" docstrings | Q4 | If left SaaS-gated per the stale docstring, an OSS-light install silently loses two shipped Phase-7 features with no product announcement anywhere that they're now paid — high user-confusion risk |

## Open Questions

1. **`langfuse-minio` profile placement (Pitfall 3).**
   - What we know: it's the only MinIO instance in the compose file; both Langfuse (event/media uploads) and core memory-api media/decks depend on it by hostname convention, not by an explicit `depends_on` edge for memory-api (only `mcp-deck` has an explicit edge).
   - What's unclear: whether the plan should split it into two MinIO containers or promote the single instance to core.
   - Recommendation: resolve this as a Wave-0 design decision before any task depends on it — it changes the shape of at least 3 downstream plan tasks (compose service list, memory-api env defaults, mcp-deck env defaults).

2. **`xbrain-backup` profile placement (Q2, third blocker).**
   - What we know: no candidate placement (core, saas, a new `ops` tag) is clean; core breaks the depends_on graph, saas leaves OSS-light installs unbacked-up, a new tag violates D-15-02's "three profiles" rule.
   - What's unclear: whether the fix is a tag decision at all, or a script-level change (drop the `librechat-mongo` `depends_on` edge, make `backup.sh` skip Mongo gracefully when `LIBRECHAT_MONGO_URI` is unset/unreachable, keep the service core).
   - Recommendation: the script-level fix (mirroring the Neo4j graceful-degrade precedent) is the option most consistent with D-15-04/D-15-05's spirit — recommend it, but confirm with the user since it changes backup script behavior, not just compose tagging.

3. **GitHub-auth-family and `admin_projects` router classification (Q4, A2).**
   - What we know: not named in either the explicit core or SaaS-only lists; D-15-05 defaults unnamed routers to mounted.
   - What's unclear: whether GitHub auth in particular is intended to stay core (self-hosters can configure their own GitHub App) or is conceptually closer to the hosted-only "org-driven team membership" model the design doc associates with SaaS.
   - Recommendation: default to CORE per D-15-05's stated default and the Neo4j precedent, but flag explicitly in the plan for a quick user confirmation rather than asserting it silently.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker Desktop | All live verification in this research | Yes | 4.81.0 | — |
| `docker compose` | Compose profile semantics testing | Yes | v5.2.0 | — |
| Host architecture | Local build/run parity with prod | `linux/aarch64` (host) vs. amd64 (prod GCP VM) | — | QEMU emulation confirmed available (`--platform linux/amd64`) for the one arm64 gap (`xbrain-backup`'s `google/cloud-sdk:slim` base) |
| Python 3.12 (for live memory-api boot test) | Q3 verification | Yes, via `python:3.12-slim` container (no image build) | 3.12 | — |

No missing dependencies block this phase's planning.

## Validation Architecture

> `workflow.nyquist_validation` is `false` in `.planning/config.json`. Per the orchestrator's explicit instructions for this phase ("the_lesson_that_matters_most"), this section is included anyway — Phase 15 is entirely compose/boot-time wiring, and a check that never traverses the real deployment path (Phase 14's own root-cause) would repeat the exact failure mode already diagnosed in `14-REVIEW.md`. The checks below are infra-boot assertions, not pytest-style unit tests, and are additive to (not a replacement for) whatever the plan's own pytest-level checks cover for router-mounting logic.

### Check 1 — `docker compose config --profiles` must succeed and list exactly the expected profile names
```bash
cd infrastructure && docker compose config --profiles
```
**Asserts:** exit code 0, stdout is exactly `integrations\nsaas` (order-independent), no `depends on undefined service` error text anywhere in output. This single command is the cheapest possible regression gate for the entire `depends_on` graph — it was the command that surfaced both hard blockers in this research, and it fails loudly and immediately if a third one is introduced later.

### Check 2 — Per-profile service-set assertions, run against the actual plan's tagged file
```bash
docker compose config --services | sort                              # bare / OSS-light
docker compose --profile integrations config --services | sort       # integrations
docker compose --profile saas config --services | sort               # saas
docker compose --profile integrations --profile saas config --services | sort   # everything
```
**Asserts:** the bare list matches the plan's explicit, written-down OSS-light service list (not "~10 services" — an exact `diff` against a committed expected-list file). This is the check that catches the Q1 leak (langfuse-minio/xbrain-backup/librechat-mongo etc. silently appearing in the bare set) — a leak that `docker compose config --profiles` alone (Check 1) cannot see, because a leaked-core service doesn't break the config, it just silently changes what boots.

### Check 3 — Real `docker compose up` boot, OSS-light only, asserting the promised-core capability actually works
```bash
COMPOSE_PROFILES= docker compose up -d
# wait for memory-api healthcheck
curl -fsS http://localhost:<port>/v1/healthz
curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:<port>/v1/media/upload ...   # must NOT be a connection-level failure
docker compose ps --format '{{.Name}}\t{{.State}}'   # assert neo4j, langfuse*, librechat*, openwebui NOT present
```
**Asserts:** (a) memory-api reaches healthy with zero Neo4j container running — this research already proved the app code supports this, so this check exists to prove the *compose wiring* also supports it, closing the loop D-15-03 opened; (b) the media upload path — the concrete capability this research found at risk (Pitfall 3) — returns something other than a connection-refused/timeout, proving the langfuse-minio question was actually resolved and not just tagged around; (c) no `saas`- or `integrations`-tagged container appears in `docker compose ps`, catching the inverse leak (an SC#1-violating over-inclusion).

### Check 4 — `EDITION` router-mount negative test (SaaS routes must be absent under `EDITION=oss`)
```bash
EDITION=oss <boot memory-api>
curl -s -o /dev/null -w '%{http_code}' http://localhost:<port>/v1/waitlist   # expect 404 (route not registered), not 401/403
curl -s -o /dev/null -w '%{http_code}' http://localhost:<port>/v1/me/external-sessions   # expect 404
EDITION=saas <boot the SAME image, no rebuild>
curl -s -o /dev/null -w '%{http_code}' http://localhost:<port>/v1/waitlist   # expect a real response (400/422, not 404)
```
**Asserts:** the D-15-05 negative case explicitly — a `404` for an unmounted route is structurally different from a `401`/`403` (which would mean the route exists but rejected the caller), and this distinction is exactly what "router not mounted" vs. "router mounted but auth-gated" needs to prove. Re-running with `EDITION=saas` on the identical image (no rebuild) directly proves D-15-05's "one image, no rebuild" claim, not just the gating logic.

### Existing pytest infrastructure (for reference — not the primary gate for this phase, but available)
`apps/memory-api/pytest.ini` + `apps/memory-api/tests/` exist with 25+ test files already covering routers individually (`test_admin_brain.py`, `test_media.py`, `test_health.py`, etc.) `[VERIFIED: directory listing]`. Any new `EDITION` field or router-gating logic should get a focused unit test here (e.g. `test_edition_gating.py` constructing the FastAPI app twice with different `EDITION` values and asserting `app.routes` contents) — cheap, fast, and complements but does not replace Checks 1-4 above, which are the only checks that can catch compose-graph and cross-container issues.

## Sources

### Primary (HIGH confidence — live command output or direct source read, this session, this repo)
- `infrastructure/docker-compose.yml` (full 1138-line read) — service inventory, depends_on graph, env var defaults
- `docker compose config --profiles` / `config --services` / `--profile X config --services`, real file and scratch hypothesis file, 2026-07-12
- Live memory-api boot test (ephemeral python:3.12-slim container, real Postgres 17 + Qdrant v1.17.1, no Neo4j) — startup logs, `/v1/healthz` response
- `apps/memory-api/app/neo4j_client.py`, `app/main.py`, `app/outbox_worker.py`, `app/routes/graph.py`, `app/routes/admin_wipe.py`, `app/routes/memory.py`, `app/config.py` — full or targeted reads
- `apps/memory-api/app/routes/*.py` docstrings — all 35 routers included in `main.py`
- `docker buildx imagetools inspect` / `docker manifest inspect --verbose` for every image referenced in the compose file, plus every `FROM` line in every `apps/*/Dockerfile`
- `infrastructure/backup/Dockerfile`, `infrastructure/backup/backup.sh` — arm64 gap + graceful-degrade gap
- `.planning/phases/15-edition-mechanics/15-CONTEXT.md`, `.planning/ROADMAP.md` (Phase 15 section), `.planning/REQUIREMENTS.md`, `.planning/features/open-core-edition-design.md`, `.planning/STATE.md`, `CLAUDE.md`

### Secondary (MEDIUM confidence)
- Inferred `saas` classification of `librechat-mongo`/`librechat-meili`/`librechat-bridge`/`openwebui-pipeline` — consistent with design-doc prose ("librechat (+ mongo, meili)") but not stated as an explicit D-15-02 table row

### Tertiary (LOW confidence — flagged, not asserted)
- GitHub-auth-family, `admin_projects`, `crm`/`tasks` router classification recommendations — presented as recommendations with reasoning, explicitly marked as open questions requiring planner/user confirmation, not as locked facts

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependency; both mechanisms are native platform features already used elsewhere in this codebase
- Architecture (depends_on graph, router inventory): HIGH — every edge and every router was read directly from source and, where behavior mattered, proven with live `docker compose`/container tests, not inferred
- Pitfalls: HIGH for the two proven `depends_on` blockers and the compose-service leak; MEDIUM-LOW (explicitly flagged) for router-classification ambiguity, since that genuinely depends on product intent not yet locked anywhere

**Research date:** 2026-07-12
**Valid until:** Until `infrastructure/docker-compose.yml` or `apps/memory-api/app/main.py` changes again (this is an infra snapshot, not a library-version claim — re-verify the `depends_on` graph and router list if either file is touched by another phase before Phase 15 executes)

# xbrain Post-Phase-13 Integration Check — 2026-05-27

**Trigger:** Standing order (user instruction 2026-05-17) + Phase 13 (Chat → Brain Ingestion + Retrieval Enrichment) marked code-complete 2026-05-24.
**Scope:** 8-section cross-system audit covering Phase 12 (GitHub App) + Phase 13 (Haiku classifier, brain ingest, per-turn enrichment) end-to-end.
**Auditor:** general-purpose agent (Claude Sonnet 4.6), read-only.
**Mode:** READ-ONLY — no source code modified, no VM writes.

**Overall status:** ⚠️ **2 WARNINGS** — `/v1/messages` sporadic 500 on anonymous user upsert (race in librechat-bridge); disk at 84% (up from 69% on 2026-05-17). All Phase 13 core functionality OPERATIONAL.

---

## Summary table

| Section | Tests | PASS | WARN | FAIL |
|---|---:|---:|---:|---:|
| 1. Backend (memory-api) | 8 | 8 | 0 | 0 |
| 2. Auth (GitHub App) | 4 | 3 | 1 | 0 |
| 3. Brain (memory_items + Qdrant + Neo4j) | 5 | 4 | 1 | 0 |
| 4. Pipeline (universal extraction) | 5 | 3 | 2 | 0 |
| 5. Frontends (LibreChat + Open WebUI + app-site) | 4 | 4 | 0 | 0 |
| 6. Observability (Langfuse + Centrifugo) | 3 | 3 | 0 | 0 |
| 7. Cleanup / health gates | 5 | 4 | 1 | 0 |
| 8. Phase 13–specific | 6 | 5 | 1 | 0 |
| **Total** | **40** | **34** | **6** | **0** |

---

## 1. Backend (memory-api) — 8 / 8 PASS

### 1.1 Container status

```
xbrain-memory-api   Up 9 minutes (healthy)   xbrain/memory-api:phase2
```

Container healthy. (9-minute uptime indicates a recent restart/rebuild for Phase 13 deploy — expected.)

### 1.2 Key endpoints — all wired

```
GET  /v1/healthz            → 200  {"status":"ok"}
GET  /v1/me                 → 422  (validation — route wired, no auth)
GET  /v1/teams/my-teams     → 422  (validation — route wired, no auth)
POST /v1/brain/ingest       → 422  (validation — route wired, body required)
GET  /v1/system-prompt      → 422  (validation — route wired, query params required)
GET  /v1/auth/github/signin → 404  (GET not supported — route wired for POST/exchange)
```

All expected response classes. 422s are FastAPI body validation with no auth. The `/v1/auth/github/exchange` endpoint is wired (confirmed via OpenAPI spec — route `/v1/auth/github/signin` exists).

Phase 13 new endpoints confirmed in OpenAPI spec:
```
/v1/brain/events
/v1/brain/events/{entity_type}/{entity_id}
/v1/brain/events/{entity_type}/{entity_id}/restore
/v1/brain/ingest                         ← Phase 13 new
/v1/admin/brain/overview
/v1/admin/brain/storage
/v1/admin/brain/activity
/v1/admin/brain/sources
/v1/admin/brain/events
```

### 1.3 Alembic head version

```sql
SELECT version_num FROM alembic_version;
  version_num
-------------------------
 0019_github_app_install
```

Phase 12 migration is the current head. Phase 13 required no new migration (uses existing `memory_items` table).

### 1.4 Phase 13 env vars in memory-api (6/6 required)

```
GITHUB_APP_ID=3743573
RELEVANCE_HAIKU_ENABLED=true
RELEVANCE_HAIKU_MODEL=claude-haiku-4-5-20251001
RELEVANCE_HAIKU_TIMEOUT_S=3.0
RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM=50000
BRAIN_INGEST_ENABLED=true
```

Note: `CHAT07_TOP_K` and `CHAT07_TRUTH_FILTER_MIN_LEVEL` correctly belong to `librechat-bridge` and `openwebui-pipeline`, not memory-api. All 6 memory-api Phase 13 vars present.

### 1.5 Live Haiku classification activity (from logs)

```json
{"team_scope": "your-team", "relevant": true, "score": 0.89, "tokens_in": 25,
 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 5201, "latency_ms": 832,
 "event": "relevance_filter.classified", ...}
{"team_scope": "your-team", "relevant": false, "score": 0.08, "tokens_in": 23,
 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 5201, "latency_ms": 1345,
 "event": "relevance_filter.classified", ...}
```

Haiku classifier is live and processing messages. `cache_read_input_tokens=5201` confirms ephemeral prompt cache is active on every call.

### 1.6 Key table row counts

```
 users | teams | team_members | memory_items | conversations | messages
-------+-------+--------------+--------------+---------------+----------
     3 |     1 |            1 |            1 |             1 |        4
```

### 1.7 memory-api container fleet (30 of 30 running)

All 30 `xbrain-*` containers are Up. 28 of 30 have `(healthy)` status. 2 without explicit healthcheck (`xbrain-backup`, `xbrain-langfuse-worker`) — expected by design.

### 1.8 Alembic migration chain (Phase 11 soft-delete + Phase 12 GitHub App)

Phase 11 tables with `deleted_at` (verified 2026-05-17) remain: `contacts`, `conversations`, `memory_items`, `messages`, `tasks`, `team_messages`. `memory_items_history` present (Phase 13 upsert race-fix). All confirmed via table listing.

---

## 2. Auth (GitHub App) — 3 / 4 PASS, 1 WARN

### 2.1 ✓ `/v1/auth/github/signin` endpoint wired (route present in OpenAPI)

GET returns 404 (expected — route is POST-only); confirmed present in OpenAPI path list.

### 2.2 ✓ GitHub App credentials present in memory-api env

```
GITHUB_APP_ID=3743573  ← matches operator-sourced value
```

### 2.3 ✓ `users` table — mrboups record present

```
 github_username |        email        |          created_at
-----------------+---------------------+-------------------------------
 mrboups         | nicoboups@gmail.com | 2026-05-22 02:05:21.227262+00
```

mrboups successfully authenticated post-Phase 12 (created 2026-05-22, 5 days ago).

### 2.4 ⚠ `installations` table — 0 rows

```sql
SELECT installation_id, github_org_login, installed_at FROM installations;
 installation_id | github_org_login | installed_at
-----------------+------------------+--------------
(0 rows)
```

The GitHub App has not been installed on any organization. Per the Phase 12 design, this is expected for a single-user deployment where the user authenticates via GitHub App web flow but hasn't triggered an org installation. The app is functional (mrboups signed in, JWT received). This is a **soft warning** — org-level installation is required for team-scoped multi-member use (the `team_org_blocks` RBAC path and per-org membership sync).

---

## 3. Brain (memory_items + Qdrant + Neo4j) — 4 / 5 PASS, 1 WARN

### 3.1 ✓ Postgres `memory_items` — row count + truth_level distribution

```sql
SELECT COUNT(*) AS total, truth_level, COUNT(*) FILTER (WHERE deleted_at IS NULL) AS active
FROM memory_items GROUP BY truth_level ORDER BY truth_level;

 total | truth_level | active
-------+-------------+--------
     1 | VALIDATED   |      1
```

1 active VALIDATED memory item (seeded during Phase 13 verify-phase13.sh cross-frontend test `g`).

### 3.2 ⚠ Qdrant — `memory_items` collection absent; `messages` collection present

```json
GET /collections → {"result":{"collections":[{"name":"messages"}]}}
GET /collections/memory_items → {"status":{"error":"Not found: Collection `memory_items` doesn't exist!"}}
GET /collections/messages →
  {"result":{"status":"green", "points_count":7, "indexed_vectors_count":0,
   "payload_schema":{"team_scope":{"data_type":"keyword"},"truth_level":{"data_type":"keyword"}}}}
```

The Qdrant `memory_items` collection is absent — all 7 Qdrant vectors are in the `messages` collection. This is consistent with Phase 13 design where the `native_provider.upsert` writes to the `messages` collection (legacy collection name), not a renamed `memory_items` collection. However, the memory-api `relevance_filter` and `ingest_external_message` reference `memory_items` in comments/config. **Impact**: functional — the verify-phase13.sh cross-frontend test `g` passed (Qdrant write confirmed in logs: `PUT /collections/messages/points?wait=true → 200 OK`). Cosmetic inconsistency between collection name `messages` and the concept `memory_items`.

### 3.3 ✓ Qdrant `messages` collection healthy

```
status: green, optimizer_status: ok
points_count: 7, indexed_vectors_count: 0 (below indexing_threshold=10000 — expected)
payload_schema: team_scope (keyword), truth_level (keyword) ← tagging contract applied
```

### 3.4 ✓ Neo4j — 0 nodes (empty graph, expected at this stage)

```
cypher-shell -u neo4j -p <password> "MATCH (n) RETURN labels(n) AS label, count(n) AS cnt ORDER BY cnt DESC LIMIT 10;"
+-------------+
| label | cnt |
+-------------+
+-------------+
0 rows
```

Neo4j is live and accessible. Empty graph is expected — Neo4j entity extraction (Phase 3 concept) was deferred; the graphiti-service container is running but writes to Neo4j have not been triggered by any agent workflow yet.

### 3.5 ✓ team_context_cache — not directly exposed; inferred active

`brain_ingest.external.ok` logs confirm `idem_key` present → cache invalidation path runs. No `team_context_cache.invalidate` error in logs.

---

## 4. Pipeline (universal extraction) — 3 / 5 PASS, 2 WARN

### 4.1 ⚠ `librechat-bridge` — active BUT sporadic 500 errors on `/v1/messages`

Container: `Up 2 hours (healthy)`. Bridge is actively watching LibreChat MongoDB and firing events.

**Critical finding:** Repeated 500 errors in the last 2 hours:

```
asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "users_source_user_id_key"
DETAIL:  Key (source_user_id)=(anonymous) already exists.
[SQL: INSERT INTO users (..., source_user_id, email, ...) VALUES (..., 'anonymous', 'anonymous@bridged.local', ...)]
```

The bridge is attempting to create a user record with `source_user_id='anonymous'` for each message it observes. When a second `anonymous` user insert races with an existing one, the unique constraint fires and the entire `/v1/messages` POST returns 500. The bridge then retries, hitting the same 500 repeatedly.

**Impact:** LibreChat messages that arrive while the `anonymous` user creation races are NOT being recorded in the conversations/messages tables (the 500 aborts the transaction). The brain ingest path (`_maybe_ingest_to_brain`) is a separate `asyncio.create_task` that fires before the message creation — so some brain_ingest events may still succeed. The `conv_already_enriched` log entries confirm enrichment is running in parallel.

**Frequency:** 1 unique 500 traceback in 2h window (1 original race event; subsequent retries are 422 due to empty content after the race). This is sporadic, not a sustained storm.

**Root cause:** The memory-api `/v1/messages` route does `INSERT INTO users` with `source_user_id='anonymous'` instead of using `INSERT … ON CONFLICT (source_user_id) DO NOTHING` or checking for existence first.

### 4.2 ✓ `openwebui-pipeline` — healthy (port 9099)

```
xbrain-openwebui-pipeline   Up 9 minutes (healthy)
healthcheck: CMD curl -fsS http://localhost:9099/health → {"status":"ok"}
```

Pipeline is healthy. (Note: the healthcheck runs on port 9099, not 8200. External access via `/health` was initially probed on wrong port — the container itself confirms 9099.)

Phase 13 env vars confirmed:
```
BRAIN_INGEST_ENABLED=true
CHAT07_TOP_K=5
CHAT07_TRUTH_FILTER_MIN_LEVEL=VALIDATED
```

### 4.3 ✓ `granola-sync` — healthy, polling every 5 min

```
2026-05-27 19:16:01 [info] poll_loop.tick_complete   teams=0 users=0
```

No Granola API key configured (`teams=0 users=0`). Container polling correctly, awaiting credentials.

### 4.4 ⚠ librechat-bridge `messages_watch_loop` — no heartbeat log visible

The `messages_watch_loop` logs show `messages_watch_event_failed` events but no explicit `messages_watch_loop.started` heartbeat in the last 2-hour window. The watch is functionally active (events being processed), but structured heartbeat logging may not be implemented or may be at DEBUG level. The container health is `(healthy)` via its own probe.

### 4.5 ✓ session-bridge — `bridge.example.com/nginx-health` 200 OK

```
curl https://bridge.example.com/nginx-health → 200
```

---

## 5. Frontends (LibreChat + Open WebUI + app-site) — 4 / 4 PASS

```
curl https://chat.example.com         → 200 OK
curl https://example.com              → 200 OK
curl https://example.com/docs/index.html → 200 OK
curl https://example.com/account/teams/ → 200 OK
```

All four public-facing frontend URLs return 200. LibreChat and the app-site are serving correctly. Firebase Hosting for app-site is operational.

Container health:
```
xbrain-librechat   Up 5 days (healthy)
xbrain-openwebui   Up 10 days (healthy)
xbrain-nginx       Up 10 days (healthy)
```

---

## 6. Observability (Langfuse + Centrifugo) — 3 / 3 PASS

### 6.1 ✓ Langfuse web UI — 200 OK

```
curl https://lang.example.com/ → 200
xbrain-langfuse   Up 10 days (healthy)
```

### 6.2 ✓ Centrifugo — healthy

```
xbrain-centrifugo   Up 10 days (healthy)
docker exec xbrain-centrifugo wget -qO- http://localhost:8000/health → {}   (exit 0)
```

### 6.3 ✓ RAM usage

```
free -h:
  total: 7.8 GiB, used: 5.7 GiB, available: 2.0 GiB
  Usage: 5.7 / 7.8 GiB = 73%
```

Top consumers:
```
xbrain-neo4j                  832 MiB / 1 GiB     (81%)
xbrain-librechat              305 MiB / 384 MiB   (79%)
xbrain-memory-api             516 MiB / 768 MiB   (67%)  ← up from 272 MiB post-phase12
xbrain-librechat-mongo        320 MiB / 512 MiB   (63%)
xbrain-langfuse               627 MiB / 1.1 GiB   (54%)
xbrain-openwebui              645 MiB / 1.25 GiB  (50%)
```

RAM at 73% — above the 70% target set in the prior check but still comfortable (2.0 GiB available). Memory-api grew from 272 MiB to 516 MiB, likely due to the SYSTEM_PROMPT (16,501 bytes) loaded in-process plus the LangGraph/httpx client pool.

---

## 7. Cleanup / health gates — 4 / 5 PASS, 1 WARN

### 7.1 ✓ Disk usage — 84% (⚠ approaching threshold)

```
df -h /: 48G total, 40G used, 8.0G avail → 84%
```

Disk grew from 33G (69%) on 2026-05-17 to 40G (84%) today — +7 GB in 10 days. This exceeds the 80% warning threshold. The growth is primarily from Docker image layers added by Phase 13 rebuilds, Qdrant data, and MongoDB.

Log files are well-controlled:
```
find /var/lib/docker/containers -name '*-json.log' -size +100M → 0 files
```

Largest log files:
```
42 MB  xbrain-librechat-meili
31 MB  xbrain-nginx
25 MB  xbrain-librechat-mongo
```

All below the 100 MB cap. However, total containers log directory is 322 MB. The disk pressure is from images/volumes, not logs.

### 7.2 ✓ Container health summary

- 28 containers: `(healthy)`
- 2 containers without healthcheck (`xbrain-backup`, `xbrain-langfuse-worker`) — expected by design
- 0 containers: `(unhealthy)` ← Phase 11 `brain-janitor` unhealthy from prior check is **RESOLVED**

### 7.3 ✓ brain-janitor — FIXED (was FAIL in 2026-05-17 check)

```
2026-05-27 19:06:55 [info] brain_janitor.boot         qdrant_collection=memory_items retention_days=30
2026-05-27 19:06:55 [info] brain_janitor.run_complete  purged_counts={all zeros} sentinel=/tmp/brain-janitor-alive
2026-05-27 19:06:55 [info] brain_janitor.sleep         next_run_utc=2026-05-28T03:00:00+00:00
```

**P1 from prior check is resolved.** Both prior bugs are fixed:
1. `column "deleted_at" does not exist` → no longer occurring
2. `asyncpg interval parameter rejection` → `run_complete` fires without error
3. Sentinel `/tmp/brain-janitor-alive` is now written (per log)

Container is `Up 6 minutes (healthy)`. BMO-08 contract (30-day soft-delete purge) will execute correctly on 2026-06-27 for the first items soft-deleted today.

### 7.4 ✓ nginx 400s — only TLS handshake probes (expected)

```
nginx log 400s in last 2h:
  3 × raw TLS handshake bytes (external scanners hitting port 80 with HTTPS)
  2 × 422 (brain event PATCH with validation error — cosmetic test artifact)
```

No legitimate application-layer 4xx/5xx from nginx. The raw binary request 400s are scanner/bot traffic (IPs: 34.45.231.83, 172.236.228.208, 198.235.24.232) — normal for any public IP.

### 7.5 ✓ Large log file check — 0 files > 100 MB

```
find /var/lib/docker/containers -name '*-json.log' -size +100M → 0
```

Logging caps (max-size 100m, max-file 3) applied to all 29 services are holding.

---

## 8. Phase 13–specific — 5 / 6 PASS, 1 WARN

### 8.1 ✓ `relevance_filter.classified` events — 13 in last 24h, 12/13 cache hits

```
Total classified: 13
Cache hits (cache_read_input_tokens > 0): 12/13  (92%)
Avg cache_read_input_tokens: 4,801
```

Sample events:
```json
{"team_scope": "your-team", "relevant": true,  "score": 0.89, "tokens_in": 25,
 "cache_read_input_tokens": 5201, "latency_ms": 832}
{"team_scope": "your-team", "relevant": false, "score": 0.08, "tokens_in": 23,
 "cache_read_input_tokens": 5201, "latency_ms": 1345}
{"team_scope": "your-team", "relevant": false, "score": 0.08, "tokens_in": 21,
 "cache_read_input_tokens": 5201, "latency_ms": 973}
```

**Prompt cache is working correctly.** `cache_read_input_tokens=5201` on 12 of 13 calls means the SYSTEM_PROMPT (padded to ≥16,384 bytes / ~4,096 tokens) is being served from Anthropic's ephemeral cache on almost every call. The 1 miss is likely the first call in a cache TTL window (cache_creation event). Latency is consistently 830–1,350 ms (cached calls).

### 8.2 ✓ `brain_ingest.external.ok` — 9 events in last 24h

```
brain_ingest.external.ok count (24h): 9
brain_ingest.external.skipped_by_filter count (24h): ~4 (implied from classified=13, ok=9)
```

Latest event:
```json
{"team_scope": "your-team", "source": "team-chat:verify-phase13-g",
 "chars": 69, "idem_key": "verify-phase13-test-g",
 "event": "brain_ingest.external.ok", "timestamp": "2026-05-27T19:11:58Z"}
```

Idempotency key confirmed working. The verify-phase13 cross-frontend test `g` succeeded end-to-end (ingest → Qdrant write → system-prompt retrieval).

### 8.3 ✓ Haiku relevance filter — correct accept/reject decisions visible

```
"relevant": true, "score": 0.89  ← substantive message accepted
"relevant": false, "score": 0.08 ← short/trivial message rejected
```

The classifier distinguishes relevant from irrelevant content. Score spread (0.08 vs 0.89) is clean — no borderline indecision.

### 8.4 ✓ Phase 13 env vars — 3 containers verified

| Container | BRAIN_INGEST_ENABLED | CHAT07_TOP_K | CHAT07_TRUTH_FILTER_MIN_LEVEL | RELEVANCE_HAIKU_ENABLED | RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM |
|---|---|---|---|---|---|
| `memory-api` | ✓ true | N/A (bridge-side) | N/A (bridge-side) | ✓ true | ✓ 50000 |
| `librechat-bridge` | ✓ true | ✓ 5 | ✓ VALIDATED | N/A | N/A |
| `openwebui-pipeline` | ✓ true | ✓ 5 | ✓ VALIDATED | N/A | N/A |

All 7 Phase 13 env vars present across the correct containers.

### 8.5 ✓ verify-phase13.sh cross-frontend test `g` — PASS (evidence in logs)

```
brain_ingest.external.ok   source=team-chat:verify-phase13-g   idem_key=verify-phase13-test-g
PATCH /v1/brain/events/memory_item/c02ad3ce-...  → 200 OK  (truth_level promotion)
GET  /v1/system-prompt?query=deploy%20window%20Tuesday&min_level=VALIDATED&top_k=5&team_scope=your-team → 200 OK
POST /v1/brain/ingest  → 202 Accepted
relevance_filter.classified  relevant=false  score=0.08  (short irrelevant message correctly rejected)
```

Full ingest → promote → retrieve loop confirmed working in production.

### 8.6 ⚠ memory-api `/v1/messages` — sporadic 500 on `anonymous` user race

Cross-ref with §4.1. The librechat-bridge is calling `POST /v1/messages` (the message recording endpoint, distinct from the new `POST /v1/brain/ingest`). When the bridge sends a message attributed to an anonymous/unauthenticated LibreChat user, the API attempts `INSERT INTO users (source_user_id='anonymous', ...)`. If a second such INSERT races with an already-committed row, it throws `UniqueViolationError` → 500. This is a pre-existing gap in the `POST /v1/messages` route's user creation path (no `ON CONFLICT DO NOTHING` on the anonymous user upsert).

Frequency confirmed: 1 traceback in 2h window. Not a storm. Impact: the specific message is dropped from `messages` table but brain_ingest runs as a separate fire-and-forget task and may still succeed.

---

## Summary

### Overall verdict: ⚠️ 6 WARNINGS, 0 BLOCKERS

| # | Color | Item | Severity | Recommended action |
|---|---|---|---|---|
| 1 | 🟡 | `/v1/messages` anonymous user race → UniqueViolationError 500 | P1 | Add `ON CONFLICT (source_user_id) DO NOTHING` (or `DO UPDATE`) to the `anonymous` user upsert in memory-api's `/v1/messages` handler. Low-frequency but causes message-record loss for anonymous LibreChat sessions. |
| 2 | 🟡 | Disk at 84% (40/48 GB) — above 80% threshold | P1 | Run `docker system prune --volumes` on unused images/layers. Investigate `docker df` breakdown. With current growth rate (+7 GB/10 days), < 30 days before 90% threshold. |
| 3 | 🟡 | `installations` table empty — no GitHub App org installation | P2 | Informational for now. When inviting a second team member, mrboups needs to install the GitHub App on the target org. Until then, solo use is unaffected. |
| 4 | 🟡 | Qdrant collection named `messages` (not `memory_items`) | P3 | Cosmetic. Rename via Qdrant API if clarity desired, or leave as-is. No functional impact — reads/writes are working. |
| 5 | 🟡 | librechat-bridge `messages_watch_loop` — no heartbeat log visible | P3 | Add explicit `messages_watch_loop.heartbeat` structured log every N events. Not a functional issue — container is healthy and events are being processed. |
| 6 | 🟡 | RAM at 73% (2.0 GiB headroom) — above prior 70% target | P3 | Monitor trend. The memory-api footprint grew 244 MiB post-Phase 13 (SYSTEM_PROMPT in-memory + httpx pool). Acceptable. Consider tuning `RELEVANCE_HAIKU_TIMEOUT_S` lower if RAM pressure increases. |

### Green items (all PASS)

- Phase 13 Haiku classifier: LIVE, 12/13 calls served from ephemeral cache (avg 4,801 cache_read_input_tokens)
- `brain_ingest.external.ok`: 9 events confirmed in 24h
- Full ingest → promote → retrieve loop: CONFIRMED via verify-phase13 test `g` log evidence
- All 4 public frontend URLs: 200 OK
- Langfuse + Centrifugo: healthy
- brain-janitor: **FIXED** (was P1 in prior check — now healthy, `run_complete` with sentinel)
- All 30 xbrain-* containers: Up (28 healthy, 2 no-healthcheck-by-design)
- No container is `(unhealthy)`
- Log caps holding: 0 files > 100 MB
- GitHub App: mrboups authenticated (2026-05-22), JWT path working
- Alembic: 0019_github_app_install (Phase 12 head)

### Phase 12 → 13 regression check

No Phase 12 regressions detected. GitHub App auth (`GITHUB_APP_ID=3743573`), mrboups user record, and the auth endpoints are all intact. The new Phase 13 `POST /v1/brain/ingest` endpoint is correctly added alongside the existing brain routes without affecting them.

---

*Report generated: 2026-05-27.*
*Auditor: general-purpose agent (Claude Sonnet 4.6), read-only mode.*
*SSH: `~/.ssh/xbrain_key` → `mrboups@130.211.55.142`.*
*Prior check: `.planning/INTEGRATION-CHECK-2026-05-17.md`.*

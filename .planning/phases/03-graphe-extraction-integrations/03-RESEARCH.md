---
phase: 3
type: research
date: 2026-05-04
---

# Phase 3 — Technical Research

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- VM stays e2-standard-2 (8GB). No upgrade unless docker stats shows pressure.
- No new VM, no Langfuse split. Cost stays ~49€/mo.
- Memori is OUT. Extraction = Claude-based pattern from extract_facts.py.
- Pre-sync into memory-api (NOT live read at query time).
- Polling cron 5min via drive-sync Python sidecar. Uses changes.list with team-scoped tokens.
- 1 Drive folder mapped per team. Multi-folder deferred to Phase 4.
- File types: Google Docs/Sheets/Slides (export), PDFs (pypdf), Markdown. Images/blobs skipped.
- Sync flow: drive-sync fetches → ingestion-agent → facts to memory-api.
- Build custom Python sidecar for MCP gateway (FastAPI + MCP protocol). NOT mcp-proxy.
- MCP gateway ~150 lines: POST /tools/{name}/call, GET /tools, POST /admin/register.
- Auth pattern: mirrors agent-runtime acting_user_sub from promotion_manager.py.
- Audit: every tool call via POST /v1/audit-log.
- 3 MCP tools in Phase 3: scraper, drive-read, calendar. deck-service deferred.
- Neo4j rich model: Entity, Fact, User, Conversation nodes + 5 edge types.
- Outbox pattern for Neo4j sync. Facts in Postgres are SoT; Neo4j is read-replica.
- Entity extraction: Claude NER extending extract_facts.py. No spaCy, no separate NER service.

### Claude's Discretion
- Best Python library for Google Drive changes.list polling
- Best MCP protocol library for FastAPI integration
- Neo4j async vs sync driver choice
- Drive file deletion strategy (hard delete vs soft archive)
- Outbox worker: separate container vs in-process

### Deferred Ideas (OUT OF SCOPE)
- deck-service MCP tool
- Multi-folder per team Drive mapping
- Push webhooks for Drive
- OCR for scanned PDFs
- Drive write-back beyond simple text append
- MCP tool discovery from external registries
- Apache AGE migration
- mcp-proxy adoption
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SRCH-05 | Graph-traversal queries (e.g., "what depends on entity X?") via memory-api, no direct Cypher | Neo4j async driver + memory-api proxy endpoint pattern |
| MCP-01 | MCP gateway routes tool calls from any frontend or agent to registered services | FastMCP streamable-http + FastAPI gateway architecture |
| MCP-02 | New tool added by registering MCP server URL — no core changes | DB-backed registry in mcp-gateway |
| MCP-03 | Every tool call includes caller's team_scope and user_id, enforced by gateway | acting_user_sub injection pattern from promotion_manager.py |
| MCP-04 | Tool outputs written to memory-api with full tagging contract | audit-log POST endpoint reuse |
| MCP-05 | Scraper MCP tool works end-to-end from LibreChat/agent | reuses document_loader.load_url() |
| MCP-06 | Calendar MCP tool queryable from chat and agents | google-api-python-client calendar.list |
| MCP-07 | New MCP server registerable without infra restart | DB-backed registry + GET /tools discovery |
| INT-01 | Drive folders synced into team memory with full tagging | google-api-python-client changes.list + ingestion-agent |
| INT-02 | Drive sync is incremental — only changed files re-processed | changes.list newStartPageToken persisted in Postgres |
| INT-03 | Drive folders mapped to specific team/project scopes | Team row drive_folder_id + change_token columns |
| INT-04 | Agent summaries written back to Drive with explicit user opt-in + audit log | drive.file scope + audit-log POST |
</phase_requirements>

---

## Resolved Questions

### Q1 — Drive sync polling library

**Recommendation:** `google-api-python-client==2.195.0` + `google-auth-oauthlib==1.3.1`

**Why:** `google-api-python-client` is the official Google-maintained client that wraps the Drive REST API including `changes.list` and `changes.getStartPageToken`. `google-auth-oauthlib` handles the OAuth 2.0 token refresh cycle. Both are actively maintained (2.195.0 released April 2026). [VERIFIED: PyPI registry]

**Token persistence:** Store the `newStartPageToken` in a Postgres column on the `team_drive_mappings` table (a new table for Phase 3):

```sql
-- migration 0004_neo4j_outbox.py also adds:
CREATE TABLE team_drive_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_scope TEXT NOT NULL,
    folder_id TEXT NOT NULL,
    change_token TEXT,            -- persisted newStartPageToken
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

**Code skeleton:**

```python
from googleapiclient.discovery import build

service = build("drive", "v3", credentials=creds)
# First call: get baseline token
resp = service.changes().getStartPageToken().execute()
token = resp["startPageToken"]
# Subsequent polls (every 5min):
changes = service.changes().list(pageToken=token, includeRemoved=True).execute()
new_token = changes.get("newStartPageToken")  # save this back to DB
for change in changes.get("changes", []):
    ...
```

**Pitfalls:**
- Token dies on restart if stored in-memory only — always persist `newStartPageToken` to Postgres immediately after receiving it, before processing changes. If the service crashes mid-batch, you re-process the same batch on restart (idempotent by design because memory-api upserts on `source: "drive:{file_id}"`).
- `changes.list` with a stale token (>30 days unused) returns 410 Gone — catch `HttpError(410)` and call `getStartPageToken()` to re-baseline, then log "full re-sync triggered".
- `files.export` for Docs/Sheets/Slides returns the full file body (not a diff) — rate limit is 200 quota units per call. At 5min intervals for a team folder, this is well within 325,000 units/min/user. [VERIFIED: Google Drive API limits documentation]

---

### Q2 — MCP SDK for FastAPI

**Recommendation:** `mcp==1.27.0` (official Anthropic SDK), using `FastMCP` with **Streamable HTTP** transport, deployed as a **standalone uvicorn process** (not mounted inside a larger FastAPI app).

**Why:** The official `mcp` package (MIT, maintained by Anthropic) provides `FastMCP` which supports the current `streamable-http` transport (replacement for the deprecated HTTP+SSE transport as of protocol 2025-06-18). [VERIFIED: PyPI registry, modelcontextprotocol.io/docs/concepts/transports]

**Critical caveat — do not use `app.mount()` in a parent FastAPI app.** GitHub issue #1367 (open as of May 2026) documents a `RuntimeError: Task group is not initialized` when mounting FastMCP's streamable-http ASGI app inside a parent FastAPI instance. The workaround is to run each MCP tool service as its own standalone uvicorn process. This aligns with the CONTEXT.md decision to build each tool (scraper, drive-read, calendar) as separate sidecars. [VERIFIED: github.com/modelcontextprotocol/python-sdk/issues/1367]

**mcp-gateway pattern** (the custom FastAPI gateway in CONTEXT.md) does NOT use FastMCP — it is a plain FastAPI service that speaks the MCP HTTP protocol as a *client forwarder*. The gateway itself has no tools; it proxies calls to tool sidecars. The tool sidecars (mcp-scraper, mcp-drive-read, mcp-calendar) each run `FastMCP` standalone.

**Tool sidecar skeleton:**

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("xbrain-scraper")

@mcp.tool()
async def scrape(url: str) -> str:
    """Fetch URL text content (max 50KB)."""
    from app.tools.document_loader import load_url
    return await load_url(url)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")  # binds to :8000/mcp
```

**Streamable HTTP vs STDIO:**
- STDIO: server runs as subprocess. Inappropriate for Docker network services.
- Streamable HTTP: server is an independent process reachable over HTTP. Correct for our Docker-internal topology.
- Each tool sidecar binds on its own port (e.g., 8100/mcp, 8101/mcp, 8102/mcp) on the internal `xbrain_net`.

**Auth:** The `mcp` SDK supports `TokenVerifier` for OAuth 2.1. For our internal Docker network, we inject `X-Team-Scope` / `X-User-Sub` headers from the gateway (service-to-service, bridge JWT) — the tool sidecars trust headers from the gateway. The gateway authenticates the end client (Google OIDC or bridge JWT). [CITED: github.com/modelcontextprotocol/python-sdk README]

**Pitfalls:**
- `Mcp-Session-Id` header must be included on all requests after init — the gateway must track session state per connected client.
- CORS: `expose_headers=["Mcp-Session-Id"]` required if any browser client connects directly.
- Workers: do NOT run tool sidecars with `--workers N` in uvicorn — multi-worker mode causes in-memory session state to be lost across workers (issue #658). Single worker per sidecar is correct given the 50-128MB memory budget.
- Protocol version header: send `MCP-Protocol-Version: 2025-06-18` on all requests to avoid version negotiation failures.

---

### Q3 — Neo4j async driver

**Recommendation:** `neo4j==6.1.0` (current), using `AsyncGraphDatabase`. Async is production-ready since v5.0. [VERIFIED: PyPI registry, neo4j.com/docs/api/python-driver/current/async_api.html]

**FastAPI lifespan integration:**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from neo4j import AsyncGraphDatabase

_driver = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    _driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    yield
    await _driver.close()

app = FastAPI(lifespan=lifespan)

# Usage in route:
async def write_entity(entity: str):
    async with _driver.session(database="neo4j") as session:
        await session.run(
            "MERGE (e:Entity {name: $name})",
            name=entity,
        )
```

**Version gotchas:**
- The `neo4j` package v6.x renamed config parameters (e.g., `max_connection_pool_size` → `max_connection_pool_size` unchanged, but check release notes for any deprecated kwargs). [ASSUMED — migration notes from v5→v6 not exhaustively verified]
- `AsyncSession` must not be shared across concurrent async Tasks — create a new session per request or per outbox batch.
- `execute_query()` (introduced v5.8) is the simplest async API for one-shot queries: `await _driver.execute_query("...", database_="neo4j")`.
- Bolt connection verification: the driver does NOT connect on construction — it connects on first use. Call `await _driver.verify_connectivity()` in the lifespan startup to fail fast.

---

### Q4 — Drive file deletion handling

**Recommendation: soft archive with `validation_status='archived'`** for facts at WORKING and above. Hard delete only for EPHEMERAL facts that have never been promoted.

**Rationale aligned with xbrain truth-level invariants:**

The Phase 2 audit log invariant says the log is append-only and immutable. The truth-level state machine (TRUTH-07) explicitly supports demotion with a recorded reason. Hard-deleting facts would break:
1. The audit trail (a WORKING fact that referenced a Drive doc would vanish with no history)
2. Entity graph lineage (Neo4j `[:DERIVED_FROM]` edges would point to nothing)
3. The `MEM-07` versioning invariant (fact versions must be retained, not dropped)

**Proposed deletion strategy in drive-sync:**

```
When changes.list returns change.removed == True for a file:
  facts = memory-api.query(source="drive:{file_id}")
  for fact in facts:
    if fact.truth_level == "EPHEMERAL":
      memory-api.delete(fact.id)          # safe: never promoted, no audit trail
    else:
      memory-api.patch(fact.id, {
          "validation_status": "archived",
          "metadata.archived_reason": "source_drive_file_deleted",
          "metadata.archived_at": now(),
      })
      # also emit an audit-log entry for the archive event
```

**Rule of thumb:** Facts that have been seen by a human (truth_level >= WORKING) are never silently deleted — they are archived. Archived facts do not appear in default RAG retrieval (filter `validation_status != 'archived'`), but they remain queryable with explicit filter.

---

### Q5 — Outbox worker placement

**Recommendation: in-process background task inside memory-api** (using FastAPI `BackgroundTasks` or a lightweight `asyncio` loop started in lifespan).

**Why not a separate container:**
- RAM budget is constrained. A separate Python container consumes ~80-150MB for the interpreter alone plus the neo4j driver.
- The outbox only needs to drain `neo4j_outbox` rows and call `driver.execute_query()` — this is I/O bound, fits naturally in asyncio, and shares the existing DB connection pool.
- Phase 3 headroom is ~2.9 GB but we already budget 1024m for Neo4j and ~600m for 4 MCP sidecars. An outbox container would push closer to the budget ceiling.

**Recommended pattern:** A background `asyncio.Task` launched in `memory-api`'s lifespan context, polling `neo4j_outbox` every 2 seconds, draining up to 50 rows per tick:

```python
# In memory-api lifespan:
async def drain_outbox():
    while True:
        rows = await db.fetch("SELECT * FROM neo4j_outbox WHERE processed = false LIMIT 50 FOR UPDATE SKIP LOCKED")
        for row in rows:
            await neo4j_driver.execute_query(row["cypher"], **row["params"])
            await db.execute("UPDATE neo4j_outbox SET processed = true WHERE id = $1", row["id"])
        await asyncio.sleep(2)

task = asyncio.create_task(drain_outbox())
```

`SKIP LOCKED` is critical — if memory-api ever runs with `--workers 2`, both workers would race on the same outbox rows without it. Currently `--workers 2` is set in docker-compose.yml, so `SKIP LOCKED` is not optional.

**If this becomes a maintenance problem** (e.g., slow Cypher writes block uvicorn workers), extract to a separate container in Phase 4. The outbox table schema stays the same, the move is a refactor only.

---

## Additional Findings

### Google OAuth re-consent for new Drive/Calendar scopes

The existing Google OAuth client has `email profile openid`. Adding `drive.readonly`, `drive.file`, `calendar.readonly` requires re-consent because these are **sensitive scopes** (Google's classification). [CITED: developers.google.com/identity/protocols/oauth2/web-server#incrementalAuth]

**Cleanest UX pattern — incremental authorization:**

1. When a user first accesses a Drive-dependent feature (e.g., maps a Drive folder), redirect to Google OAuth with:
   - `scope`: ALL desired scopes (old + new)
   - `include_granted_scopes=true`
   - `access_type=offline` (to get a refresh token)
2. Google shows consent screen for **only the new scopes** — users who already granted email/profile don't re-consent for those.
3. On callback, persist the new access_token + refresh_token in the `team_drive_mappings` row (encrypted, not in Postgres plaintext — use Fernet or store in a secrets env var pattern).

**Important:** `drive.file` (write-back) scope should be requested separately from `drive.readonly`, triggered only by the explicit INT-03 opt-in flow ("allow agent to write back to Drive"), not bundled with the initial sync setup. This avoids consent fatigue.

**No impact on existing users who don't use Drive sync** — incremental auth means they never see the new consent screen until they trigger the Drive feature.

---

### Neo4j memory tuning for Phase 3

The CONTEXT.md budgets `Neo4j Community: heap 512m + page cache 256m`. For ~10k nodes / ~50k relationships, this is workable in Phase 3 but tight. [CITED: neo4j.com/docs/operations-manual/current/performance/memory-configuration/]

**Recommended docker-compose config:**

```yaml
neo4j:
  image: neo4j:2026.04.0-community
  environment:
    NEO4J_server_memory_heap_initial__size: 512m
    NEO4J_server_memory_heap_max__size: 512m     # set equal to initial to avoid GC pauses
    NEO4J_server_memory_pagecache_size: 256m
    NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    NEO4J_PLUGINS: "[]"                          # no plugins for Community baseline
  mem_limit: 1024m
```

**Why equal initial/max heap:** Neo4j docs recommend setting both to the same value to prevent GC stop-the-world pauses from heap expansion. With 512/512 + 256 page cache + ~200m JVM overhead (thread stacks, metaspace), total resident ≈ 960m — fits within 1024m mem_limit with ~64m margin.

**Page cache sizing for our scale:** At 10k nodes + 50k relationships, the on-disk store will be <50MB. 256m page cache will cache the entire graph in RAM — ideal for traversal performance. As the graph grows into Phase 4 range (~100k+ nodes), revisit with `neo4j-admin server memory-recommendation`.

**JVM overhead:** The ~200m is ASSUMED based on community knowledge of Neo4j's JVM baseline; the actual value should be verified with `docker stats` after Neo4j starts in Phase 3 wave 1.

---

### FastAPI MCP pitfalls (v1 implementations)

These are confirmed from real-world GitHub issues as of May 2026:

1. **`RuntimeError: Task group is not initialized`** when mounting FastMCP's `streamable_http_app()` as an ASGI sub-app inside a parent FastAPI. Root cause: the lifespan of the mounted app is not invoked by FastAPI's lifespan machinery. **Avoid**: run each tool sidecar as a standalone uvicorn process, not mounted. [VERIFIED: github.com/modelcontextprotocol/python-sdk/issues/1367]

2. **404 with multiple uvicorn workers** (`--workers N`). FastMCP's in-memory session registry is per-process; a request that hits worker A after init on worker B gets 404. **Avoid**: run each MCP sidecar with a single uvicorn worker. This is consistent with the ~50-128m mem_limit budgeted. [VERIFIED: github.com/jlowin/fastmcp/issues/658]

3. **GET /mcp hangs with streamable HTTP** causing timeouts. When no client is connected to the SSE stream, a GET to the MCP endpoint can block. **Avoid**: set a short `timeout` on the gateway's httpx client when forwarding GET /mcp probes. Only forward POST tool calls from the gateway; don't relay open-ended SSE streams. [VERIFIED: github.com/jlowin/fastmcp/issues/532]

4. **Missing `MCP-Protocol-Version` header causes version mismatch.** Servers receiving requests without this header fall back to protocol `2025-03-26` (older). Include `MCP-Protocol-Version: 2025-06-18` on all gateway-to-tool-sidecar requests. [CITED: modelcontextprotocol.io/docs/concepts/transports]

5. **Schema validation on tool input is strict.** FastMCP generates JSON Schema from Python type hints. If a tool function uses `Any` or missing type annotations, schema generation fails at startup. Always fully annotate tool function signatures.

---

### Drive rate limits

[VERIFIED: developers.google.com/drive/api/guides/limits]

| Operation | Quota units | Notes |
|-----------|-------------|-------|
| `changes.list` | 100 | Per poll tick |
| `files.export` | 200 | Per file fetched |
| `files.get` (metadata) | 10 | Cheap |

**Per-user budget:** 325,000 units/min per user per project.

**Phase 3 realistic load:** With a 5-min cron and a team folder of 100 docs, a sync tick might trigger `changes.list` (100 units) + up to 10 changed files × `files.export` (2,000 units) = ~2,100 units per 5 minutes = ~420 units/min. Well within the limit.

**Handling 429s:** Implement truncated exponential backoff: `wait = min(2^n + random_ms, 64s)`. Use `googleapiclient.errors.HttpError` and check `e.resp.status == 429`. A simple decorator:

```python
import time, random
from googleapiclient.errors import HttpError

def with_backoff(fn, max_retries=5):
    for n in range(max_retries):
        try:
            return fn()
        except HttpError as e:
            if e.resp.status not in (429, 500, 503):
                raise
            wait = min(2 ** n + random.random(), 64)
            time.sleep(wait)
    raise RuntimeError("Drive API: max retries exceeded")
```

**Token expiry (410 Gone):** When `changes.list` returns 410, the page token is expired (unused >30 days or token invalidated). Catch it, call `getStartPageToken()` to re-baseline, update DB, and log "full re-sync triggered for team={team_scope}".

---

### Phase 2 pitfalls applied to Phase 3

- **Build context for shared packages.** In docker-compose, `memory-api` uses `context: ..` (repo root) because `packages/memory-models` is a sibling of `apps/`. Any Phase 3 service that needs `packages/` must also set `context: ..` and a relative `dockerfile: apps/<service>/Dockerfile`. Concretely: `drive-sync` will import `packages/memory-models` models, so it needs repo-root context. `mcp-scraper` / `mcp-drive-read` / `mcp-calendar` do NOT need shared packages — their context can be `apps/<service>` directly.

- **DNS routing — internal calls bypass Cloudflare.** All service-to-service calls must use Docker internal hostnames (e.g., `http://memory-api:8000`, `http://neo4j:7474`), never the public subdomain. Using the public subdomain routes through Cloudflare's proxy, which caused 502 errors on POST in Phase 2. Explicitly document `MEMORY_API_URL=http://memory-api:8000` (not `https://x.dejavu.cat`) in every new service's env.

- **Healthcheck IPv4 binding.** Use `http://127.0.0.1:<port>/healthz` in healthcheck `test`, not `http://localhost:...`. On some container images `localhost` resolves to `::1` (IPv6) which fails if the service binds to `0.0.0.0` (IPv4 only). The existing services in docker-compose.yml already use `127.0.0.1` — maintain this pattern for all 5 new services.

- **`start_period` on slow-start services.** Neo4j Community typically takes 20-40s to boot (JVM + graph store open). Set `start_period: 60s` and `retries: 10` on its healthcheck to avoid premature `unhealthy` state blocking dependent services.

---

## Dependencies to Add (per service)

### apps/drive-sync/pyproject.toml
```
google-api-python-client>=2.195.0
google-auth-oauthlib>=1.3.1
google-auth-httplib2>=0.2.0    # required transport adapter for google-api-python-client
httpx>=0.28.0                   # shared with agent-runtime
asyncpg>=0.30.0                 # async Postgres for token persistence
structlog>=25.0.0               # logging pattern from other services
pypdf>=5.0.0                    # PDF extraction (already in agent-runtime)
```

### apps/mcp-gateway/pyproject.toml
```
fastapi>=0.115.0
uvicorn[standard]>=0.34.0
httpx>=0.28.0          # forward calls to tool sidecars
asyncpg>=0.30.0        # tool registry DB queries
python-jose[cryptography]>=3.4.0  # bridge JWT verify (reuse agent-runtime pattern)
structlog>=25.0.0
```
Note: mcp-gateway does NOT import the `mcp` package — it is a plain HTTP proxy that speaks the MCP wire protocol itself (JSON-RPC over HTTP POST). It does not need the SDK.

### apps/mcp-scraper/pyproject.toml
```
mcp>=1.27.0
httpx>=0.28.0           # load_url() reuse from document_loader.py
structlog>=25.0.0
```

### apps/mcp-drive-read/pyproject.toml
```
mcp>=1.27.0
google-api-python-client>=2.195.0
google-auth-oauthlib>=1.3.1
google-auth-httplib2>=0.2.0
structlog>=25.0.0
```

### apps/mcp-calendar/pyproject.toml
```
mcp>=1.27.0
google-api-python-client>=2.195.0
google-auth-oauthlib>=1.3.1
google-auth-httplib2>=0.2.0
structlog>=25.0.0
```

### apps/memory-api/pyproject.toml (additions only)
```
neo4j>=6.1.0          # AsyncGraphDatabase — replaces the stub neo4j dep if already present
```

---

## Service Architecture Diagrams

### Flow 1: Drive Sync (INT-01, INT-02)

```
[Google Drive API]
     |  changes.list(token)  (every 5min)
     v
[drive-sync sidecar]
  - fetch changed file IDs
  - files.export → text (Docs/Sheets/Slides)
  - load_pdf_bytes() → text (PDFs, via document_loader)
  - persist newStartPageToken → Postgres team_drive_mappings.change_token
     |  POST /v1/agents/ingest  (bridge JWT, team_scope from mapping)
     v
[agent-runtime:9100]
  - ingestion LangGraph graph (existing Phase 2)
  - extract_facts() + entity extraction (new: emit entities for Neo4j)
  - HITL gate if needed
     |  POST /v1/memory/upsert  (facts: source="drive:{file_id}", truth_level=WORKING)
     v
[memory-api:8000]
  - enforce tagging contract
  - write to Postgres facts table
  - write to Qdrant (vector index)
  - INSERT INTO neo4j_outbox (cypher, params)  ← new Phase 3
     |  (async background drain every 2s)
     v
[Neo4j:7474]  ← new Phase 3
  - MERGE Entity nodes
  - CREATE Fact nodes
  - CREATE [:MENTIONS] and [:DERIVED_FROM] edges
```

### Flow 2: MCP Tool Call (MCP-01..07)

```
[LibreChat or agent-runtime]
     |  POST /tools/{tool_name}/call
     |  Headers: Authorization: Bearer {user_jwt or bridge_jwt}
     v
[mcp-gateway:8080]
  - verify JWT (Google OIDC or bridge)
  - extract acting_user_sub + team_scope
  - lookup tool sidecar URL from DB registry
  - inject X-Team-Scope + X-User-Sub headers
  - POST to tool sidecar (streamable-http MCP protocol)
     |  async, forward response
     v
[mcp-scraper:8100 | mcp-drive-read:8101 | mcp-calendar:8102]
  (each: FastMCP standalone, transport="streamable-http")
     |  tool result
     v
[mcp-gateway]
  - POST /v1/audit-log  (tool_name, user_sub, team_scope, result_summary)
  - return result to caller
```

### Flow 3: Graph Query (SRCH-05)

```
[Frontend or agent]
     |  GET /v1/graph/traverse?entity=X&depth=2&team_scope=T
     v
[memory-api:8000]
  - authorize: extract team_scope from JWT
  - translate to Cypher: MATCH (e:Entity {name:$name})-[:DEPENDS_ON*1..2]->(dep)
                          WHERE e.team_scope = $team_scope RETURN dep
     |  await _neo4j_driver.execute_query(cypher, ...)
     v
[Neo4j:7474]
  - execute graph traversal
     |  results
     v
[memory-api]
  - serialize as JSON, return to caller
  (no direct Cypher exposed — memory-api is the only Neo4j client)
```

### Flow 4: Drive Write-Back (INT-03, INT-04)

```
[User in LibreChat: "Write this summary to Drive doc {id}"]
     |  POST /tools/drive-write/call  (mcp-gateway)
     |  user JWT + explicit consent flag in payload
     v
[mcp-gateway]
  - verify JWT
  - check consent: payload.user_consent == true (required)
     |
     v
[mcp-drive-read (extended for write)] or dedicated mcp-drive-write
  - files.update() with drive.file scope
     |  result
     v
[mcp-gateway]
  - POST /v1/audit-log (action="drive_write", file_id, user_sub, team_scope)
  - return confirmation to LibreChat
```

---

## Risk Log

### RISK-01: FastMCP mount bug makes mcp-gateway design harder
**Severity:** MEDIUM. **Status:** Active open issue (#1367).
**Impact:** Cannot embed FastMCP inside mcp-gateway's FastAPI app. Requires separate uvicorn processes for each tool sidecar.
**Mitigation:** Already aligned with CONTEXT.md decision (separate sidecars per tool). mcp-gateway is a plain FastAPI proxy, NOT a FastMCP host. Risk is contained.

### RISK-02: Google OAuth re-consent breaks existing users' sessions
**Severity:** MEDIUM.
**Impact:** When Drive scopes are added, any user who tries to use Drive features will be redirected for consent. If the redirect flow is not smooth, users may be confused or locked out temporarily.
**Mitigation:** Use incremental auth (`include_granted_scopes=true`). Only trigger consent flow when user initiates Drive mapping, not on login. Display a clear in-UI prompt: "To sync your Drive folder, Google needs additional permissions."

### RISK-03: Neo4j 1024m mem_limit too tight if CONTEXT.md estimate is optimistic
**Severity:** LOW-MEDIUM.
**Impact:** OOM kill of Neo4j container corrupts the graph store.
**Mitigation:** Start with `mem_limit: 1024m`. Monitor with `docker stats` in Wave 1. If neo4j resident > 900m, raise to 1280m (still within 2.9 GB headroom). Neo4j is a read-replica — if it dies, drain the outbox replay to rebuild from Postgres.

### RISK-04: drive-sync token rotation — 410 Gone on cold restart
**Severity:** LOW.
**Impact:** If `team_drive_mappings.change_token` is NULL (first run or token expired), `changes.list` returns 410. Full re-sync runs — all team documents ingested as new. This may create duplicate facts if upsert logic in memory-api is not idempotent on `source: "drive:{file_id}"`.
**Mitigation:** Verify that memory-api `POST /v1/memory/upsert` performs an upsert (not insert) keyed on `source` + `team_scope`. Add a UNIQUE constraint on `(source, team_scope)` in migration 0004 if not already present.

### RISK-05: MCP tool sidecars single-worker + session state
**Severity:** LOW.
**Impact:** FastMCP with multiple uvicorn workers causes 404 for session continuity. Single-worker constraint limits throughput.
**Mitigation:** Single worker is correct for Phase 3 load (internal team, not public SaaS). Each tool call is stateless in our design (no long-running MCP sessions — each tool call is a single request/response). Session management overhead is minimal.

---

## Sources

### Primary (HIGH confidence)
- [PyPI: google-api-python-client 2.195.0](https://pypi.org/project/google-api-python-client/) — version verified
- [PyPI: google-auth-oauthlib 1.3.1](https://pypi.org/project/google-auth-oauthlib/) — version verified
- [PyPI: mcp 1.27.0](https://pypi.org/project/mcp/) — version + transport description verified
- [PyPI: neo4j 6.1.0](https://pypi.org/project/neo4j/) — version verified
- [Neo4j Python Driver 6.1 Async API](https://neo4j.com/docs/api/python-driver/current/async_api.html) — AsyncGraphDatabase, session, lifespan pattern
- [MCP Transports specification](https://modelcontextprotocol.io/docs/concepts/transports) — Streamable HTTP vs STDIO, security warnings
- [Google Drive changes.list](https://developers.google.com/drive/api/guides/manage-changes) — token flow, incremental sync
- [Google Drive API limits](https://developers.google.com/drive/api/guides/limits) — quota units per operation, 429 backoff
- [Google OAuth incremental authorization](https://developers.google.com/identity/protocols/oauth2/web-server#incrementalAuth) — scope upgrade re-consent UX

### Secondary (MEDIUM confidence)
- [MCP Python SDK GitHub README](https://github.com/modelcontextprotocol/python-sdk) — FastMCP FastAPI integration patterns
- [Neo4j memory configuration](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/) — heap/page cache guidance
- [FastMCP mounting issue #1367](https://github.com/modelcontextprotocol/python-sdk/issues/1367) — confirmed open bug, unresolved
- [FastMCP multi-worker 404 issue #658](https://github.com/jlowin/fastmcp/issues/658) — confirmed bug, single-worker workaround
- [FastMCP GET /mcp hang #532](https://github.com/jlowin/fastmcp/issues/532) — confirmed bug, avoid relay of open SSE GET

### Tertiary (LOW confidence / ASSUMED)
- Neo4j v5→v6 driver API changes: some kwargs may have changed — verify against v6.1 changelog before migration 0004 [ASSUMED]
- Neo4j JVM overhead estimate ~200m — ASSUMED based on community knowledge; verify with `docker stats` in Wave 1
- `memory-api` upsert idempotency on `source` + `team_scope` — ASSUMED from Phase 2 design intent; verify the actual Postgres schema before writing migration 0004

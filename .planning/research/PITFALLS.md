# Pitfalls Research

**Domain:** AI memory platform — collective persistent-memory for humans + agents, multi-team, multi-frontend, self-hosted
**Researched:** 2026-05-02
**Confidence:** HIGH (architectural pitfalls), MEDIUM (framework maturity), HIGH (operational sizing)

---

## Critical Pitfalls

### Pitfall 1: Memory-API Degrades Into a Thin CRUD Proxy

**What goes wrong:**
`memory-api` is built as a thin pass-through layer — it validates schema on input, writes to Postgres/Qdrant/Neo4j, and exposes GET/POST endpoints. No enforcement of the tagging contract beyond "field exists". No invariant enforcement. Frontends and agents write data with `truth_level: "EPHEMERAL"` directly into canonical slots because nothing prevents it. The promotion workflow becomes UI-only, not enforced at the persistence layer.

**Why it happens:**
The fastest way to wire up a backend is to generate CRUD routes from a schema. Teams reach for frameworks that auto-generate REST from database models. The enforcement logic feels like "business logic that can come later." It never does — once consumers are writing unvalidated data, retrofitting invariant enforcement breaks all existing callers.

**How to avoid:**
Design `memory-api` as a domain service from day one, not a CRUD layer. Enforce the tagging contract as a middleware chain that rejects any write missing the 7 required fields. Truth-level promotion must be a dedicated, explicit endpoint (`POST /memories/{id}/promote`) with role checks and audit logging — never a free-form field update via `PATCH /memories/{id}`. Add a unit test for every invariant on the first day of Phase 1 scaffolding. Document the internal API as a contract, not a convenience layer.

**Warning signs:**
- Any service does a `PATCH /memories/{id} { truth_level: "CANONICAL" }` directly without going through the promotion endpoint
- Integration tests mock memory-api instead of calling it
- "We'll add validation later" appears in PR comments on memory-api
- Schema validation is a TODO in the middleware

**Phase to address:** Phase 1 — design the enforcement layer before any frontend connects. The invariants must be in place before the first real data write.

---

### Pitfall 2: Frontend-Coupled State — Logic or Data Locked in LibreChat or Open WebUI

**What goes wrong:**
A feature gets built as a LibreChat plugin that stores conversation metadata in LibreChat's own MongoDB. Another feature stores agent outputs in Open WebUI's local SQLite. The "memory" diverges into three places: LibreChat's DB, Open WebUI's DB, and memory-api. A user accessing from ChatGPT API sees none of the history. The multi-frontend invariant is silently broken.

**Why it happens:**
LibreChat and Open WebUI both have plugin/extension mechanisms that are easier to use than building a proper memory-api integration. The path of least resistance is to use what the frontend already provides. This is the #1 failure mode the project owner explicitly identified as the chatbot-workspace trap.

**How to avoid:**
Enforce a hard rule at the architecture level: frontends are read/write clients of memory-api, not data stores. Any conversation artifact that should persist (message, extracted fact, agent output) must be written to memory-api before it is considered persisted. LibreChat and Open WebUI local databases are treated as ephemeral caches only — they hold session state for UX purposes, nothing more. Include this rule in the CLAUDE.md and in the Phase 1 acceptance criteria. Review every PR that touches LibreChat or Open WebUI config to confirm no data persists locally.

**Warning signs:**
- LibreChat plugin code imports from `mongoose` or touches its local MongoDB schema
- Open WebUI `functions/` directory grows with data-storing code
- "Show me history from LibreChat" and "Show me history from memory-api" return different results
- Any frontend has a database migration that isn't in the shared `/packages/schemas` directory

**Phase to address:** Phase 1 — establish the pattern with the first LibreChat integration. Getting this wrong in Phase 1 means migrating data out of two frontend databases in Phase 2.

---

### Pitfall 3: Team Isolation Enforced Only at the Application Layer

**What goes wrong:**
`team_scope` is a field on every memory record. The application code always adds `WHERE team_scope = ?` to queries. But nothing at the database or vector-store level enforces this. A misconfigured query, a missing filter on a new retrieval endpoint, or a raw DB connection used for debugging returns cross-team data. In Qdrant, a semantic search without the `team_scope` payload filter silently returns vectors from all teams.

**Why it happens:**
Application-layer filtering is the natural first implementation. DB-level row security and Qdrant namespace isolation require extra setup and feel like premature optimization when the first team is alone on the system. The problem only manifests when a second team is onboarded.

**How to avoid:**
For PostgreSQL: use Row-Level Security (RLS) policies on every table that holds team-scoped data. The policy enforces `team_scope = current_setting('app.current_team')` at the DB engine level — a query without the context variable returns nothing, not everything. For Qdrant: stamp every vector with a `team_scope` payload field and enforce it as a mandatory filter in every retrieval path by wrapping the Qdrant client in a `TeamScopedQdrantClient` that injects the filter before every search call. Never expose the raw Qdrant client to application code. Write an integration test that asserts Team A cannot see Team B's data even with a direct Qdrant query.

**Warning signs:**
- Qdrant search calls use `query_filter=None` anywhere in the codebase
- PostgreSQL tables have `team_scope` column but no RLS policy
- A new retrieval endpoint is added without a corresponding isolation test
- Database connection used directly in debug scripts, not through the scoped client

**Phase to address:** Phase 1 for PostgreSQL RLS design, Phase 2 when Qdrant becomes active for semantic retrieval.

---

### Pitfall 4: Cross-Team Bleed via Shared Embedding Index Without Scope Enforcement

**What goes wrong:**
Qdrant is configured with a single collection for all teams. Embeddings from Team A's confidential market research and Team B's public product specs share the same HNSW graph. A semantic search for "Q3 revenue projections" from Team B accidentally retrieves Team A's confidential records because the `team_scope` filter was missing on one endpoint, or because HNSW graph traversal occasionally returns neighbors across filter boundaries when the index becomes disconnected.

**Why it happens:**
Single-collection deployment is simpler, cheaper (fewer Qdrant resources), and documented as the recommended approach for small-scale multi-tenancy. The HNSW disconnection problem under high-cardinality filters is a documented Qdrant behavior that is not obvious from the documentation overview.

**How to avoid:**
Use Qdrant's payload-indexed filtering with `team_scope` as a mandatory filter on every search. Create a payload index on `team_scope` explicitly — without this, filters are full scans. For teams with high data volume or strict isolation requirements, use Qdrant's shard-based multitenancy (dedicated shards per team). In all cases, wrap the Qdrant client to make the `team_scope` filter non-optional (see Pitfall 3). Run a dedicated cross-team bleed integration test after every schema change that touches retrieval.

**Warning signs:**
- Qdrant payload index on `team_scope` is not explicitly created in the migration scripts
- Retrieval latency is high even for small result sets (symptom of missing payload index causing full scans)
- Any search result set contains records with a `team_scope` that doesn't match the requesting team

**Phase to address:** Phase 2 — when Qdrant becomes the active semantic retrieval layer.

---

### Pitfall 5: Identity Fragmentation — Same User Appears as Different Identities Across Frontends

**What goes wrong:**
LibreChat uses its own user database with email-based identity. Open WebUI uses a different auth system. A user logged into LibreChat as `alice@team.io` creates memories tagged with `source: librechat:alice`. The same user in Open WebUI creates memories tagged with `source: openwebui:user-42`. Memory-api sees two distinct authors. Agents retrieving "Alice's context" get a fragmented, incomplete view. Truth-level promotions made from one frontend are invisible to the other frontend's history.

**Why it happens:**
LibreChat and Open WebUI both support SSO via OIDC, but their default configurations use local user databases. The SSO integration is documented as optional. When developers wire up each frontend independently, they use the path of least resistance — local auth — and defer SSO "until we need it." The identity fragmentation is invisible until a second frontend is active.

**How to avoid:**
Deploy a central identity provider (Google SSO via OIDC is the natural choice given Google Drive integration) before connecting the second frontend. Both LibreChat and Open WebUI support OIDC natively. Configure both frontends in Phase 1 to authenticate through the same OIDC provider. The canonical user identity must be the OIDC `sub` claim (a stable, provider-issued identifier), not email or display name. Memory-api must record `source_user_id` as the OIDC sub, not a frontend-local user ID.

**Warning signs:**
- LibreChat and Open WebUI have separate user tables with no shared identifier
- Memory records from LibreChat have `source_user_id` in format `lc-{mongo-id}` and Open WebUI records have `owui-{local-id}`
- The same person appears twice in the memory-api author index
- Promotion history on a memory record shows different user names from different frontends

**Phase to address:** Phase 1 — this must be designed before the second frontend is connected. Retrofitting shared identity after data exists in two systems requires a migration.

---

### Pitfall 6: VM Memory Saturation When Phase 2 and Phase 3 Services Come Online

**What goes wrong:**
Phase 1 runs comfortably on an e2-medium (4 GB RAM) with LibreChat + Open WebUI + PostgreSQL + Qdrant. Phase 2 adds LangGraph workers + Remembra + Memstate. Phase 3 adds Neo4j. Neo4j alone needs ~1.5–2 GB heap + pagecache for useful operation. With all services running, total resident memory exceeds 4 GB. The Linux OOM killer starts terminating processes — often the database, causing data corruption. The system becomes unstable in ways that are hard to diagnose.

**Why it happens:**
Phase 1 fits comfortably, so no one adjusts the VM. Adding services one at a time, each "small", doesn't trigger the obvious alarm. Memory is monitored per-container, not as total host utilization. The VM limit is only discovered when the OOM killer fires in production.

**How to avoid:**
Do a memory budget calculation before Phase 2 starts, not after. Realistic minimums for a fully operational stack: PostgreSQL (512 MB), Qdrant (512 MB for Phase 2 load), LibreChat (512 MB), Open WebUI (512 MB), Neo4j (1.5 GB heap + 512 MB pagecache = 2 GB minimum), LangGraph workers (512 MB), Langfuse (512 MB), MinIO (256 MB), OS + Docker overhead (512 MB). Total: ~5.9 GB. An e2-medium cannot fit this. Plan the VM upgrade to e2-standard-2 (8 GB, ~50 €/month) before Phase 2, or start on Railway where you can scale without migration. Set explicit `mem_limit` in Docker Compose for every service and monitor total host utilization with `docker stats`.

**Warning signs:**
- `docker stats` shows total memory usage above 70% of host RAM during normal operation
- Any service has no `mem_limit` set in `docker-compose.yml`
- Neo4j's heap is set to its Docker default (512 MB) — it will thrash with real data
- System logs show OOM kills (`dmesg | grep -i kill`)

**Phase to address:** Phase 1 (planning, budget the upgrade), Phase 2 (execute the VM resize before adding services).

---

### Pitfall 7: Truth-Level Promotion as Fire-and-Forget Instead of Auditable Workflow

**What goes wrong:**
The promotion from `EPHEMERAL` to `CANONICAL` is implemented as a single API call that updates a field. No approval gate. No human review step for the `VALIDATED` → `CANONICAL` transition. No audit log of who promoted what and why. Agents can programmatically promote their own outputs to `CANONICAL`. Six months later, the team cannot distinguish machine-promoted canonical facts from human-validated ones. The truth-level system loses its value — everything is either `EPHEMERAL` (untouched) or `CANONICAL` (auto-promoted garbage).

**Why it happens:**
The promotion workflow feels like a workflow engine problem, not a data model problem. Teams implement the simple case (field update) to get the feature working and mark the workflow as a future enhancement. The audit trail is always "future work."

**How to avoid:**
Design the promotion model as an event-sourced workflow from day one. Each promotion is an immutable event: `{ memory_id, from_level, to_level, promoted_by, promoted_at, justification, promotion_type: "human"|"agent"|"automated" }`. The current truth_level is derived from the event log, not stored as a mutable field. The `VALIDATED` → `CANONICAL` and `CANONICAL` → `PUBLIC` transitions require explicit human authorization (role: `validator` or `admin`). Agents may auto-promote from `EPHEMERAL` to `WORKING` only. Implement this event model in Phase 2 when the promotion workflow is built — not as a PATCH to an existing field.

**Warning signs:**
- `truth_level` is a mutable column updated with a direct `UPDATE` statement
- No `promotion_events` table exists in the schema
- Agents have the same promotion permissions as human validators
- No way to query "show all CANONICAL facts and their promotion history"

**Phase to address:** Phase 2 — design before the first agent writes to memory-api.

---

### Pitfall 8: Premature Graph (Neo4j) Before Vector and Relational Layers Are Stable

**What goes wrong:**
Neo4j is added in Phase 1 or early Phase 2 because the architecture diagram includes it. The team spends engineering time on Cypher queries, graph schema design, and Neo4j integration. But the underlying facts being graphed are still unstable (no tagging contract enforcement, no truth-level workflow). The graph captures wrong or unvalidated data. When the memory layer is refactored in Phase 2, the graph must be rebuilt. Neo4j on a 4 GB VM competes for memory with every other service.

**Why it happens:**
Graph databases are architecturally exciting. Neo4j is in the stack from day one of the spec. Developers want to build toward the final architecture rather than starting with something simpler. The cost of running Neo4j alongside 6 other services on a small VM is underestimated.

**How to avoid:**
Treat Neo4j as a Phase 3 component, exactly as the phasing plan specifies. Do not introduce it earlier for "quick wins" or to test graph queries. The lineage and relationship graph is only meaningful once the facts being graphed are stable (truth-level enforced, tagging contract live, Qdrant retrieval working). Use PostgreSQL's adjacency list or JSONB to prototype any graph-like relationships needed in Phase 2, then migrate to Neo4j in Phase 3 when the data model is frozen.

**Warning signs:**
- Neo4j appears in the Phase 1 or Phase 2 `docker-compose.yml`
- A developer says "I just want to try the graph schema" and starts writing Cypher
- Neo4j container is running but no Phase 3 feature actually requires it yet

**Phase to address:** Phase 3 — hold the line on phasing. Do not add Neo4j until Phase 3 explicitly requires it.

---

### Pitfall 9: niche Memory Framework Abandonment — Remembra or Memstate Become Dead Projects

**What goes wrong:**
Remembra and Memstate are pre-1.0, low-star projects (13 and unknown stars respectively as of May 2026). The team builds Phase 2 around one of them, integrating deeply into their abstractions, storage models, and APIs. Six months later, the project is abandoned or pivots. Migrating away requires rewriting the entire memory layer.

**Why it happens:**
The frameworks look promising, are well-documented, and match the desired abstractions exactly. The low star count feels acceptable because they are "early stage." Deep integration happens naturally as features are built on top of the framework's primitives.

**How to avoid:**
Treat Remembra, Memstate, and Memori as adapters behind an abstraction layer, not as the foundation. Define a `MemoryProvider` interface in `/packages/memory-models` that specifies exactly what operations xbrain needs: `store`, `retrieve`, `promote`, `search`, `version`. Each framework is an implementation of this interface. If a framework is abandoned, swap the implementation without touching the rest of the codebase. Before committing to any framework in Phase 2, run a 1-day POC (as noted in the idea.md) that tests the specific operations xbrain requires. Memori (14k stars, v3.3.2, 34 releases) is substantially more mature than Remembra (13 stars, v0.13.2) — weight the maturity difference when making integration depth decisions.

**Warning signs:**
- Application code imports directly from `remembra` or `memstate` packages instead of through an internal adapter
- The `MemoryProvider` interface does not exist in `/packages/memory-models`
- A framework's internal data models leak into the xbrain API response shapes
- Commits reference framework-specific configuration that has no abstraction layer

**Phase to address:** Phase 2 — define the abstraction layer before integrating any framework. Run the POC before Phase 2 planning finalizes the framework choice.

---

### Pitfall 10: Secrets (API Keys) Leaked Into Git via Docker Compose or .env Files

**What goes wrong:**
The `docker-compose.yml` contains `ANTHROPIC_API_KEY=sk-ant-...` directly, or a `.env` file with all LLM provider keys is committed because `.gitignore` was misconfigured. The repo is on GitHub at `mrboups/xbrain`. Keys are exposed publicly. Anthropic, OpenAI, and Google revoke the keys. The team scrambles to rotate credentials across all services while the platform is down.

**Why it happens:**
Local development with hardcoded credentials in `.env` is fast. `.gitignore` is not configured at project start. One commit with `git add .` includes the `.env` file. The damage is done even if the commit is later reverted — git history retains it.

**How to avoid:**
Set up `.gitignore` before the first commit. The file must explicitly list `.env`, `.env.*`, `*.secret`, `docker-compose.override.yml`, and any file pattern that could contain secrets. Use `.env.example` with placeholder values for documentation. For production, use file-based Docker secrets (mounted at `/run/secrets/`) or a tool like Mozilla SOPS to encrypt secrets in the repo. Implement `detect-secrets` or `git-secrets` as a pre-commit hook that blocks any commit containing high-entropy strings or known secret patterns. Rotate all keys immediately if any leak is suspected — never assume a leaked key was not harvested.

**Warning signs:**
- `.env` file exists in the repo root and is not in `.gitignore`
- `docker-compose.yml` contains literal API key values (not `${VARIABLE}` references)
- `git log --all --oneline -- .env` shows any historical commits
- No pre-commit hook scanning for secrets exists

**Phase to address:** Phase 1 — before the first commit. This is a day-zero configuration, not a feature.

---

### Pitfall 11: Agents Hammering Memory-API — Hot Loops and DB Saturation

**What goes wrong:**
A LangGraph ingestion agent processes a batch of documents and writes an extracted fact to memory-api for every sentence. On a 50-page document, this generates 2,000+ API calls in a tight loop. Memory-api's database connections saturate. PostgreSQL's connection pool exhausts. Other services (LibreChat user sessions) get connection refused errors. The system is effectively DoS'd by its own agent.

**Why it happens:**
LangGraph agents are easy to write as "process item, write result, repeat" loops. The rate-limiting and batching concerns feel like optimization, not correctness. In development with small test documents, the loop works fine. In production with real workloads, it saturates the stack.

**How to avoid:**
Design memory-api with explicit rate-limiting middleware (token bucket per `agent_id`) from Phase 2. Implement a bulk-write endpoint (`POST /memories/batch`) that accepts arrays of memory records — agents must use it for batch operations. Implement connection pooling at memory-api (PgBouncer in front of PostgreSQL) before agents go live. Set LangGraph agent memory write operations to be async and non-blocking — use an internal queue (Postgres LISTEN/NOTIFY or Redis) rather than synchronous per-item writes. Add integration tests that simulate a 1,000-item batch and assert memory-api remains responsive to other callers.

**Warning signs:**
- LangGraph agent code has a `for item in items: memory_api.write(item)` pattern (synchronous per-item loop)
- Memory-api has no rate-limiting middleware
- No `POST /memories/batch` endpoint exists
- PostgreSQL connection count hits max during agent runs (visible in `pg_stat_activity`)

**Phase to address:** Phase 2 — design the rate-limiting and batch API before the first agent is deployed.

---

### Pitfall 12: Multi-Agent Deadlock on Competing Fact Promotions

**What goes wrong:**
An ingestion agent and a validation agent both have a reference to the same memory record. The ingestion agent promotes it from `WORKING` to `VALIDATED`. Simultaneously, the validation agent reads `WORKING` state, performs validation logic, and attempts to promote to `VALIDATED`. One of the writes succeeds, the other fails with a state conflict. The validation agent retries indefinitely without backoff. Both agents hold soft locks on related records. The system enters a livelock state where neither agent makes progress.

**Why it happens:**
Optimistic concurrency is often not implemented in the first version of the promotion workflow. Agents are written to retry on failure without jitter or backoff. State conflicts in a multi-agent system are difficult to trigger in single-agent integration tests.

**How to avoid:**
Implement optimistic locking on memory records using an `etag` or `version` field. Every promotion request must include the current `version`. If the version has changed since the agent last read the record, the promotion is rejected with a 409 Conflict. Agents must implement exponential backoff with jitter on conflict responses. Set a maximum retry count (e.g., 5 retries) after which the agent emits an alert to Langfuse and yields. Design fact promotions to be idempotent — promoting a record that is already at the target level returns 200, not an error. Write a dedicated integration test with two agents competing to promote the same record.

**Warning signs:**
- Promotion endpoint does not check a `version` or `etag` field
- Agent retry logic uses fixed delay (`time.sleep(1)`) instead of exponential backoff
- Langfuse shows agent runs that never complete (infinite retry loops)
- No maximum retry limit is configured for any agent

**Phase to address:** Phase 2 — design concurrency control before multi-agent scenarios are possible.

---

### Pitfall 13: Partial Team Adoption — Shadow Tools Bypass the Memory Layer

**What goes wrong:**
The platform is deployed. Some team members use LibreChat for their AI interactions. Others continue using ChatGPT.com directly, Grok's web interface, or Claude.ai. Their work products (insights, validated facts, decisions) never enter memory-api. The "all roads lead to memory" invariant breaks in practice. The team brain is incomplete. Members using xbrain see a partial picture and lose confidence in the platform. Adoption stalls. The platform becomes another tool in the pile, not the memory layer.

**Why it happens:**
The existing tools (ChatGPT.com, Claude.ai) have better UX, faster responses, and zero friction. Requiring everyone to go through LibreChat or Open WebUI imposes adoption cost. Without a visible payoff in the first weeks, team members revert to familiar tools. Shadow AI usage is the norm — IDC research shows 56% of employees use unauthorized AI tools even when sanctioned alternatives exist.

**How to avoid:**
Two approaches, applied together. First: make the on-ramp frictionless — the ChatGPT API path (listed as a supported frontend) must be fully operational, so users who prefer ChatGPT can still route through xbrain's memory layer via a custom GPT or API wrapper. This is the "meet people where they are" approach. Second: make the value visible early — the "memory delivers value" moment must arrive in Phase 1 or early Phase 2. If the first 4 weeks of xbrain produce no visible memory benefit (no "hey, the system remembered what we decided last week"), adoption will not take hold. Design Phase 1 to include at least one demo showing a conversation in LibreChat that recalls context from a previous session stored in memory-api. Set a team norm: any insight marked `VALIDATED` or higher must be routed through xbrain.

**Warning signs:**
- Team members reference "I asked ChatGPT directly about this" in meetings
- Memory-api write volume is low relative to team size (implies most AI interactions are bypassing it)
- Onboarding documentation for new team members does not mention xbrain as the primary AI interface
- No "memory recall demo" is planned for Phase 1 acceptance

**Phase to address:** Phase 1 — the adoption story must be designed before deployment, not added as a Phase 3 concern.

---

### Pitfall 14: Backup and Restore Not Designed Into the Single-VM Architecture

**What goes wrong:**
The platform runs on a single VM with Docker volumes. PostgreSQL data, Qdrant vectors, and MinIO assets all live on the VM's disk. The VM disk fails, gets accidentally deleted, or a botched migration corrupts the PostgreSQL volume. There is no backup. Months of team memory, validated facts, and agent work are gone permanently.

**Why it happens:**
"We'll add backups later" is one of the most common deferred decisions in single-VM deployments. The platform works fine day-to-day, creating false confidence. GCP persistent disks are reliable but not immune to logical corruption, accidental deletion, or botched migrations.

**How to avoid:**
Design the backup strategy in Phase 1 before any real data enters the system. Minimum viable backup: a daily cron job (or Docker sidecar using `offen/docker-volume-backup`) that dumps PostgreSQL with `pg_dump`, exports Qdrant snapshots via its REST API, and tarballs MinIO buckets to GCP Cloud Storage. Retain 7 daily + 4 weekly backups. Test the restore procedure explicitly before Phase 1 is declared complete — restore to a fresh Docker Compose environment and verify all services start with correct data. Never count on "the VM won't fail" as a backup strategy.

**Warning signs:**
- No backup cron job or sidecar container exists in `docker-compose.yml`
- The restore procedure has never been tested
- GCP Cloud Storage has no xbrain-backup bucket
- `docker-compose.yml` volumes are not all named volumes (anonymous volumes are harder to back up)

**Phase to address:** Phase 1 — make backup verification a Phase 1 acceptance gate. Data without backup is not production data.

---

### Pitfall 15: "Platform" Framing Fuels Endless Backlog and Nothing Ships

**What goes wrong:**
The idea.md explicitly frames xbrain as a platform with non-exhaustive capabilities. This is architecturally correct — but productively dangerous if internalized wrong. Every planning session spawns new capability ideas ("we could also do X"). The roadmap grows. Phase 1 keeps expanding ("let's add MinIO since it's in the architecture"). The definition of "done" for Phase 1 balloons. Six months pass with no working deployment.

**Why it happens:**
Platform thinking is expansive by design. The architecture supports unlimited extension, which makes it tempting to pre-build extension points that haven't been asked for yet. Each new capability feels cheap to add "while we're here." The team is ambitious and the vision is genuinely large.

**How to avoid:**
Phase 1 definition must be held firm: LibreChat + Open WebUI + PostgreSQL + Qdrant + a minimal memory-api stub that enforces the tagging contract on writes. Nothing else. MinIO, Neo4j, LangGraph, Remembra/Memstate, MCP tools — all Phase 2 or Phase 3. The acceptance test for Phase 1 is specific and binary: "A team member can open LibreChat, have a conversation using at least two different AI models, and that conversation is stored in memory-api with the 7 required tagging fields. Team A cannot see Team B's conversations." That is Phase 1. Anything more is scope creep. Use GSD's phase boundary enforcement to reject Phase 2 work entering Phase 1 tasks.

**Warning signs:**
- Phase 1 task list includes LangGraph, Neo4j, or MinIO
- "We should add X while we set up Y" appears in planning discussions
- Phase 1 has been replanned more than once
- No single sentence can describe what Phase 1 success looks like

**Phase to address:** Phase 1 planning — establish the hard boundary before implementation starts.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| LibreChat local MongoDB as conversation store | Faster Phase 1 demo | Multi-frontend invariant breaks silently | Never — write to memory-api from day one |
| `truth_level` as a mutable field (no event log) | Simpler schema | No audit trail, agents can self-promote to CANONICAL | Never — event log is the feature |
| Single Qdrant collection, no payload index on `team_scope` | Fewer moving parts | Cross-team bleed risk, O(N) filter scans | Never in multi-team context |
| Hardcoded API keys in docker-compose.yml | Faster local setup | Git exposure risk for production keys | Local dev only, never committed |
| No `mem_limit` in Docker Compose services | Simpler config | OOM kills take down all services together | Only acceptable in isolated dev |
| Memory framework imported directly (no adapter) | Faster integration | Vendor lock-in to potentially abandoned project | Acceptable only if POC proves framework is stable and team commits to maintaining the adapter later |
| Skip RLS on PostgreSQL tables | Faster schema design | Isolation enforced only in app code, one missed WHERE clause exposes cross-team data | Never |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| LibreChat + OIDC | Use LibreChat local auth for Phase 1 convenience | Configure OIDC before connecting the second frontend — identity federation cannot be retrofitted cleanly |
| Qdrant multitenancy | Skip `team_scope` payload index to simplify setup | Always create a payload index on `team_scope` before first write — missing index causes full scans that degrade under load |
| Neo4j in Docker | Use Neo4j default memory settings (512 MB heap) | Explicitly configure heap + pagecache via env vars; default settings cause excessive GC pauses and slow query performance |
| LangGraph + Langfuse | Initialize Langfuse but not call `flush()` in agent shutdown | Always call `langfuse.flush()` at agent exit; missing flush silently drops traces |
| LibreChat + memory-api | Store conversation metadata in LibreChat's MongoDB | Write all persistent artifacts to memory-api immediately; treat LibreChat DB as session-only cache |
| Docker secrets + GCP | Use `.env` files copied to VM for production secrets | Mount secrets as files via Docker secrets or use SOPS-encrypted files; never plain env vars in production |
| LangGraph state + memory-api | Agent writes state directly to memory-api per item in a loop | Use batch write endpoint; enforce rate limits at memory-api middleware |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| No PostgreSQL connection pooling before agents go live | LibreChat gets `connection refused` during agent batch runs | Add PgBouncer in transaction mode between memory-api and PostgreSQL before Phase 2 agents | At ~5 concurrent agent workers |
| Qdrant HNSW graph disconnected under high-cardinality team_scope filter | Semantic search returns poor results for teams with small corpora | Use `indexed_filtering` flag, pre-filter with payload index, or use dedicated shards for large teams | When a team has <1,000 vectors in a 100k+ collection |
| Neo4j heap below 1 GB on a 4 GB VM | Constant GC pauses, query timeouts, slow startup | Set `NEO4J_server_memory_heap_max__size` to at least 1G and pagecache to 512M | With any non-trivial graph (>10k nodes) |
| LangGraph agent memory spikes from large state versions | Memory-api container OOM killed during batch ingestion | Limit state size, implement streaming writes, set `mem_limit` on agent container | At ~50 MB of per-run state |
| MinIO without disk quotas | Single large upload fills VM disk, takes down all services | Set MinIO bucket quotas and VM disk alerts at 70% utilization | At first large PDF ingestion batch |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| API keys committed to git history | Permanent exposure even after removal; keys must be rotated | Pre-commit hook (`detect-secrets`), `.gitignore` on day one, audit `git log` before first push |
| PostgreSQL exposed on public VM port | Direct DB access bypasses all memory-api invariants | Bind PostgreSQL to `127.0.0.1` only; only memory-api connects to it via internal Docker network |
| Qdrant REST API exposed publicly | Vector data from all teams directly accessible | Bind Qdrant to internal Docker network only; access only via memory-api |
| Agent with CANONICAL promotion rights | Agents can elevate their own outputs to canonical truth without human review | Agents limited to EPHEMERAL → WORKING promotion; VALIDATED and above require human role |
| Same OIDC client_secret in dev and production | Production token signing key exposed in dev environment | Separate OIDC client registrations and secrets per environment |

---

## "Looks Done But Isn't" Checklist

- [ ] **Team isolation:** Cross-team bleed test passes — verify Team A cannot retrieve Team B data via direct Qdrant query, direct PostgreSQL query, and memory-api query
- [ ] **Tagging contract:** A write to memory-api without all 7 required fields returns 422 — not silently stored with nulls
- [ ] **Truth-level promotion:** Promotion without appropriate role returns 403, not 200
- [ ] **Identity federation:** The same person logged into LibreChat and Open WebUI appears as the same `source_user_id` in memory-api — confirmed, not assumed
- [ ] **Backup:** A full restore from backup has been tested on a clean environment — not just "the backup script runs"
- [ ] **Agent rate limiting:** Memory-api remains responsive to LibreChat users during a 1,000-item agent batch write — confirmed under load
- [ ] **Secrets hygiene:** `git log --all --oneline -- .env` returns no results; `detect-secrets scan` returns clean
- [ ] **Memory-api is not a proxy:** At least 3 business invariants are enforced at the API layer and tested independently of the database

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Data locked in frontend DBs | HIGH | Export LibreChat MongoDB conversations, write migration script to memory-api format, re-ingest with correct tagging, verify truth_levels |
| Identity fragmentation after both frontends live | HIGH | Map all existing memories by frontend-local user ID to OIDC sub, run migration, update all `source_user_id` fields, test attribution |
| Secrets committed to git | HIGH | Rotate all exposed keys immediately, use `git filter-repo` to scrub history, force-push (coordinate with team), audit for unauthorized usage |
| VM OOM kills without backup | CRITICAL | Restore from last backup; if no backup exists, data is lost — cannot recover |
| Abandoned memory framework | MEDIUM | Implement the missing adapter interface backed by direct Postgres + Qdrant calls, freeze framework version, migrate incrementally |
| Cross-team bleed discovered in production | HIGH | Audit all retrieval logs to determine scope of exposure, notify affected teams, add RLS and payload indexes immediately, re-test isolation |
| Phase 1 scope creep (too large to ship) | MEDIUM | Cut to the hard minimum definition (see Pitfall 15), move deferred work to Phase 2 backlog, reset acceptance criteria |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Memory-api as thin proxy | Phase 1 — design before first frontend | Invariant unit tests pass; rejection test for missing tagging fields |
| Frontend-coupled state | Phase 1 — establish pattern on first integration | LibreChat local MongoDB has no persistent memory records |
| Team isolation at app layer only | Phase 1 (PostgreSQL RLS design), Phase 2 (Qdrant payload index) | Cross-team bleed integration test passes at DB layer, not just API layer |
| Cross-team bleed in Qdrant | Phase 2 — when Qdrant becomes active | Payload index confirmed created; search without team_scope filter returns empty, not all-team results |
| Identity fragmentation | Phase 1 — before second frontend connects | Same user has same `source_user_id` in memory-api across both frontends |
| VM memory saturation | Phase 1 (planning), Phase 2 (VM resize before services added) | `docker stats` total under 60% of host RAM with all Phase 2 services running |
| Truth-level promotion fire-and-forget | Phase 2 — before first agent deployment | `promotion_events` table exists; field-level PATCH of `truth_level` returns 405 Method Not Allowed |
| Premature Neo4j | Phase 3 — hold the line | Neo4j container absent from Phase 1 and Phase 2 docker-compose.yml |
| Framework abandonment | Phase 2 — abstraction before integration | `MemoryProvider` interface exists; no direct framework imports outside the adapter |
| Secrets in git | Phase 1 — day zero, before first commit | `detect-secrets scan` returns clean; `.env` in `.gitignore` |
| Agent hot loops | Phase 2 — before first agent deployment | Batch endpoint exists; rate-limit middleware present; load test passes |
| Multi-agent deadlock | Phase 2 — concurrency design | Optimistic locking test with competing promotions passes; backoff implemented |
| Partial team adoption | Phase 1 — design adoption story before deployment | Memory-recall demo included in Phase 1 acceptance; team norm documented |
| No backup strategy | Phase 1 — restore test before any real data | Restore procedure tested on clean environment; backup appears in GCP Cloud Storage |
| Scope creep / platform trap | Phase 1 planning — hold boundary firm | Phase 1 task list contains no Phase 2 or Phase 3 components |

---

## Sources

- Multi-tenant vector isolation: [Qdrant Multitenancy Documentation](https://qdrant.tech/documentation/manage-data/multitenancy/) | [Qdrant Tiered Multitenancy (v1.16)](https://qdrant.tech/blog/qdrant-1.16.x/) | [Multi-tenant vector search pitfalls (Medium)](https://kulekci.medium.com/multi-tenant-vector-search-in-practice-building-a-shared-knowledge-base-with-qdrant-7b7928ba00fe)
- Multi-tenant AI agent isolation: [Bulkhead patterns for AI agents](https://brandonlincolnhendricks.com/research/implementing-bulkhead-isolation-patterns-multi-tenant-ai-agent-systems-google-cloud) | [Multi-tenant isolation for AI agents — security architecture](https://blaxel.ai/blog/multi-tenant-isolation-ai-agents)
- LangGraph production patterns: [LangGraph production agents](https://www.alphabold.com/langgraph-agents-in-production/) | [LangGraph memory overview](https://docs.langchain.com/oss/python/langgraph/memory)
- LibreChat OIDC: [LibreChat OAuth2/OIDC](https://www.librechat.ai/docs/configuration/authentication/OAuth2-OIDC) | [Open WebUI SSO](https://docs.openwebui.com/troubleshooting/sso/)
- VM sizing and OOM: [e2-medium specs](https://sparecores.com/server/gcp/e2-medium) | [Docker OOM killer behavior](https://medium.com/@mdmarjanrafi/devops-scenario-11-why-your-docker-container-exceeds-memory-limits-deep-dive-into-cgroups-7c4930633d2c)
- Neo4j Docker memory: [Neo4j memory configuration](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/) | [Neo4j Docker configuration](https://neo4j.com/docs/operations-manual/current/docker/configuration/)
- Docker secrets and git leak prevention: [Docker Compose secrets management](https://docs.docker.com/compose/how-tos/use-secrets/) | [GitGuardian secrets in Docker](https://blog.gitguardian.com/how-to-handle-secrets-in-docker/)
- Backup strategy: [Docker volume backup patterns](https://osmosys.co/blog/backup-and-restore-of-docker-volumes-a-step-by-step-guide/) | [docker-volume-backup tool](https://offen.github.io/docker-volume-backup/how-tos/restore-volumes-from-backup.html)
- Langfuse observability gaps: [Missing traces FAQ](https://langfuse.com/faq/all/missing-traces) | [LangGraph + Langfuse integration issue](https://github.com/orgs/langfuse/discussions/6960)
- Shadow AI / partial adoption: [Shadow AI statistics 2026](https://www.lasso.security/blog/what-is-shadow-ai) | [Shadow AI enterprise risks](https://witness.ai/blog/shadow-ai/)
- Framework maturity: [Remembra GitHub](https://github.com/remembra-ai/remembra) | [Memori GitHub](https://github.com/MemoriLabs/Memori) | [Memstate benchmark 2026](https://memstate.ai/blog/ai-memory-benchmark-2026)
- RAG platform failure modes: [Why most RAG projects fail in production](https://towardsai.net/p/machine-learning/why-most-rag-projects-fail-in-production-and-how-to-build-one-that-doesnt)

---
*Pitfalls research for: xbrain — AI memory platform, multi-team, multi-frontend, self-hosted*
*Researched: 2026-05-02*

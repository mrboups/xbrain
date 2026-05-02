# Project Research Summary

**Project:** xbrain -- AI Cognitive OS (collective persistent-memory platform)
**Domain:** Self-hostable multi-team AI memory platform, multi-frontend, multi-agent
**Researched:** 2026-05-02
**Confidence:** HIGH (stack, architecture, pitfalls) / MEDIUM (memory framework maturity)

---

## DECISIONS REQUIRED BEFORE ROADMAPPER

Three findings from parallel research directly conflict with the current PROJECT.md and must be resolved before requirements and roadmap are finalized.

### Decision 1: Replace Memstate with mem0 or native implementation

**Current PROJECT.md:** Names Memstate (versioning + conflict detection) as a Phase 2 component.

**Research finding (STACK.md + ARCHITECTURE.md, independently):** memstate.ai is a proprietary cloud-only SaaS service with no self-hosted option. It violates the OSS constraint. The similarly-named scream4ik/MemState (Apache 2.0, v0.5.1, 12 stars, beta, no Docker image) is a different and far less mature project.

**Two viable paths:**

| Option | Description | Confidence |
|--------|-------------|------------|
| **A: mem0 open source** | Apache 2.0, 40k+ stars, self-hosted via FastAPI + PostgreSQL + pgvector, versioned fact storage since v1.0.4 | MEDIUM (needs 1-day POC) |
| **B: Native in memory-api** | Build versioning in memory-api using the truth_level_transitions table, facts.version column, is_active soft-delete -- already fully designed in ARCHITECTURE.md | HIGH (fully within our control) |

**Architecture recommendation:** Option B is safer for Phase 2 (zero external dependency). mem0 can be integrated later as an adapter if the POC confirms it maps to xbrain truth-level state machine.

**Question for user:** Proceed with native Option B for Phase 2, with mem0 as a Phase 3 candidate after POC? Or run the mem0 POC before Phase 2 planning starts?

---

### Decision 2: Remembra maturity -- POC required before Phase 2 commitment

**Current PROJECT.md:** Names Remembra as the Phase 2 long-term memory + entity graph component.

**Research finding (ARCHITECTURE.md):** Remembra has 13 stars and a SQLite backend, insufficient for multi-team production load. STACK.md found production features (2FA, audit logs, 100% LoCoMo benchmark), but the SQLite backend is the blocking risk for concurrent writes.

**Two viable paths:**

| Option | Description | Risk |
|--------|-------------|------|
| **A: Use Remembra with shared Qdrant** | Override QDRANT_URL to point at xbrain Qdrant; use entity graph + provenance; accept SQLite as low-volume operational store | MEDIUM -- test in POC |
| **B: Native entity resolution in memory-api** | PostgreSQL JSONB for entity graph in Phase 2, Neo4j in Phase 3; no Remembra dependency | LOW risk, more build work |

**Question for user:** Run a 1-day Remembra POC before Phase 2 planning (recommended), or skip Remembra and build entity resolution natively?

---

### Decision 3: VM sizing -- e2-medium is insufficient from Phase 2

**Current PROJECT.md:** States e2-medium (~25 EUR/mo) as baseline, with sizing can go up if Phase 2/3 require it.

**Research finding (STACK.md + ARCHITECTURE.md + PITFALLS.md, all three independently):**

| Phase | Services | RAM floor | VM needed |
|-------|----------|-----------|-----------|
| Phase 1 | LibreChat + Open WebUI + PostgreSQL + Qdrant + memory-api | ~4-6 GB | e2-medium (4 GB) tight; e2-standard-2 (8 GB) safe |
| Phase 2 | + agent-runtime + MinIO + Remembra/mem0 + Langfuse | ~6-8 GB | e2-standard-2 (8 GB, ~38-49 EUR/mo) required |
| Phase 3 | + Neo4j + mcp-gateway + tools + Langfuse full (ClickHouse 1.5 GB alone) | ~12-16 GB | e2-standard-4 (16 GB, ~98 EUR/mo) OR separate Langfuse VM (~62 EUR/mo total) |

Langfuse v3 bundles ClickHouse + Redis + MinIO. The Langfuse stack alone requires 3-4 GB minimum and cannot coexist on an e2-medium with Phase 1. Either defer Langfuse to Phase 2 (after VM upgrade) or use a separate VM from the start.

**Question for user:** Start on e2-standard-2 from Phase 1 (safer, ~38-49 EUR/mo)? Or start on e2-medium and defer Langfuse to Phase 2 after the VM upgrade?

---

## Executive Summary

xbrain is a self-hostable collective AI memory platform -- not a chat workspace. The differentiator is the memory-api layer that enforces team-scoped, truth-level-tagged storage across all frontends (LibreChat, Open WebUI, ChatGPT API, Claude Code) and agents (LangGraph). Every design decision in the research confirms this: the memory layer is the product, the frontends are pluggable clients. The standard approach for this class of system is a domain service (not a CRUD proxy) at the center, with vector search (Qdrant), relational event sourcing (PostgreSQL), and graph relationships (Neo4j) as specialized stores each accessed only through the domain service. The tagging contract and truth-level state machine must be enforced as infrastructure-level invariants from day one -- retrofitting them after data exists is prohibitively expensive.

The recommended stack is well-suited to the requirements with two exceptions that must be resolved before Phase 2 planning: Memstate is not self-hostable and must be replaced, and Remembra SQLite backend needs POC validation before being committed to production load. Everything else -- LibreChat (MIT), Open WebUI (acceptable for internal use), PostgreSQL 17, Qdrant v1.17.1, LangGraph 1.1.0, Langfuse (MIT), Neo4j Community -- is production-verified and license-compatible with the OSS constraint. The MinIO Docker Hub discontinuation is already worked around using the Chainguard image (cgr.dev/chainguard/minio), which Langfuse itself already uses.

The key risks are not technical but architectural: (1) memory-api degrading into a thin CRUD proxy if enforcement is not designed from Phase 1, (2) data fragmentation across frontend local databases if the multi-frontend invariant is not enforced as a Phase 1 acceptance gate, and (3) identity fragmentation if OIDC is not configured before the second frontend connects. The VM sizing risk (OOM at Phase 2/3) is solvable with a planned upgrade path. The memory framework maturity risk (Remembra/Memstate) is solvable with a 1-day POC before Phase 2 planning locks in component choices.

---

## Key Findings

### Recommended Stack

The pre-selected stack is sound. The main adjustment is replacing Memstate (cloud-only) with either mem0 (Apache 2.0, 40k+ stars) or native implementation in memory-api. All other components are production-verified with confirmed Docker images and OSS licenses.

**Core technologies:**

| Component | Purpose | Version | License | Status |
|-----------|---------|---------|---------|--------|
| LibreChat | Primary multi-model chat UI (Claude + GPT + Grok) | v0.8.2-rc2 | MIT | Production-ready |
| Open WebUI | Admin / RAG / agent-testing | v0.9.0 | Custom non-OSI, branding clause | OK for internal use |
| PostgreSQL | Event store, audit log, truth-level state machine | 17.9 | PostgreSQL License | Production-ready |
| Qdrant | Vector embeddings, per-team semantic retrieval | v1.17.1 | Apache 2.0 | Production-ready |
| LangGraph | Agent graph runtime (library only, not Platform) | 1.1.0 | MIT | Production-ready |
| Langfuse | LLM observability (self-hosted) | 3.172.1 | MIT | RAM-intensive (ClickHouse) |
| Neo4j Community | Knowledge graph, entity lineage, provenance | 2026.04.0 | GPL v3 / AGPL v3 + Commons Clause | Phase 3 only |
| MinIO | Binary asset storage (S3-compatible) | RELEASE.2026-03-25 | AGPLv3 | Use cgr.dev/chainguard/minio |
| mem0 (Memstate replacement) | Fact versioning + conflict detection | 1.0.x | Apache 2.0 | POC required |
| Remembra | Long-term memory + entity graph | v0.13.2 | MIT | POC required before Phase 2 |
| Memori | Structured extraction (facts, tasks, entities) | v3.3.2 | Apache 2.0 | Alpha; POC required |

**What NOT to use:** memstate.ai (cloud-only SaaS), LangGraph Platform/LangSmith (proprietary), official minio/minio Docker image (discontinued Oct 2025), Pinecone/OpenAI Assistants/Notion-as-store (cloud-only).

### Expected Features

**Must have -- table stakes (Phase 1):**
- Docker Compose single-command deployment with health checks
- Google SSO via OIDC + local email/password fallback
- Multi-model chat: Claude + GPT + Grok in LibreChat
- memory-api enforcing 7-field tagging contract on every write (reject partial tags)
- Conversation storage with team_scope from day 1 (born at EPHEMERAL)
- Basic semantic RAG: file upload to embedding to question over document
- RBAC: Admin / Member / Viewer enforced at memory-api layer
- Team isolation by default
- LLM call traces in Langfuse

**Should have -- differentiators (Phase 2):**
- Long-term entity memory (Remembra or native)
- Truth-level promotion workflow with human approval gates (HITL via LangGraph interrupt)
- Permission-aware RAG: team-scoped + truth_level-filtered retrieval
- LangGraph ingestion + validation agents
- Context injection from team memory into every chat system prompt
- Second-opinion parallel model call (Grok contradiction check)
- Per-team cost dashboard in Langfuse
- PgBouncer connection pool before agents go live

**Should have -- Phase 3:**
- Neo4j graph layer + lineage queries
- Automatic structured extraction (Memori or LangGraph + LLM structured output)
- Google Drive sync (incremental, team-scoped, tagged, starts at WORKING)
- MCP gateway + first tools: scraper, calendar, deck-service
- Memory lineage trace (Langfuse + Neo4j)

**Defer (v2+ / out of scope):**
- Mobile app / native app
- SaaS multi-tenant for external clients
- LDAP / Active Directory
- Kubernetes deployment
- Notion/Slack/Linear integrations before Drive stable
- Guest accounts for external clients
- Bulk import bypassing promotion workflow
- Global cross-team search by default

### Architecture Approach

The architecture is a hub-and-spoke model with memory-api as the single source of truth. All frontends, agents, and internal tools are stateless clients -- they never write to PostgreSQL, Qdrant, or Neo4j directly. The tagging contract is enforced as a middleware chain rejecting any write missing the 7 required fields; team_scope is extracted from the JWT, not from the request body. Truth-level promotion is a dedicated state-machine endpoint with role checks and an immutable truth_level_transitions event log -- field-level PATCH of truth_level returns 405.

**Major components:**

1. **memory-api (FastAPI, Python)** -- enforces tagging contract, owns all persistent writes, retrieval orchestration, truth-level state machine, audit log; never calls frontends directly
2. **agent-runtime (LangGraph)** -- agent graph execution, HITL interrupts, checkpointing to PostgreSQL (checkpoint schema only); all domain data via memory-api HTTP calls
3. **mcp-gateway (FastAPI)** -- tool registry, JWT validation, auth context injection (team_id, user_id) into every tool invocation
4. **Storage layer** -- PostgreSQL (event store + state machine), Qdrant (per-team collections: xbrain_{team_id}_facts), Neo4j (entity/lineage graph, Phase 3 only), MinIO (binary assets)
5. **LibreChat** -- conversation UI, multi-model; local MongoDB is session cache only; all persistent artifacts to memory-api
6. **Open WebUI** -- admin UI + Python pipelines; __user__ context provides team_id; never queries storage directly
7. **Langfuse** -- OTLP trace ingestion; runs its own ClickHouse + Redis + MinIO; deploy on separate VM in Phase 3

**Key patterns:**
- Tagging-at-Write Enforcement: middleware chain rejects partial tagging before any DB write
- Truth-Level as Read Filter: every retrieval call requires explicit truth_levels; conversations at EPHEMERAL never retrieved by agents
- Event Emission for Agent Triggers: memory-api emits to agent_events PostgreSQL table; agent-runtime polls; no direct coupling
- Per-team Qdrant collections: hard isolation at storage layer, not filter-only

### Critical Pitfalls

1. **memory-api degrades into a thin CRUD proxy** -- Design as a domain service from Phase 1 day one: dedicated POST /v1/facts/{id}/promote with role checks and audit log; PATCH /v1/facts/{id} with truth_level in body returns 405. Add invariant unit tests on Phase 1 day one.

2. **Frontend-coupled state (chatbot-workspace trap)** -- Phase 1 acceptance criterion: LibreChat local MongoDB must have zero persistent memory records; all artifacts to memory-api within the same request.

3. **Team isolation at application layer only** -- PostgreSQL RLS policies enforced at DB engine level; Qdrant client wrapped in TeamScopedQdrantClient; payload index on team_scope in migration scripts. Cross-team bleed integration test is a Phase 1/2 acceptance gate.

4. **Identity fragmentation across frontends** -- Configure OIDC (Google SSO) before the second frontend connects. Canonical source_user_id = OIDC sub claim, not a frontend-local ID.

5. **VM OOM kills at Phase 2/3** -- e2-medium viable Phase 1 only. Upgrade to e2-standard-2 before Phase 2 services start. Set mem_limit on every Docker Compose service. Upgrade the VM before Phase 2, not after the first OOM kill.

6. **Memory framework abandonment** -- MemoryProvider interface in /packages/memory-models before any framework integration. No direct framework imports in application code.

7. **Scope creep / platform trap** -- Phase 1 acceptance test is one binary sentence: "A team member can open LibreChat, have a conversation using at least two AI models, and that conversation is stored in memory-api with all 7 tagging fields. Team A cannot see Team B conversations." Anything more is Phase 2.

---

## Implications for Roadmap

The three-phase structure from PROJECT.md is architecturally correct. Research confirms the ordering and refines component lists. Main adjustments: memory-api stub is a Phase 1 deliverable (not Phase 2), Memstate is replaced, VM upgrade is planned into phase transitions, and the POC gates Phase 2 planning.

### Phase 1 -- Infrastructure Foundation + Frontends + memory-api Stub

**Rationale:** The memory-api stub must exist in Phase 1 to enforce the tagging contract from the first conversation. Without it, Phase 1 produces untagged data requiring retroactive migration before Phase 2. The stub is minimal but must be a real domain service.

**Delivers:**
- Docker Compose stack running locally + on GCP VM
- LibreChat (Claude + GPT + Grok) + Open WebUI (admin)
- Google SSO via OIDC configured on both frontends (before second frontend connects)
- PostgreSQL base schema (orgs, teams, members, projects, conversations)
- Qdrant (per-team collections, payload index on team_scope)
- memory-api stub: POST /v1/conversations, POST /v1/retrieve (semantic only), POST /v1/assets, POST /v1/rag-compat, POST /auth/token, GET /v1/health
- Langfuse (if VM is e2-standard-2; defer to Phase 2 if staying on e2-medium)
- Backup strategy: daily pg_dump + Qdrant snapshot to GCP Cloud Storage; restore tested before Phase 1 declared complete
- Secrets hygiene: .gitignore, detect-secrets pre-commit hook, .env.example

**Must avoid (Pitfalls 1, 2, 5, 10, 14, 15):** memory-api as proxy; conversations in LibreChat MongoDB; OIDC deferred; secrets in git; no backup; Phase 2 components in Phase 1 docker-compose.yml

**Research flag:** Standard patterns, does not need /gsd-research-phase. memory-api stub design fully specified in ARCHITECTURE.md.

---

### Phase 2 -- Memory Intelligence + Agent Runtime

**Rationale:** Phase 2 is where xbrain becomes differentiated. The truth-level promotion workflow, entity memory, and agent runtime are the core value proposition. Phase 2 requires VM upgrade to e2-standard-2 before the first new service is added.

**Delivers:**
- VM upgrade to e2-standard-2 (8 GB, ~38-49 EUR/mo) before any Phase 2 service starts
- 1-day POC (before Phase 2 planning finalizes): Remembra v0.13.2 with shared Qdrant + mem0 vs native implementation
- MemoryProvider interface in /packages/memory-models before any framework is integrated
- Full facts schema in memory-api: facts table, truth_level_transitions, optimistic locking (facts.version), audit_log
- Truth-level promotion workflow: dedicated endpoint, RBAC role checks, promotion event log, no field-level PATCH
- Long-term memory: Remembra (if POC passes) OR native entity resolution in PostgreSQL JSONB
- Fact versioning + conflict detection: mem0 (if POC passes) OR native truth_level_transitions + is_active soft-delete
- agent-runtime (LangGraph): conversation-ingestion-agent, fact-conflict-detector-agent, approval-workflow-agent
- HITL: LangGraph interrupt() to pending approval in memory-api to human approves in Open WebUI to graph resumes
- MinIO (Chainguard image) for asset storage
- Permission-aware RAG: Qdrant per-team collections + truth_level payload filter
- Langfuse (if deferred from Phase 1)
- PgBouncer connection pool (before agents go live)
- Rate-limit middleware + POST /v1/facts/batch endpoint (before first agent deployment)
- Optimistic locking + exponential backoff for competing agent promotions

**Must avoid (Pitfalls 3, 4, 6, 7, 9, 11, 12):** isolation at app layer only; cross-team Qdrant bleed; VM upgrade deferred; truth_level as mutable field; direct framework imports; agent hot loops; multi-agent deadlock

**Research flag:** Needs /gsd-research-phase for: (a) mem0 self-hosting PostgreSQL integration specifics, (b) Remembra POC results (shared Qdrant config), (c) LangGraph AsyncPostgresSaver schema. POC results determine memory framework path.

---

### Phase 3 -- Graph, Extraction + Integrations

**Rationale:** Neo4j and Memori are Phase 3 because they require stable facts from Phase 2 truth-level workflow to be meaningful. Drive sync and MCP gateway require the ingestion pipeline (POST /v1/ingest) proven at Phase 2 scale.

**Delivers:**
- VM upgrade to e2-standard-4 (16 GB) OR Langfuse moved to separate e2-small VM (~62 EUR/mo total)
- Neo4j Community (Phase 3 only -- hold this line firmly): entity/lineage/dependency graph; Cypher via memory-api graph retrieval modality
- Memori (after 1-day POC confirming BYODB mode works): structured extraction; fallback: LangGraph + LLM structured output
- drive-sync service: OAuth2 per-team tokens in PostgreSQL, incremental sync, ingests to POST /v1/ingest at WORKING
- mcp-gateway: tool registry, JWT validation, auth context injection; first tools: scraper, calendar, deck-service
- Graph traversal search: POST /v1/retrieve extended with graph modality
- Memory lineage trace (Langfuse + Neo4j)
- Temporal memory queries
- PUBLIC truth-level: cross-org readable items

**Must avoid (Pitfalls 8, 13):** Neo4j before Phase 3; no ChatGPT API passthrough causing partial adoption

**Research flag:** Needs /gsd-research-phase for: (a) Memori BYODB mode self-hosting specifics, (b) Google Drive OAuth2 incremental sync, (c) Neo4j heap + pagecache tuning. MCP gateway follows standard patterns; no research needed.

---

### Phase Ordering Rationale

- **memory-api stub in Phase 1**: tagging contract cannot be retrofitted onto existing untagged conversations
- **OIDC before second frontend**: identity fragmentation is a migration problem once data exists in two systems
- **POC before Phase 2 planning**: Remembra SQLite + Memstate cloud-only mean Phase 2 cannot be planned without empirical results
- **VM upgrade at Phase 2 start, not during Phase 2**: OOM kills corrupt PostgreSQL volumes; upgrade must precede first Phase 2 service
- **Neo4j strictly in Phase 3**: graph is only meaningful once facts are stable
- **Backup tested before any real data**: Phase 1 acceptance gate

---

### Research Flags

Phases needing /gsd-research-phase during planning:
- **Phase 2:** mem0 self-hosting integration, Remembra shared Qdrant config (POC results), LangGraph AsyncPostgresSaver schema
- **Phase 3:** Memori BYODB self-hosting, Google Drive OAuth2 incremental sync, Neo4j Community Edition memory tuning

Phases with standard patterns (skip research-phase):
- **Phase 1:** LibreChat + Open WebUI OIDC well-documented; memory-api stub fully specified in ARCHITECTURE.md; PostgreSQL schema fully designed
- **Phase 3 (MCP gateway):** FastAPI + HTTP forwarding follows standard patterns; tool registration schema specified in ARCHITECTURE.md

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All components verified via live repos and Docker Hub. Memstate cloud-only confirmed. mem0 maturity confirmed (40k stars, Apache 2.0). |
| Features | HIGH | Platform invariants authoritative from PROJECT.md + idea.md. Feature set well-understood for this class of system. |
| Architecture | HIGH | Core decisions verified against official docs. PostgreSQL schema fully designed in ARCHITECTURE.md. API surface fully specified. |
| Pitfalls | HIGH (architectural) / MEDIUM (framework maturity) | Architectural pitfalls well-documented. Memory framework production behavior requires empirical POC. |

**Overall confidence:** HIGH for architectural decisions; MEDIUM for memory framework layer (Remembra, mem0, Memori) until Phase 2 POC results available.

### Gaps to Address

- **Remembra shared Qdrant config**: can QDRANT_URL be overridden to point at xbrain Qdrant? Does team_scope filtering work via Qdrant payload filters when Remembra manages the vectors? **Handle:** 1-day POC before Phase 2 planning.
- **mem0 truth-level workflow compatibility**: does mem0 versioned fact storage map to xbrain 5-level state machine? **Handle:** same POC as Remembra.
- **Memori BYODB mode**: described in docs but not empirically confirmed self-hostable. **Handle:** 1-day POC before Phase 3 planning. Fallback: LangGraph + LLM structured output.
- **LibreChat memory.agent config at v0.8.2-rc2**: integration path documented but untested at this exact version. **Handle:** verify during Phase 1 integration; fallback is the RAG API shim.
- **Open WebUI __user__ team_id injection**: requires custom claim mapping in OIDC provider. **Handle:** verify during Phase 1 OIDC configuration.

---

## Sources

### Primary (HIGH confidence)

- LibreChat releases + docs: https://www.librechat.ai -- v0.8.2-rc2, MIT confirmed
- Open WebUI releases + license: https://github.com/open-webui/open-webui, https://docs.openwebui.com/license/
- Qdrant docs + Docker Hub + multitenancy: https://qdrant.tech -- v1.17.1, Apache 2.0
- Langfuse releases + self-hosting + docker-compose (live): https://langfuse.com -- v3.172.1, MIT, ClickHouse confirmed
- LangGraph PyPI + license: https://pypi.org/project/langgraph/ -- v1.1.0, MIT
- Neo4j Docker Hub + licensing: https://hub.docker.com/_/neo4j -- 2026.04.0 community
- PostgreSQL 17.9: https://www.postgresql.org
- MinIO Chainguard image: confirmed via Langfuse docker-compose.yml
- Remembra GitHub + Docker Hub: https://github.com/remembra-ai/remembra -- v0.13.2, MIT, SQLite backend confirmed
- Memstate.ai: https://memstate.ai -- confirmed cloud-only SaaS, not self-hostable
- mem0 open source: https://github.com/mem0ai/mem0 -- Apache 2.0, 40k+ stars
- GCP VM specs: e2-medium (4 GB), e2-standard-2 (8 GB), e2-standard-4 (16 GB) confirmed

### Secondary (MEDIUM confidence)

- Memori GitHub + pyproject.toml: https://github.com/MemoriLabs/Memori -- v3.3.2, Alpha classifier; BYODB mode not empirically tested
- LangGraph + Langfuse integration: https://github.com/orgs/langfuse/discussions/6960
- LibreChat memory config block: https://www.librechat.ai/docs/features/memory -- not tested at v0.8.2-rc2
- Open WebUI pipelines + __user__ context: https://docs.openwebui.com/features/extensibility/pipelines/

### Tertiary (LOW confidence -- needs POC)

- Remembra shared Qdrant override: inferred from docker-compose.yml config, not tested
- mem0 + xbrain truth-level mapping: compatibility assumed, not tested
- Memori BYODB PostgreSQL mode: described in docs, not confirmed working self-hosted

---

*Research completed: 2026-05-02*
*Ready for roadmap: CONDITIONAL -- resolve the 3 decisions above before roadmapper runs*

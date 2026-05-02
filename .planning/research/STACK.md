# Stack Research

**Domain:** Self-hostable collective AI memory platform (multi-frontend chat + structured memory + vector/graph/relational DB + agent runtime + object storage + observability)
**Researched:** 2026-05-02
**Confidence:** MEDIUM-HIGH (all components verified via live repos/Docker Hub; memory layer components Remembra/Memstate/Memori require additional POC validation)

---

## Executive Summary

The pre-selected stack is largely sound for a self-hosted internal platform. Three critical issues emerged from research:

1. **Memstate.ai is NOT open source.** The product at memstate.ai is a managed cloud service with no self-hosted option. The similarly-named `scream4ik/MemState` on GitHub (Apache 2.0, beta, 12 stars, last commit Dec 2025) is the closest OSS equivalent for transactional memory with ACID guarantees, but it is a different project and far less mature. This is the most urgent risk — the stack loses a critical component.
2. **Langfuse v3 bundles ClickHouse**, which inflates RAM requirements significantly. The Langfuse docker-compose alone requires 4+ GB RAM — more than the entire e2-medium baseline (4 GB total). Running Langfuse on the same VM as the rest of the stack is not viable until e2-standard-2 (8 GB).
3. **MinIO has stopped publishing free Docker images to Docker Hub** (October 2025). The Langfuse docker-compose already works around this using `cgr.dev/chainguard/minio` (Chainguard's free hardened image). For the project's own MinIO usage, adopt the same Chainguard image or build from source.

---

## Component Status Matrix

| Component | Version (pinned) | Docker Image | License | Status | Confidence |
|-----------|-----------------|--------------|---------|--------|------------|
| LibreChat | v0.8.2-rc2 | `librechat/librechat:v0.8.2-rc2` | MIT | Active, production-ready | HIGH |
| Open WebUI | v0.9.0 | `ghcr.io/open-webui/open-webui:v0.9.0` | Custom (non-OSI, branding clause since v0.6.6) | Active, production-ready | HIGH |
| Remembra | v0.13.2 | `remembra/remembra:v0.13.2` | MIT | Active, production-ready | HIGH |
| Memstate.ai | N/A | N/A | **Proprietary / cloud-only** | **NOT self-hostable — BLOCKED** | HIGH |
| scream4ik/MemState | v0.5.1 | No official image | Apache 2.0 | Beta, 12 stars, last commit Dec 2025 | LOW |
| Memori | v3.3.2 | Dockerfile present, no published image | Apache 2.0 | Alpha (PyPI classifier), 14k stars | MEDIUM |
| Qdrant | v1.17.1 | `qdrant/qdrant:v1.17.1` | Apache 2.0 | Production-ready | HIGH |
| Neo4j Community | 2026.04.0 | `neo4j:2026.04.0-community` | GPL v3 / AGPL v3 (with Commons Clause modifications) | Production-ready | HIGH |
| PostgreSQL | 17.x (17.9) | `postgres:17` | PostgreSQL License (permissive) | Production-ready | HIGH |
| MinIO | RELEASE.2026-03-25 | `cgr.dev/chainguard/minio:latest` | AGPLv3 (server); Apache 2.0 (SDKs) | Active, but official Docker Hub images discontinued Oct 2025 | HIGH |
| LangGraph | 1.1.0 (Python lib) | Not a container — Python package | MIT | Active, production-ready | HIGH |
| Langfuse | 3.172.1 | `langfuse/langfuse:3` + `langfuse/langfuse-worker:3` | MIT (core features) | Active, production-ready | HIGH |

---

## Recommended Stack (Phase-by-Phase)

### Phase 1 — Core Infrastructure and Frontends

#### LibreChat (Primary Chat Frontend)

| Property | Value |
|----------|-------|
| Version | v0.8.2-rc2 (latest stable as of 2026-05-02) |
| Docker image | `librechat/librechat:v0.8.2-rc2` |
| License | MIT — unrestricted self-hosting and commercial internal use |
| RAM footprint | ~300–500 MB idle (Node.js app) |
| Dependencies it pulls in | MongoDB (mongo:8.0.20), MeiliSearch (getmeili/meilisearch:v1.35.1), pgvector (pgvector/pgvector:0.8.0-pg15) for RAG API |
| Multi-tenant / teams | RBAC + groups available since v0.8.5; DB-backed config overrides per role/group; Admin Panel GUI in progress (2026 roadmap) |
| API key injection | Via `.env`: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`; endpoint config in `librechat.yaml` |
| Why | MIT-licensed, most feature-complete multi-LLM chat frontend with native Anthropic, OpenAI, and xAI (Grok) support, MCP-ready, RBAC-capable |

**Warning:** LibreChat ships its own MongoDB instance by default. In the xbrain architecture, consolidate around PostgreSQL where possible. Use pgvector RAG backend instead of the default MeiliSearch where possible to reduce service count.

#### Open WebUI (Admin / RAG / Agent Testing Frontend)

| Property | Value |
|----------|-------|
| Version | v0.9.0 |
| Docker image | `ghcr.io/open-webui/open-webui:v0.9.0` |
| License | Custom "Open WebUI License" (non-OSI since v0.6.6, April 2025) — branding must be retained for deployments >50 users in 30 days |
| RAM footprint | ~500 MB–1 GB idle |
| Multi-tenant / teams | Basic RBAC (admin/user roles); per-user workspace isolation; true container-level isolation requires Enterprise License |
| Why | Best admin/RAG/agent-testing UI, light footprint, widely supported |

**License flag:** The Open WebUI license is no longer OSI-certified open source. For an internal team deployment that retains the Open WebUI branding, this is acceptable. If branding removal is required (e.g. white-label), an enterprise license is needed. For xbrain's use case (internal team, <50 active users initially), the free tier fully applies.

#### PostgreSQL (Primary Relational DB)

| Property | Value |
|----------|-------|
| Version | 17.9 |
| Docker image | `postgres:17` |
| License | PostgreSQL License (permissive, BSD-like) |
| RAM footprint | ~128 MB idle with default Docker config; tune `shared_buffers` to 256 MB for Phase 1 |
| Purpose | Event store, audit logs, truth-level workflows, permissions, user/team data |
| Why | Project constraint + industry standard; Phase 1 keeps it simple with a single Postgres instance shared by memory-api and LibreChat's RAG backend (via pgvector extension) |

#### Qdrant (Vector Store)

| Property | Value |
|----------|-------|
| Version | v1.17.1 |
| Docker image | `qdrant/qdrant:v1.17.1` |
| License | Apache 2.0 |
| RAM footprint | ~200–400 MB idle with no vectors; ~120 MB additional per 100k vectors (all-in-memory mode) — can use mmap mode to reduce to ~15 MB per 100k if disk-bound |
| Purpose | Semantic retrieval, agent memory, RAG search scoped by team/project |
| Why | Apache 2.0, excellent performance, native multi-vector + payload filtering (critical for team_scope / truth_level filter-at-retrieval), active development (v1.17.1 released March 2026) |

---

### Phase 2 — Memory Layer and Agent Runtime

#### Remembra (Long-term Memory, Entity Graph, Provenance)

| Property | Value |
|----------|-------|
| Version | v0.13.2 |
| Docker image | `remembra/remembra:v0.13.2` |
| License | MIT |
| RAM footprint | Not documented; estimated ~256–512 MB (Go/Rust process + embedded Qdrant connection); uses Qdrant as vector backend |
| Qdrant dependency | Bundles `qdrant/qdrant:v1.7.4` in its quickstart compose — **pin to `v1.17.1` in xbrain's compose to share a single Qdrant instance** |
| Production maturity | Yes — 11+ releases, 272 tests, 100% on LoCoMo benchmark, PII detection, 2FA, audit logs, Stripe billing integration |
| Last commit | April 25, 2026 |
| Why | MIT-licensed, actively maintained, self-hosted Docker image available, production-grade features, MCP-compatible |

**Integration note:** Remembra has its own Qdrant in its quickstart; in xbrain's multi-service compose, point Remembra at the shared Qdrant by overriding `QDRANT_URL` — do not run two Qdrant instances.

#### Memstate — BLOCKED: NOT open source

**Finding:** The product at memstate.ai is a proprietary cloud-only service. "No infrastructure. No database to manage" is by design — it is not self-hostable. The benchmark claiming 5.3× Mem0 accuracy is marketing for their cloud product.

**Alternative 1 — scream4ik/MemState (Apache 2.0, beta)**
- GitHub: https://github.com/scream4ik/MemState
- Version: v0.5.1 (Dec 2025), 12 stars, 49 commits
- Provides ACID-like transactional memory layer keeping SQL + vector DB in sync, with rollback(n) time travel
- **Risk:** Very low community adoption, beta-quality, no Docker image published — must build from source
- **Verdict:** Usable for POC but not for production Phase 2

**Alternative 2 — mem0 open source (Apache 2.0, production-ready)**
- GitHub: https://github.com/mem0ai/mem0 — 40k+ stars, actively maintained
- Self-hosted via Docker (PostgreSQL + pgvector + Neo4j)
- Versioned fact storage since v1.0.4 (Feb 2026) with timestamp parameter
- Docker: `pip install mem0ai` + FastAPI server
- **Verdict:** Best drop-in replacement for the "versioning + conflict handling" role Memstate was supposed to fill. Production-ready, widely adopted, OSS.

**Recommendation:** Replace Memstate with mem0 open-source for Phase 2. Run a 1-day POC comparing mem0 vs scream4ik/MemState before committing.

#### Memori (Structured Extraction — Facts, Tasks, Entities, Preferences)

| Property | Value |
|----------|-------|
| Version | v3.3.2 (April 29, 2026) |
| GitHub | https://github.com/MemoriLabs/Memori |
| Python package | `memorisdk` (PyPI) |
| License | Apache 2.0 |
| Docker | Dockerfile present in repo; no published image on Docker Hub — must build |
| Development status | **Alpha** (PyPI classifier: "Development Status :: 3 - Alpha") despite 14k stars |
| Last commit | April 29, 2026 (active) |
| Dependencies | `faiss-cpu`, `sentence-transformers`, `aiohttp`, `grpcio`, SQLAlchemy; PostgreSQL adapter available |
| RAM footprint | Estimated ~512 MB–1 GB (sentence-transformers model loaded) |
| Why | Apache 2.0, SQL-native, LLM-agnostic, supports structured extraction pipeline |

**Risk flag:** Despite impressive star count and recent activity, Memori carries an Alpha classifier in its own pyproject.toml. The API surface may change between Phase 2 and Phase 3. Run a POC in Phase 2 before building memory-api extraction pipeline on top of it.

#### LangGraph (Agent Runtime)

| Property | Value |
|----------|-------|
| Version | 1.1.0 (Python library, March 2026) |
| LangGraph SDK | 0.3.13 |
| Python package | `pip install langgraph==1.1.0` |
| License | MIT |
| Self-hosting | Library only — runs in-process or in a Docker container you build; **LangGraph Platform / LangSmith Deployment is proprietary** |
| RAM footprint | Negligible as a library — footprint is your agent code's footprint |
| Why | MIT-licensed, industry standard for stateful multi-actor agent graphs; native Langfuse integration via `langfuse.CallbackHandler` |

**Deployment pattern for xbrain:** Do NOT use LangGraph Platform (proprietary). Run LangGraph agents inside the `agent-runtime` service container (FastAPI + LangGraph). Use Langfuse CallbackHandler for tracing without LangSmith dependency.

---

### Phase 3 — Graph, Extraction, Integrations

#### Neo4j Community (Graph DB)

| Property | Value |
|----------|-------|
| Version | 2026.04.0 |
| Docker image | `neo4j:2026.04.0-community` |
| License | GPL v3 / AGPL v3 with Commons Clause restrictions |
| RAM footprint | Default Docker config: 512 MB heap + 512 MB page cache = ~1 GB; recommended minimum for dedicated use: 1 GB |
| Purpose | Relationship graph, entity lineage, dependency mapping, validation graph, truth-level promotion history |
| Why | Pre-selected; deep graph query capability that PostgreSQL cannot match; Community Edition is free to self-host as long as you do not redistribute Neo4j itself as a product |

**License note:** Neo4j Community is AGPL v3 with a Commons Clause addition. For internal self-hosted use where you are not reselling Neo4j's capabilities, this is acceptable. The restriction prevents building a "Neo4j-as-a-service" product on top of it — not relevant for xbrain's use case.

---

### Observability

#### Langfuse (LLM Observability)

| Property | Value |
|----------|-------|
| Version | 3.172.1 |
| Docker images | `langfuse/langfuse:3` (web) + `langfuse/langfuse-worker:3` (worker) |
| License | MIT (core features since June 2025); enterprise features (SCIM, audit logs, data retention) require commercial license |
| RAM footprint | Langfuse web + worker: ~512 MB each; **ClickHouse: ~1–2 GB minimum**; Redis 7: ~50 MB; Total Langfuse stack: ~3–4 GB minimum |
| Bundled services | ClickHouse (OLAP), Redis 7, PostgreSQL 17, MinIO (Chainguard image) |
| Production maturity | Yes — v3 is the current recommended version; v2 EOL'd Q1 2025 |

**Critical sizing issue:** Langfuse v3 requires a minimum of 4 CPU cores and 16 GB RAM for their official production recommendation. For low-throughput dev/test, it can run on 4 CPU + 4 GB, but ClickHouse alone consumes 1–2 GB. This means **Langfuse cannot coexist with the rest of the xbrain stack on an e2-medium (4 GB total)**. See VM Sizing section.

**Langfuse MinIO note:** Langfuse's bundled docker-compose already uses `cgr.dev/chainguard/minio` (free, hardened) instead of the official MinIO image (which was pulled from Docker Hub in October 2025). Do not add a separate MinIO service in xbrain's compose for Langfuse — let Langfuse manage its own.

---

### Object Storage

#### MinIO (Asset Storage — PDFs, Images, Datasets)

| Property | Value |
|----------|-------|
| Version | RELEASE.2026-03-25T00-00-00Z |
| Docker image | `cgr.dev/chainguard/minio:latest` (official Docker Hub images discontinued October 2025) |
| License | AGPLv3 (server); Apache 2.0 (Python/Go SDKs) |
| RAM footprint | ~256 MB idle (with `CI_CD=true` env var); ~1–2 GB during large uploads |
| S3-compatible API | Yes — all S3 SDKs (boto3, etc.) work natively |
| Why | Pre-selected; S3-compatible API means easy migration to real S3/GCS if needed; AGPLv3 server is acceptable for internal use (no redistribution of MinIO itself) |

**Docker Hub discontinuation:** MinIO stopped publishing to Docker Hub in October 2025. Use `cgr.dev/chainguard/minio:latest` (free, continuously rebuilt from source, minimal attack surface) or build from source. The Chainguard image is already used by Langfuse's own docker-compose.

---

## Supporting Libraries (Python SDK Layer for memory-api)

| Library | Version | Purpose | Install |
|---------|---------|---------|---------|
| `qdrant-client` | 1.17.1 | Python client for Qdrant vector operations | `pip install qdrant-client==1.17.1` |
| `neo4j` | 6.1.x | Python driver for Neo4j (replaces deprecated `neo4j-driver`) | `pip install neo4j` |
| `langgraph` | 1.1.0 | Agent graph runtime | `pip install langgraph==1.1.0` |
| `langgraph-sdk` | 0.3.13 | LangGraph client SDK | `pip install langgraph-sdk==0.3.13` |
| `langfuse` | 4.5.1 | Observability SDK (OTEL-based v3 SDK, GA June 2025) | `pip install langfuse==4.5.1` |
| `langchain-qdrant` | latest | LangChain↔Qdrant integration | `pip install langchain-qdrant` |
| `memorisdk` | 3.3.2 | Memori Python SDK (Alpha) | `pip install memorisdk==3.3.2` |
| `mem0ai` | latest (1.0.x) | Memory versioning + conflict resolution (Memstate replacement) | `pip install mem0ai` |
| `asyncpg` | latest | Async PostgreSQL driver for FastAPI | `pip install asyncpg` |
| `boto3` | latest | S3-compatible client for MinIO | `pip install boto3` |
| `fastapi` | latest | memory-api HTTP framework | `pip install fastapi uvicorn` |

---

## VM Sizing Assessment

### e2-medium (2 vCPU, 4 GB RAM, ~25€/month)

**Stack footprint estimate (Phase 1 only):**

| Service | RAM (idle, conservative) |
|---------|--------------------------|
| LibreChat API | 400 MB |
| LibreChat MongoDB | 200 MB |
| PostgreSQL 17 | 256 MB |
| Qdrant v1.17.1 | 300 MB |
| MeiliSearch (LibreChat) | 200 MB |
| memory-api (FastAPI) | 150 MB |
| Nginx / reverse proxy | 50 MB |
| OS + Docker overhead | 600 MB |
| **Phase 1 total** | **~2.2 GB** |

**Phase 1 conclusion:** e2-medium (4 GB) is viable for Phase 1 if LibreChat's MongoDB and MeiliSearch are kept lightweight. Margin is thin (~1.8 GB free).

**Phase 2 additions (Remembra + LangGraph agents + Open WebUI):**

| Addition | RAM |
|---------|-----|
| Remembra | 400 MB |
| Open WebUI | 600 MB |
| Agent runtime container | 300 MB |
| **Phase 2 delta** | **~1.3 GB** |

**Phase 2 total: ~3.5 GB** — still fits on e2-medium but leaves only ~500 MB headroom. Any memory spike (upload, large context window) will OOM. **Upgrade to e2-standard-2 recommended at Phase 2 start.**

**Phase 3 additions (Neo4j + Langfuse + Memori + MinIO):**

| Addition | RAM |
|---------|-----|
| Neo4j Community (heap + page cache, tuned low) | 1 GB |
| Langfuse web + worker | 1 GB |
| ClickHouse (Langfuse dependency) | 1.5 GB |
| Redis 7 (Langfuse dependency) | 100 MB |
| MinIO (Chainguard) | 300 MB |
| Memori extraction service | 700 MB |
| **Phase 3 delta** | **~4.6 GB** |

**Phase 3 total: ~8.1 GB** — exceeds e2-standard-2 (8 GB). **Phase 3 requires e2-standard-2 at minimum; e2-highmem-2 (16 GB, ~100€/month) or separating Langfuse onto its own VM is the right sizing.**

### Recommended VM Strategy

| Phase | VM | RAM | Est. Cost |
|-------|----|-----|-----------|
| Phase 1 | e2-medium | 4 GB | ~25€/mo |
| Phase 2 | e2-standard-2 | 8 GB | ~49€/mo |
| Phase 3 option A | e2-standard-4 | 16 GB | ~98€/mo |
| Phase 3 option B | e2-standard-2 (xbrain) + e2-small (Langfuse only) | 8 + 2 GB | ~62€/mo |

**Option B** (separate VM for Langfuse) is the recommended Phase 3 architecture. Langfuse's ClickHouse + Redis + MinIO are entirely independent of xbrain's stack — they only need the `LANGFUSE_SECRET_KEY` and `LANGFUSE_PUBLIC_KEY` to receive traces from the agent runtime.

---

## Inter-Component Integration Map

```
LibreChat ──────────────────────────────────────► memory-api (REST/MCP)
Open WebUI ─────────────────────────────────────► memory-api (REST/MCP)
ChatGPT (API) ──────────────────────────────────► memory-api (REST/MCP)
Claude Code ─────────────────────────────────────► memory-api (MCP)

memory-api ──────────────────────────────────────► PostgreSQL (sqlalchemy/asyncpg)
memory-api ──────────────────────────────────────► Qdrant (qdrant-client 1.17.1)
memory-api ──────────────────────────────────────► Remembra (HTTP REST)
memory-api ──────────────────────────────────────► mem0 (mem0ai SDK)
memory-api ──────────────────────────────────────► Neo4j (neo4j driver 6.1)
memory-api ──────────────────────────────────────► MinIO (boto3 / S3 API)

agent-runtime ───────────────────────────────────► memory-api (REST)
agent-runtime (LangGraph) ───────────────────────► Langfuse (langfuse 4.5.1 CallbackHandler)
agent-runtime ───────────────────────────────────► Memori (memorisdk 3.3.2 — extraction)

Remembra ────────────────────────────────────────► Qdrant (shared instance)
```

### External API Keys

| Key | Service | Where Configured |
|-----|---------|-----------------|
| `ANTHROPIC_API_KEY` | Claude models | LibreChat `.env`, `memory-api` env, `agent-runtime` env |
| `OPENAI_API_KEY` | GPT models | LibreChat `.env`, `memory-api` env |
| `XAI_API_KEY` | Grok (xAI) | LibreChat `librechat.yaml` → xAI endpoint config |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth + Drive sync | `memory-api` env, `drive-sync` service env |
| `LANGFUSE_SECRET_KEY` | Langfuse server auth | `agent-runtime` env, `memory-api` env |
| `LANGFUSE_PUBLIC_KEY` | Langfuse server auth | same |

---

## Alternatives Considered

| Recommended | Alternative | Why Not |
|-------------|-------------|---------|
| mem0 (Memstate replacement) | scream4ik/MemState | 12 stars, beta, no Docker image — too immature for Phase 2 |
| mem0 (Memstate replacement) | Zep/Graphiti | Apache 2.0, self-hostable — viable alternative; more graph-native; evaluate alongside mem0 in POC |
| cgr.dev/chainguard/minio | Official minio/minio | Official Docker Hub images discontinued October 2025 |
| cgr.dev/chainguard/minio | bitnami/minio | Bitnami adds complexity; Chainguard is simpler and already used by Langfuse |
| LangGraph (MIT library) | LangGraph Platform | Proprietary cloud — violates OSS constraint |
| Langfuse (MIT) | LangSmith | Proprietary, enterprise license required for self-hosting |
| PostgreSQL 17 | PostgreSQL 18 | 18.3 available but 17.x is the current LTS; prefer stability over cutting-edge for event store |
| Neo4j Community | Apache AGE (PostgreSQL extension) | AGE is simpler but far less capable for complex graph queries; Neo4j Community is the right call if graph is a first-class citizen |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Memstate.ai | Cloud-only SaaS, not self-hostable — violates OSS constraint | mem0 open source (Apache 2.0) |
| LangGraph Platform / LangSmith Deployment | Proprietary, Enterprise license required | LangGraph (MIT library) + Langfuse (MIT) |
| Official minio/minio Docker image | Discontinued on Docker Hub October 2025 | cgr.dev/chainguard/minio |
| Open WebUI Terminals feature | Requires Enterprise License for true container-level isolation | Logical isolation via RBAC + team_scope tagging |
| Pinecone, OpenAI Assistants API, Notion API | Proprietary/cloud-only — explicitly out of scope | Qdrant + PostgreSQL + memory-api |

---

## Critical Pre-Phase-2 POC Requirement

Before committing the memory layer to code, run a 1-day POC covering:
1. **Remembra v0.13.2** — can it be configured to point at xbrain's shared Qdrant instead of its bundled one? Does `team_scope` filtering work via payload filters?
2. **mem0 vs Zep/Graphiti** — which handles fact versioning + conflict detection better for xbrain's truth-level workflow? Test both with 200 conflicting facts.
3. **Memori v3.3.2 extraction** — does the Alpha SDK work reliably for entity + task extraction from chat messages? What's the actual RAM footprint with `sentence-transformers` loaded?

These three questions are the highest-risk items in the entire stack. Everything else has been production-verified.

---

## Sources

- LibreChat releases and changelog: https://www.librechat.ai/changelog — HIGH confidence
- LibreChat Docker Hub: https://hub.docker.com/r/librechat/librechat — HIGH confidence
- Open WebUI releases: https://github.com/open-webui/open-webui/releases — HIGH confidence
- Open WebUI license: https://docs.openwebui.com/license/ — HIGH confidence
- Qdrant Docker Hub tags: https://hub.docker.com/r/qdrant/qdrant/tags — HIGH confidence
- Qdrant memory consumption: https://qdrant.tech/articles/memory-consumption/ — HIGH confidence
- Neo4j Docker Hub: https://hub.docker.com/_/neo4j/tags — HIGH confidence
- Neo4j licensing: https://neo4j.com/open-core-and-neo4j/ — HIGH confidence
- Langfuse Docker Hub: https://hub.docker.com/r/langfuse/langfuse/tags — HIGH confidence
- Langfuse docker-compose.yml (live): https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml — HIGH confidence
- Langfuse open source announcement: https://langfuse.com/changelog/2025-06-04-open-sourcing-langfuse — HIGH confidence
- LangGraph PyPI: https://pypi.org/project/langgraph/ — HIGH confidence
- LangGraph MIT license: https://github.com/langchain-ai/langgraph/blob/main/LICENSE — HIGH confidence
- Remembra GitHub (live): https://github.com/remembra-ai/remembra — HIGH confidence
- Remembra Docker Hub: https://hub.docker.com/r/remembra/remembra/tags — HIGH confidence
- Remembra license: https://github.com/remembra-ai/remembra/blob/main/LICENSE — HIGH confidence
- Remembra docker-compose.yml (live): https://raw.githubusercontent.com/remembra-ai/remembra/main/docker-compose.yml — HIGH confidence
- Memstate.ai website: https://memstate.ai — HIGH confidence (confirmed cloud-only, not OSS)
- scream4ik/MemState GitHub: https://github.com/scream4ik/MemState — HIGH confidence
- Memori GitHub: https://github.com/MemoriLabs/Memori — HIGH confidence
- Memori pyproject.toml: https://github.com/MemoriLabs/Memori/blob/main/pyproject.toml — HIGH confidence
- MinIO Docker Hub discontinuation: https://www.minimus.io/post/minio-docker-image-changes-how-to-find-a-secure-minio-alternative — MEDIUM confidence (verified via Langfuse compose using Chainguard image)
- MinIO AGPLv3: https://www.min.io/blog/from-open-source-to-free-and-open-source-minio-is-now-fully-licensed-under-gnu-agplv3 — HIGH confidence
- GCP e2-medium specs: https://gcloud-compute.com/e2-medium.html — HIGH confidence
- GCP e2-standard-2 specs: https://cloudprice.net/gcp/compute/instances/e2-standard-2 — HIGH confidence
- mem0 open source: https://docs.mem0.ai/open-source/overview — MEDIUM confidence
- Langfuse ClickHouse sizing discussion: https://github.com/orgs/langfuse/discussions/5924 — HIGH confidence

---

*Stack research for: xbrain — Self-hostable collective AI memory platform*
*Researched: 2026-05-02*

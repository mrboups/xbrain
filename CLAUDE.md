# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Pre-implementation.** No source code yet. The repo currently contains only the GSD (Get Shit Done) toolchain under `.claude/`. The target architecture has been agreed but not scaffolded.

**Vision (one line):** xbrain is a collective persistent-memory system for humans + agents organised by team — not a chatbot workspace. Differentiator is the memory + truth-level + team-scope layer, not the frontend.

For the full target architecture (stack, phasing, repo layout, data tagging contract, truth levels), read `~/.claude/projects/D--VSC-xbrain/memory/project_xbrain_overview.md` before proposing changes. Once `/gsd:new-project` has been run, `.planning/PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, and `STATE.md` become the authoritative sources — prefer those over this file or memory when they exist.

## Workflow: GSD is the build system

Development flows through GSD slash commands, not ad-hoc edits. The hooks in `.claude/settings.json` (workflow guard, phase boundary, commit validation, read-before-edit guard) enforce this — they fire on `Write`/`Edit`/`Bash` tool calls. Bypassing them defeats the point.

Entry points (run in order on a fresh project):

1. `/gsd:new-project` — initialises `.planning/` (PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md). Use `--auto @docs/idea.md` to skip the wizard when the brief is already written.
2. `/gsd:plan-phase <N>` — produces a sub-plan for phase N before any code is touched.
3. `/gsd:execute-phase <N>` — runs the sub-plan with verification gates and atomic commits per task.

`/gsd:help` lists the full 65-command surface. Common adjuncts: `/gsd:resume-work`, `/gsd:progress`, `/gsd:debug`, `/gsd:review`, `/gsd:undo`.

The hooks only activate at session start — after editing `.claude/settings.json` or installing new hooks, the Claude Code session must be reopened for changes to take effect.

## Constraints carried over from kickoff

- **Open-source + self-hostable only.** Deployment target is GCP VM Ubuntu 24.04 (e2-medium baseline, ~25€/mo) or Railway, via Docker Compose. No managed-cloud-only services.
- **Multi-frontend assumed** — LibreChat, Open WebUI, ChatGPT (via API), and Grok all read/write the same memory layer. Logic that locks data to one frontend is wrong by construction.
- **Every data point carries the tagging contract** — `team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level` (EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC), `source`, `validation_status`. New schemas without these fields should be flagged.
- **Don't start coding on architecture/scope messages.** Confirm understanding and wait for explicit go-ahead before scaffolding.

## Commands

There are no build, lint, or test commands yet — nothing to build. Once Phase 1 lands (LibreChat + Open WebUI + PostgreSQL + Qdrant on Docker Compose), this section should be replaced with the real `docker compose` / migration / test commands. Do not invent placeholder ones.

## Language

- **Conversation with the user:** French. Reply in French unless the content is purely technical or they switch to English.
- **Product / app / code:** English **only**. All user-facing UI strings, button labels, popup copy, error messages, logs, comments, identifiers, and documentation in the product itself MUST be in English — including the Chrome extension, LibreChat custom labels, memory-api responses, MCP tool descriptions, etc. The extension popup currently has French strings (legacy) — migrate to English on next touch.
- **Planning artifacts (`.planning/`):** English. They are shared with subagents and other tools that assume English.

<!-- GSD:project-start source:PROJECT.md -->
## Project

**xbrain — AI Cognitive OS**

Plateforme open-source de **mémoire collective persistante** pour humains + agents, organisée par équipe et par projet. Toute donnée (chats, faits extraits, documents, sorties d'outils internes) traverse une couche unique — `memory-api` — qui applique un contrat de tagging strict : team-scope, truth-level, provenance, validation. Frontends multiples (LibreChat / Open WebUI / ChatGPT API / Claude Code) et agents LangGraph lisent et écrivent **la même mémoire**.

Ce n'est pas un workspace de chatbot. Le différenciateur est la couche mémoire + truth-level + team-scope, pas l'interface.

**Core Value:** **Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.** Si tout le reste plante, ce contrat doit tenir.

### Constraints

- **Tech stack** (révisée après research) : LibreChat + Open WebUI + **mem0** + LangGraph + Qdrant + Neo4j + PostgreSQL + MinIO (image Chainguard) + Langfuse — **Pourquoi :** stack 100 % OSS auto-hébergeable. Memstate.ai (SaaS fermé), Remembra (13★ + SQLite, immature) et Memori (Alpha) ont été retirés au profit de mem0 + memory-api natif après vérification : voir Key Decisions ci-dessous.
- **Déploiement** : GCP VM Ubuntu 24.04, Docker Compose — **Pourquoi :** budget contraint, ops simple, pas d'expertise Kubernetes requise. Stratégie de sizing échelonnée :
  - **Phase 1** : `e2-medium` (4 GB, ~25€/mo) — LibreChat + Open WebUI + Postgres + Qdrant + memory-api stub. Tolérance fine — surveiller OOM, pas de service ajouté en plus sans couper autre chose.
  - **Phase 2** : upgrade vers `e2-standard-2` (8 GB, ~38-49€/mo) en début de phase, **avant** d'ajouter mem0 + LangGraph + agent runtime.
  - **Phase 3** : `e2-standard-4` (16 GB, ~98€/mo) **OU** Langfuse sur VM séparée (~62€/mo total) — décision en début de Phase 3 selon charge observée.
  - GCP project cible : compte `team@example.com`, projet à créer (`xbrain-prod` proposé) sans toucher aux projets existants.
- **Open-source uniquement** : aucun service managé propriétaire dans le chemin critique — **Pourquoi :** auto-hébergeable, pas de lock-in, contrôle complet de la donnée (sensibilité multi-team).
- **Multi-frontend invariant** : LibreChat + Open WebUI + ChatGPT (API) + Claude Code lisent/écrivent la même mémoire — **Pourquoi :** l'équipe utilise déjà ces outils en pratique. Imposer un frontend unique ferait échouer l'adoption.
- **Contrat de tagging obligatoire** : 7 champs minimum sur chaque donnée — **Pourquoi :** invariant qui rend possibles l'isolation team, la promotion truth-level, l'audit, le retrieval scopé. C'est le différenciateur.
- **Multi-modèle** : Claude (coding/archi), GPT (reasoning/summary), Grok (second avis) — **Pourquoi :** chaque modèle a un rôle distinct. La plateforme doit pouvoir en ajouter (futur Mistral, Gemini, etc.) sans refactor.
- **Performance** : pas de SLA strict en v1, mais l'expérience LibreChat doit rester fluide (< 2s pour une réponse simple, retrieval mémoire < 500ms en P95) — **Pourquoi :** UX d'équipe.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Executive Summary
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
#### Open WebUI (Admin / RAG / Agent Testing Frontend)
| Property | Value |
|----------|-------|
| Version | v0.9.0 |
| Docker image | `ghcr.io/open-webui/open-webui:v0.9.0` |
| License | Custom "Open WebUI License" (non-OSI since v0.6.6, April 2025) — branding must be retained for deployments >50 users in 30 days |
| RAM footprint | ~500 MB–1 GB idle |
| Multi-tenant / teams | Basic RBAC (admin/user roles); per-user workspace isolation; true container-level isolation requires Enterprise License |
| Why | Best admin/RAG/agent-testing UI, light footprint, widely supported |
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
#### Memstate — BLOCKED: NOT open source
- GitHub: https://github.com/scream4ik/MemState
- Version: v0.5.1 (Dec 2025), 12 stars, 49 commits
- Provides ACID-like transactional memory layer keeping SQL + vector DB in sync, with rollback(n) time travel
- **Risk:** Very low community adoption, beta-quality, no Docker image published — must build from source
- **Verdict:** Usable for POC but not for production Phase 2
- GitHub: https://github.com/mem0ai/mem0 — 40k+ stars, actively maintained
- Self-hosted via Docker (PostgreSQL + pgvector + Neo4j)
- Versioned fact storage since v1.0.4 (Feb 2026) with timestamp parameter
- Docker: `pip install mem0ai` + FastAPI server
- **Verdict:** Best drop-in replacement for the "versioning + conflict handling" role Memstate was supposed to fill. Production-ready, widely adopted, OSS.
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
## VM Sizing Assessment
### e2-medium (2 vCPU, 4 GB RAM, ~25€/month)
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
| Addition | RAM |
|---------|-----|
| Remembra | 400 MB |
| Open WebUI | 600 MB |
| Agent runtime container | 300 MB |
| **Phase 2 delta** | **~1.3 GB** |
| Addition | RAM |
|---------|-----|
| Neo4j Community (heap + page cache, tuned low) | 1 GB |
| Langfuse web + worker | 1 GB |
| ClickHouse (Langfuse dependency) | 1.5 GB |
| Redis 7 (Langfuse dependency) | 100 MB |
| MinIO (Chainguard) | 300 MB |
| Memori extraction service | 700 MB |
| **Phase 3 delta** | **~4.6 GB** |
### Recommended VM Strategy
| Phase | VM | RAM | Est. Cost |
|-------|----|-----|-----------|
| Phase 1 | e2-medium | 4 GB | ~25€/mo |
| Phase 2 | e2-standard-2 | 8 GB | ~49€/mo |
| Phase 3 option A | e2-standard-4 | 16 GB | ~98€/mo |
| Phase 3 option B | e2-standard-2 (xbrain) + e2-small (Langfuse only) | 8 + 2 GB | ~62€/mo |
## Inter-Component Integration Map
### External API Keys
| Key | Service | Where Configured |
|-----|---------|-----------------|
| `ANTHROPIC_API_KEY` | Claude models | LibreChat `.env`, `memory-api` env, `agent-runtime` env |
| `OPENAI_API_KEY` | GPT models | LibreChat `.env`, `memory-api` env |
| `XAI_API_KEY` | Grok (xAI) | LibreChat `librechat.yaml` → xAI endpoint config |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth + Drive sync | `memory-api` env, `drive-sync` service env |
| `LANGFUSE_SECRET_KEY` | Langfuse server auth | `agent-runtime` env, `memory-api` env |
| `LANGFUSE_PUBLIC_KEY` | Langfuse server auth | same |
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
## Critical Pre-Phase-2 POC Requirement
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
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

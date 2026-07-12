# Requirements: xbrain — AI Cognitive OS

**Defined:** 2026-05-02
**Core Value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.

**Note de lecture :** les requirements parlent de **capacités**, pas de frameworks. Les choix techniques (mem0, LibreChat, Qdrant, Neo4j, etc.) sont dans `PROJECT.md` et `research/STACK.md` et peuvent évoluer sans changer les REQ-IDs.

## Milestone v2.0 Requirements — Open-Core Edition

Active scope. One codebase, two runtime shapes (OSS self-host / SaaS hosted); a single update flows to both — never a fork. Design source: `.planning/features/open-core-edition-design.md`.

> **⚠️ Model shift locked 2026-07-11** (see design doc "Locked Decisions"): the "paid self-host pro" edition is **dropped**. It is now **OSS-everything + monetize-hosted**: no product feature is paywalled; only the hosted control plane (billing/multi-tenant) stays closed. Code license = **AGPLv3 + CLA**. Consequences below: `EDIT-03` (Ed25519 license) is **DROPPED**; new requirements needed for **local email/password auth** and **local embeddings**; the OSS frontend is the **web group-chat** (not LibreChat). REQUIREMENTS + ROADMAP to be re-synced for phases 15–16; Phase 14 (Portability) is unaffected in intent and expands to a full cleanup.

### Portability (config-driven, not hardcoded)

- [ ] **PORT-01**: An operator can point the entire stack at their own domain and keys via config alone — no `grooveos.app`, `aibrussels`, or hardcoded `default` team_scope remains in source
- [ ] **PORT-02**: An operator can configure a fresh install from a slim, documented OSS `.env.example` without reading source code

### Edition Mechanics

- [ ] **EDIT-01**: An operator selects which services run via `COMPOSE_PROFILES` — untagged services are the OSS-light core; `integrations` / `pro` / `saas` / `ops` are opt-in
- [ ] **EDIT-02**: The same memory-api image serves every edition — an `EDITION` flag (oss|saas|pro) gates SaaS/pro-only routers while brain, chat, retrieval, truth-levels and the ChatGPT-web connector stay always mounted
- [~] **EDIT-03**: ~~A paying customer unlocks the `pro` profile by installing a signed Ed25519 license verified offline~~ — **DROPPED 2026-07-11** (no paid product tier; monetize hosted only)

### OSS Packaging

- [ ] **PKG-01**: A team can stand up the OSS-light edition (chat + full brain: doc analysis, ingest, retrieval, truth-levels, ChatGPT connector, clip) on a fresh VM from the install docs alone
- [ ] **PKG-02**: A user can chat and query their team brain from a standalone hosted web app, without installing a browser extension

### Release / CI Lockstep

- [ ] **REL-01**: A single CI run per commit builds images once and tests both the OSS subset and the full profile before any release
- [ ] **REL-02**: One commit produces both the published OSS release (tagged images + light compose + install docs) and the deployed SaaS full profile
- [ ] **REL-03**: An operator upgrades a running self-host install through a forward-only, edition-agnostic migration path

**Out of scope for v2.0 (separate tracks):** Email feature (send + Gmail read/search/ingest — absent today); Grok API-key fallback + per-message trial cap (SaaS trial).


## v2.0 Traceability

Mapping requirement -> phase for milestone v2.0 "Open-Core Edition". Filled by the roadmapper — 2026-07-11.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PORT-01 | Phase 14 | Pending |
| PORT-02 | Phase 14 | Pending |
| EDIT-01 | Phase 15 | Pending |
| EDIT-02 | Phase 15 | Pending |
| EDIT-03 | Phase 15 | Pending |
| PKG-01 | Phase 16 | Pending |
| PKG-02 | Phase 16 | Pending |
| REL-01 | Phase 17 | Pending |
| REL-02 | Phase 17 | Pending |
| REL-03 | Phase 17 | Pending |

**Coverage:**
- v2.0 requirements: 10 total (PORT x2, EDIT x3, PKG x2, REL x3)
- Mapped to phases: 10/10
- Phase 14 (Portability Foundation): 2 requirements
- Phase 15 (Edition Mechanics): 3 requirements
- Phase 16 (OSS Light Packaging): 2 requirements
- Phase 17 (CI Lockstep): 3 requirements
- Unmapped: 0
- Out of scope for v2.0 (separate tracks, not phase-mapped): Email feature; Grok API-key fallback + per-message trial cap

---

## v1 Requirements

Périmètre du milestone d'initialisation (Phases 1 + 2 + 3 de la roadmap). Tout ce qui doit exister pour que xbrain soit une plateforme utilisable et différenciante de bout en bout.

### Authentication & Identity

- [x] **AUTH-01**: User can sign in with Google SSO via the team frontend (LibreChat)
- [x] **AUTH-02**: User can sign in with email/password as fallback when Google SSO is unavailable
- [x] **AUTH-03**: Service clients (agents, MCP servers) can authenticate with short-lived JWT/API tokens issued by `memory-api`
- [x] **AUTH-04**: User session persists across browser refresh and is invalidated on logout
- [x] **AUTH-05**: Service-account tokens carry an explicit `team_scope` and act under that scope only
- [x] **AUTH-06**: Every API call (human or service) is traceable to its identity (user id or service-account id) in the audit log

### Team & Permissions

- [x] **TEAM-01**: Org admin can create teams and invite members with role assignment (Admin / Member / Viewer)
- [x] **TEAM-02**: Every persisted datum is bound to a single `team_scope` and is invisible to other teams by default
- [x] **TEAM-03**: A team can have multiple projects; each datum carries a `project_scope` within its team
- [x] **TEAM-04**: RBAC is enforced at `memory-api` layer (not in frontends) for read, write, and promotion endpoints
- [x] **TEAM-05**: Cross-team visibility is impossible except for items explicitly promoted to `PUBLIC` (cf. `TRUTH-*`)
- [x] **TEAM-06**: Org admin panel allows managing teams, users, roles, and quotas

### Memory & Tagging

- [x] **MEM-01**: Every datum written to the system carries the 7-field tagging contract (`team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`); writes missing any field are rejected at `memory-api` layer
- [x] **MEM-02**: New data is born at `truth_level=EPHEMERAL` by default
- [x] **MEM-03**: Every memory item has provenance metadata: who created it (user/service), from which frontend or tool, on which conversation/event
- [x] **MEM-04**: Conversations from any frontend (LibreChat, Open WebUI) are persisted via `memory-api` and indexed for retrieval
- [x] **MEM-05**: Documents and assets (PDFs, images, decks, datasets) are stored in object storage and indexed with full tagging contract
- [x] **MEM-06**: Memory items support entity-aware long-term storage (entities surface across conversations and projects within a team)
- [x] **MEM-07**: Memory items support fact versioning — updating a fact creates a new version with diff retained, never a silent overwrite
- [x] **MEM-08**: Conflict detection surfaces when a `WORKING` fact contradicts an existing `VALIDATED` or `CANONICAL` fact in the same scope
- [x] **MEM-09**: Automatic structured extraction can convert raw text (conversations, documents) into structured facts/tasks/entities with provenance back to the source
- [x] **MEM-10**: Temporal queries are supported — "what did the team believe about X as of date Y?" returns the truth state at that point

### Frontends & Chat

- [x] **CHAT-01**: User can chat with multiple LLMs (Claude, GPT, Grok minimum) from a single team frontend (LibreChat)
- [x] **CHAT-02**: New LLM providers can be added by org admin via configuration without code change
- [x] **CHAT-03**: Conversation history persists per user and is queryable as team memory
- [x] **CHAT-04**: User can upload files (PDF, image, dataset) into a conversation and ask questions over them (basic RAG)
- [x] **CHAT-05**: Open WebUI is available as a separate admin/tooling workspace (RAG config, agent tests, monitoring)
- [x] **CHAT-06**: User can request a parallel "second opinion" from a different model on the same prompt (e.g., ask Claude, get Grok's parallel critique)
- [x] **CHAT-07**: Chat replies are auto-enriched with relevant `CANONICAL` facts from the user's team/project memory before the LLM call
- [x] **CHAT-08**: Members who use ChatGPT externally can read/write team memory via a `memory-api` OpenAI-compatible endpoint

### Search & RAG

- [x] **SRCH-01**: Semantic search over team memory returns results ranked by relevance, scoped to caller's team and visible truth levels by default
- [x] **SRCH-02**: Hybrid search combines semantic (vector) and keyword (BM25) modalities for exact-term recall
- [x] **SRCH-03**: User can filter search results by `truth_level` (e.g., "only `VALIDATED` or above")
- [x] **SRCH-04**: User can search across all projects within their team explicitly (cross-project within team)
- [x] **SRCH-05**: Graph-traversal queries are available (e.g., "what depends on entity X?", "show lineage of fact Y")

### Truth-Level Workflow

- [x] **TRUTH-01**: A truth-level state machine enforces the progression `EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC` with no skipping; demotions are also tracked
- [x] **TRUTH-02**: Member can request promotion of a fact; admin or designated reviewer approves to advance the level
- [x] **TRUTH-03**: Every promotion event is recorded in an append-only audit log: who proposed, who approved, when, with what evidence
- [x] **TRUTH-04**: Visual indicator on memory items shows current `truth_level` in any frontend that surfaces them
- [x] **TRUTH-05**: Conflict-aware promotion: attempting to promote a fact that contradicts a higher-level fact in scope surfaces a warning and requires explicit override
- [x] **TRUTH-06**: Items at `PUBLIC` level are readable across all teams in the org; items at `CANONICAL` and below are not
- [x] **TRUTH-07**: Demotion (e.g., `CANONICAL → VALIDATED`) is supported with a recorded reason
- [x] **TRUTH-08**: Bulk imports land at `EPHEMERAL` or `WORKING` only; no shortcut to `VALIDATED+` via import
- [x] **TRUTH-09**: Agents can propose promotions but cannot autonomously promote to `CANONICAL` or `PUBLIC` — those require human approval

### Agents & Runtime

- [x] **AGENT-01**: Agents execute on a persistent runtime (LangGraph) with checkpointed state and crash recovery
- [x] **AGENT-02**: Agents read and write memory exclusively through `memory-api` endpoints (never direct DB access)
- [x] **AGENT-03**: Agents can be paused for human-in-the-loop approval at configured workflow steps
- [x] **AGENT-04**: Agents handle LLM failures, tool timeouts, and retries gracefully without leaking partial state
- [x] **AGENT-05**: Background ingestion agent processes new documents (uploads, Drive sync) into structured memory automatically
- [x] **AGENT-06**: Each agent has its own working memory namespace and reads from team shared memory with team_scope filter
- [x] **AGENT-07**: Multi-agent orchestration is supported (supervisor delegates to specialized agents) with isolated task contexts

### Internal Tools (MCP / API)

- [x] **MCP-01**: An MCP gateway routes tool calls from any frontend or agent to registered services
- [x] **MCP-02**: A new internal tool can be added by registering an MCP server URL with the gateway — no core changes required
- [x] **MCP-03**: Every tool invocation includes the caller's `team_scope` and `user_id`, enforced by the gateway
- [x] **MCP-04**: Tool outputs are written to `memory-api` with the full tagging contract (no direct DB writes from tools)
- [x] **MCP-05**: Scraper service is available as the first MCP tool, demonstrating the integration pattern end-to-end
- [x] **MCP-06**: Calendar service is available as an MCP tool (team events queryable from chat and agents)
- [x] **MCP-07**: Pitch deck editor service is available as an MCP tool (decks editable, stored in object storage, indexed in memory)

### Integrations (External Sources)

- [x] **INT-01**: Google Drive folders can be synced (read) into team memory; documents are indexed with full tagging contract
- [x] **INT-02**: Drive sync is incremental — only changed files are re-processed
- [x] **INT-03**: Drive folders can be mapped to specific team/project scopes (no cross-team bleed via Drive)
- [x] **INT-04**: Agent-produced summaries can be written back to Drive documents (write-back loop) with explicit user opt-in

### Observability

- [x] **OBS-01**: Every LLM call is traced with model, latency, token count, cost, and prompt version (Langfuse)
- [x] **OBS-02**: Every agent workflow execution produces an end-to-end trace (request → plan → tool calls → memory writes → response)
- [x] **OBS-03**: Operators receive alerts on agent crashes, tool timeouts, and RAG retrieval failures
- [x] **OBS-04**: Every memory item is traceable to its origin: `memory-api` endpoint → conversation/tool call → identity → trace_id
- [x] **OBS-05**: Admin sees a per-team cost dashboard (token spend, model breakdown, agent-vs-human contribution)

### Admin & Config

- [x] **ADMIN-01**: The full stack (frontends, memory-api, agent runtime, DBs, observability, object storage) deploys via a single `docker compose up` from the repo
- [x] **ADMIN-02**: All secrets (API keys, DB passwords, OAuth secrets) are sourced from environment variables, with `.env.example` template tracked in git but real `.env` excluded
- [x] **ADMIN-03**: Each service exposes a healthcheck endpoint and Docker Compose is configured to wait for healthy upstreams
- [x] **ADMIN-04**: Schema migrations are versioned (Alembic or equivalent) — no in-place destructive schema changes
- [x] **ADMIN-05**: A documented backup procedure covers PostgreSQL, Qdrant, Neo4j, and object storage; a restore drill is performed and passes
- [x] **ADMIN-06**: Admin can configure per-team rate limits / quotas (token budget, write rate) without restarting services

## v2 Requirements

Reconnu utile, repoussé après le milestone d'initialisation.

### Authentication & Identity

- **AUTH-V2-01**: LDAP / Active Directory integration for orgs that don't use Google Workspace
- **AUTH-V2-02**: Per-token rate limiting and revocation UI

### Team & Permissions

- **TEAM-V2-01**: Self-service team creation by members (with admin approval)
- **TEAM-V2-02**: Guest accounts for external clients (cross-org sharing v2)

### Memory & Tagging

- **MEM-V2-01**: Schema evolution UI (add custom fields per team without code change)
- **MEM-V2-02**: Memory deduplication agent (merge duplicate facts into one with provenance preserved)

### Search & RAG

- **SRCH-V2-01**: Saved searches and named filters per user
- **SRCH-V2-02**: Federated search across selected external sources (e.g., GitHub, Linear) live, without prior ingestion

### Truth-Level Workflow

- **TRUTH-V2-01**: Reviewer rotation / multi-reviewer approval policies
- **TRUTH-V2-02**: Custom truth-level extensions per team (some teams may want to insert intermediate levels)

### Agents & Runtime

- **AGENT-V2-01**: Agent marketplace UI (browse, install, configure agents)
- **AGENT-V2-02**: Agent versioning and A/B testing
- **AGENT-V2-03**: Long-running agent jobs with progress reporting (hours/days)

### Internal Tools (MCP / API)

- **MCP-V2-01**: Public MCP server registry / discovery beyond the org
- **MCP-V2-02**: Tool execution sandboxing (untrusted MCP servers in isolated containers)

### Integrations

- **INT-V2-01**: Notion connector
- **INT-V2-02**: Slack connector (sync messages flagged for indexing)
- **INT-V2-03**: Linear / Jira connector
- **INT-V2-04**: Gmail connector
- **INT-V2-05**: GitHub connector

### Observability

- **OBS-V2-01**: Prompt versioning and A/B experiment tracking
- **OBS-V2-02**: Detailed per-user activity dashboard

### Admin & Config

- **ADMIN-V2-01**: Kubernetes deployment manifests for orgs that outgrow single VM
- **ADMIN-V2-02**: SSO via SAML for enterprise orgs
- **ADMIN-V2-03**: Multi-region deployment for compliance / latency

## Out of Scope

Exclusions explicites — listées pour empêcher leur réintroduction sans débat.

| Feature | Reason |
|---------|--------|
| Mobile-first / native app | LibreChat est responsive et suffisant pour l'usage interne. Pas de frontend custom à maintenir. |
| Custom-built chat frontend | LibreChat + Open WebUI couvrent tous les personas et sont activement maintenus en OSS. |
| SaaS multi-tenant pour clients externes (v1) | Périmètre v1 = interne org seulement. Multi-tenant cross-org peut venir en v2 (cf. TEAM-V2-02). |
| Schémas sans le contrat de tagging complet (7 champs) | Viole l'invariant fondateur. Toute donnée doit traverser `memory-api` qui rejette les writes incomplets. |
| Logique métier ou stockage enfermé dans un frontend | Viole l'invariant multi-frontend. Toute capacité passe par `memory-api`. |
| Services managés cloud-only (Pinecone, OpenAI Assistants persistance, Notion comme source de vérité) | Verrouille le déploiement, contredit la contrainte OSS / auto-hébergeable. |
| Single-model lock (Claude-only ou GPT-only) | Multi-modèle est une contrainte dure depuis le début. |
| Promotion autonome agent → `CANONICAL` ou `PUBLIC` | Effondre la confiance dans les niveaux supérieurs. Agents s'arrêtent à `VALIDATED`. |
| Recherche cross-team par défaut | Violation de l'isolation. Cross-team uniquement via items `PUBLIC` explicites. |
| Bulk import contournant le workflow de promotion | Imports atterrissent à `EPHEMERAL` ou `WORKING` ; promotion explicite requise. |
| Real-time collaborative editing dans le chat | Complexité élevée, ROI faible pour une plateforme mémoire. Collaboration async via promotion. Google Drive couvre l'édition collaborative live. |
| Kubernetes (v1) | Ops disproportionnée vs taille équipe. e2-medium → e2-standard-2 → e2-standard-4 + Docker Compose suffit. K8s reportée à v2 (cf. ADMIN-V2-01). |
| Memstate.ai / Remembra / Memori comme dépendances directes | Memstate = SaaS fermé (pas d'OSS officiel). Remembra v0.13.2 = 13★ + SQLite, immature. Memori = self-déclaré Alpha. Remplacés par mem0 (40k★, Apache 2.0) + memory-api natif (state machine truth-level). Voir `PROJECT.md` Key Decisions. |

## Traceability

Mapping requirement → phase. Rempli par le roadmapper — 2026-05-02.

| Requirement | Phase | Status |
|-------------|-------|--------|
| AUTH-01 | Phase 1 | Done (Phase 1) |
| AUTH-02 | Phase 1 | Done (Phase 1) |
| AUTH-03 | Phase 1 | Done (Phase 1) |
| AUTH-04 | Phase 1 | Done (Phase 1) |
| AUTH-05 | Phase 1 | Done (Phase 1) |
| AUTH-06 | Phase 1 | Done (Phase 1) |
| TEAM-01 | Phase 1 | Done (Phase 1) |
| TEAM-02 | Phase 1 | Done (Phase 1) |
| TEAM-03 | Phase 1 | Done (Phase 1) |
| TEAM-04 | Phase 1 | Done (Phase 1) |
| TEAM-05 | Phase 1 | Done (Phase 1) |
| TEAM-06 | Phase 1 | Done (Phase 1) |
| MEM-01 | Phase 1 | Done (Phase 1) |
| MEM-02 | Phase 1 | Done (Phase 1) |
| MEM-03 | Phase 1 | Done (Phase 1) |
| MEM-04 | Phase 1 (closed Phase 13) | Done (Phase 13) |
| MEM-05 | Phase 1 | Done (Phase 1) |
| MEM-06 | Phase 2 | Done (Phase 2) |
| MEM-07 | Phase 2 | Done (Phase 2) |
| MEM-08 | Phase 2 | Done (Phase 2) |
| MEM-09 | Phase 2 | Done (Phase 2) |
| MEM-10 | Phase 2 | Done (Phase 2) |
| CHAT-01 | Phase 1 | Done (Phase 1) |
| CHAT-02 | Phase 1 | Done (Phase 1) |
| CHAT-03 | Phase 1 (closed Phase 13) | Done (Phase 13) |
| CHAT-04 | Phase 1 | Done (Phase 1) |
| CHAT-05 | Phase 1 | Done (Phase 1) |
| CHAT-06 | Phase 2 | Done (Phase 2) |
| CHAT-07 | Phase 2 (closed Phase 13) | Done (Phase 13) |
| CHAT-08 | Phase 1 | Done (Phase 1) |
| SRCH-01 | Phase 1 | Done (Phase 1) |
| SRCH-02 | Phase 1 | Done (Phase 1) |
| SRCH-03 | Phase 2 | Done (Phase 2) |
| SRCH-04 | Phase 2 | Done (Phase 2) |
| SRCH-05 | Phase 3 | Done (Phase 3) |
| TRUTH-01 | Phase 2 | Done (Phase 2) |
| TRUTH-02 | Phase 2 | Done (Phase 2) |
| TRUTH-03 | Phase 2 | Done (Phase 2) |
| TRUTH-04 | Phase 2 | Done (Phase 2) |
| TRUTH-05 | Phase 2 | Done (Phase 2) |
| TRUTH-06 | Phase 2 | Done (Phase 2) |
| TRUTH-07 | Phase 2 | Done (Phase 2) |
| TRUTH-08 | Phase 2 | Done (Phase 2) |
| TRUTH-09 | Phase 2 | Done (Phase 2) |
| AGENT-01 | Phase 2 | Done (Phase 2) |
| AGENT-02 | Phase 2 | Done (Phase 2) |
| AGENT-03 | Phase 2 | Done (Phase 2) |
| AGENT-04 | Phase 2 | Done (Phase 2) |
| AGENT-05 | Phase 2 | Done (Phase 2) |
| AGENT-06 | Phase 2 | Done (Phase 2) |
| AGENT-07 | Phase 2 | Done (Phase 2) |
| MCP-01 | Phase 3 | Done (Phase 3) |
| MCP-02 | Phase 3 | Done (Phase 3) |
| MCP-03 | Phase 3 | Done (Phase 3) |
| MCP-04 | Phase 3 | Done (Phase 3) |
| MCP-05 | Phase 3 | Done (Phase 3) |
| MCP-06 | Phase 3 | Done (Phase 3) |
| MCP-07 | Phase 3 | Done (Phase 3) |
| INT-01 | Phase 3 | Done (Phase 3) |
| INT-02 | Phase 3 | Done (Phase 3) |
| INT-03 | Phase 3 | Done (Phase 3) |
| INT-04 | Phase 3 | Done (Phase 3) |
| OBS-01 | Phase 1 | Done (Phase 1) |
| OBS-02 | Phase 2 | Done (Phase 2) |
| OBS-03 | Phase 2 | Done (Phase 2) |
| OBS-04 | Phase 1 | Done (Phase 1) |
| OBS-05 | Phase 2 | Done (Phase 2) |
| ADMIN-01 | Phase 1 | Done (Phase 1) |
| ADMIN-02 | Phase 1 | Done (Phase 1) |
| ADMIN-03 | Phase 1 | Done (Phase 1) |
| ADMIN-04 | Phase 1 | Done (Phase 1) |
| ADMIN-05 | Phase 1 | Done (Phase 1) |
| ADMIN-06 | Phase 1 | Done (Phase 1) |

**Coverage:**
- v1 requirements: 73 total (note: REQUIREMENTS.md header stated 65 but 73 REQ-IDs are defined across 11 categories — all 73 mapped)
- Mapped to phases: 73/73
- Phase 1: 33 requirements
- Phase 2: 28 requirements
- Phase 3: 12 requirements
- Unmapped: 0

---

## Post-v1 capabilities (extended scope)

The following capabilities were added to Milestone v1.0 **after** the initial 73 REQ-IDs were frozen. They are tracked here for documentation but were not part of the original v1 scope contract — the v1 73-REQ contract above remains the authoritative baseline for what "xbrain v1" delivers. See `ROADMAP.md` for the full per-phase specification and `.planning/phases/*` for shipped artefacts.

### Phase 8 — Granola Per-User + Universal Extraction Pipeline + Platform Agents (LIVE 2026-05-09)

Phase 8 introduced per-user Granola integration, universal extraction across frontends, and an admin-editable agent registry. No formal `XX-NN` requirement labels were defined — phase shipped against 8 acceptance criteria (D1..D6 + verify-phase8.sh PASS 7/7). See `ROADMAP.md` Phase 8 section.

### Phase 9 — Session Bridge — Pro/Max Routing via Chrome Extension (LIVE 2026-05-12) — SESSION-01..06

- **SESSION-01**: Microservice `session-bridge` (port 8105) exposes OpenAI-compatible `/v1/chat/completions` HTTP endpoint + `/ws/{user_sub}` WebSocket pool per user.
- **SESSION-02**: Chrome extension v1.1.0+ maintains persistent WebSocket to `bridge.grooveos.app` with `chrome.alarms` watchdog, dispatches inbound chat requests to credentialed `fetch()` against claude.ai internal API.
- **SESSION-03**: Per-user session tracking in `user_external_sessions` table (last_seen_at, metadata JSONB); extension popup surfaces session status with logged claude.ai email.
- **SESSION-04**: Graceful fallback — requests without active extension session OR without claude.ai login return explicit error "Install xbrain extension and login to claude.ai", no silent fallback to team API key.
- **SESSION-05**: LibreChat config exposes "Claude (mon abonnement)" endpoint routing via `session-bridge`; Sonnet via routed chat consumes user's Pro/Max quota visible at claude.ai/settings/usage.
- **SESSION-06**: SSE event-style translation — claude.ai internal streaming format converted to OpenAI-compatible SSE so LibreChat consumes responses without patching.

ChatGPT Plus routing explicitly deferred (out of scope Phase 9 — possibly revisited post-milestone).

### Phase 10 — GitHub-Primary Auth + Org-Driven Team Membership (LIVE 2026-05-14) — GHA-01..08

- **GHA-01**: `POST /v1/auth/github/exchange` (and successor `POST /v1/auth/github/signin`) mints an `xbt_` token directly from GitHub OAuth code (no prior Google sign-in required).
- **GHA-02**: Auto-grant team membership at first GitHub sign-in via org match — if a `teams.github_org` matches any of the user's GitHub orgs (`/user/orgs`), an `INSERT team_members(role='member')` is inserted automatically.
- **GHA-03**: Admin block/unblock endpoint `POST /v1/teams/{id}/members/{user_id}/block` (sets `team_members.blocked_at`); blocked user receives 403 on team-scoped routes even if still org member.
- **GHA-04**: Pre-block via `POST /v1/teams/{id}/org-blocks {github_login}` for GitHub logins that have not yet signed in (table `team_org_blocks`).
- **GHA-05**: Email admins on auto-grant via `send_member_autojoined_email()` (fail-soft if `SMTP_HOST` empty).
- **GHA-06**: Auto-merge orphan user rows (Google ↔ GitHub) on link/sign-in — `team_members` migrated to primary row, orphan row soft-deleted via `users.merged_into_user_id`, idempotent on re-sign.
- **GHA-07**: GitHub primary sign-in button in `chrome-extension/popup.html` (visually dominant; Google sign-in secondary).
- **GHA-08**: GitHub primary sign-in button in `app-site/account/teams/index.html` (same dominance hierarchy).

### Phase 11 — Brain Monitor + Superadmin Dashboard (LIVE 2026-05-17) — BMO-01..12

- **BMO-01**: Migration 0017 adds `truth_level` (TEXT NOT NULL DEFAULT per entity type) + `deleted_at TIMESTAMPTZ NULL` + `deleted_by UUID NULL` columns to `tasks`, `contacts`, `team_messages`, `conversations`, `messages` (memory_items already had `truth_level` since Phase 2). Backfill defaults: tasks → WORKING, contacts → VALIDATED, conversations/messages/team_messages → EPHEMERAL.
- **BMO-02**: Migration 0018 creates universal event view `v_brain_events` as `UNION ALL` of 7 logical streams (memory_item, granola_note, conversation, message, team_message, task, contact) with normalized columns: `entity_type`, `entity_id`, `team_scope`, `truth_level`, `deleted_at`, `deleted_by`, `source`, `created_by`, `created_at`, `preview` (200-char truncate), `conversation_id`.
- **BMO-03**: `GET /v1/brain/events` paginated (cursor `created_at + id`) with filters `entity_type[]`, `truth_level[]`, `source[]`, `created_by`, `q` (text search on preview), `include_deleted`, `since` — team-scoped via `X-Team-Scope` header.
- **BMO-04**: `PATCH /v1/brain/events/{entity_type}/{entity_id}` sets `truth_level` — author can edit their own events, team admins can edit any (auth check via `created_by == principal.user.id` OR `team_members.role='admin'`).
- **BMO-05**: `DELETE /v1/brain/events/{entity_type}/{entity_id}` performs soft delete (`deleted_at=now() + deleted_by=principal.user.id`) — same permissions as BMO-04; triggers async Qdrant point delete for memory_items.
- **BMO-06**: `POST /v1/brain/events/{entity_type}/{entity_id}/restore` clears `deleted_at` (author or admin) — only succeeds if `deleted_at > now() - INTERVAL '30 days'`.
- **BMO-07**: Retrieval regression filter — all existing routes (memory search, tasks list, contacts list, conversations list, messages list, native_provider) MUST exclude `deleted_at IS NOT NULL` by default. Regression tests in each router confirm.
- **BMO-08**: Service `brain-janitor` cron container (daily 03:00 UTC) — for each entity with `deleted_at < now() - 30 days`: (a) Qdrant point delete if vector exists, (b) Neo4j relation cleanup if node exists, (c) Postgres hard DELETE. Idempotent + audit log entry per purge.
- **BMO-09**: app-site UI `/account/teams/brain/?team=<slug>` — virtualized table (1000+ rows), lateral filters (entity_type, truth_level, source, date range, deleted), preview row, inline truth_level dropdown (5 levels), Delete/Restore buttons, bulk select for admins, 30s polling.
- **BMO-10**: Superadmin auth helper `assert_is_superadmin(principal)` wrapping existing `_is_admin()` from `deps.py`; new endpoint family `/v1/admin/brain/...` gated by it. Cross-team drill-down endpoint `GET /v1/admin/brain/events?team_slug=X` bypasses `X-Team-Scope` for superadmins and writes audit_log entry per call (`action='superadmin_brain_access'`).
- **BMO-11**: Aggregate metrics endpoints (Pack M): `GET /v1/admin/brain/overview` (counts × truth_level × entity_type per team), `GET /v1/admin/brain/storage` (PG rows + Qdrant points + MinIO bytes per team), `GET /v1/admin/brain/activity?days=30` (events/day per team), `GET /v1/admin/brain/sources?days=30` (top sources breakdown per team). On-the-fly queries, no pre-aggregation table in v1.
- **BMO-12**: app-site superadmin dashboard at `/account/admin/` — 4 sections (Brain Overview, Storage, Activity, Top Sources). Tables + inline SVG sparklines for Activity (no chart library dependency). Drill-down button per team row routes to `/account/teams/brain/?team=<slug>&as_superadmin=1` (banner "Viewing as superadmin — this access is logged.").

### Phase 12 — GitHub App Migration (LIVE 2026-05-17) — GHAPP-01..08

- **GHAPP-01**: GitHub App "xbrain" created on `mrboups` personal account with multi-callback URLs natively supported: `https://grooveos.app/account/teams/` (web) + `https://anigikcnmldoklcmogffmgcojdhhficb.chromiumapp.org/` (Chrome extension stable ID via manifest `key`). Minimal permissions: `read:user`, `user:email`, `read:org`. Private key (PEM, RS256) stored server-side as `GITHUB_APP_PRIVATE_KEY_B64`. App ID `3743573`, Client ID `Iv23liVnZvIN0Lo6isof`.
- **GHAPP-02**: Backend JWT signing infrastructure — `app/services/github_app_jwt.mint_app_jwt()` mints RS256 JWT signed with `GITHUB_APP_PRIVATE_KEY_B64`, `iss = GITHUB_APP_CLIENT_ID`, 10-min TTL. `app/services/github_installation.get_installation_token()` exchanges JWT for installation token (1h TTL), in-process LRU cache, refresh-on-401. PyJWT[crypto]>=2.10 added to `apps/memory-api/pyproject.toml`.
- **GHAPP-03**: New `installations` table (`installation_id BIGINT PK`, `github_org_login TEXT`, `github_account_type TEXT DEFAULT 'Organization'`, `installed_at TIMESTAMPTZ`, `installed_by_github_id BIGINT NULL`, `permissions JSONB`, `suspended_at TIMESTAMPTZ NULL`, `revoked_at TIMESTAMPTZ NULL`, `raw_payload JSONB`, `updated_at TIMESTAMPTZ`) + webhook handler `POST /v1/webhooks/github/installation` with HMAC signature verification for `installation` and `installation_repositories` events. Source-of-truth synced from GitHub.
- **GHAPP-04**: `/orgs/{org}/members/{username}` org membership check migrated from `GITHUB_API_PAT` to installation token via hybrid lookup (`get_installation_token_for_org(session, org)` — looks up `installations` row, mints/caches installation token, falls back to "org not installed" error if absent). `GITHUB_API_PAT` removed from `.env.example`, `docker-compose.yml`, and all runtime config.
- **GHAPP-05**: User-to-server token refresh flow — migration 0019 adds 7 columns to `users` table (`github_access_token_enc`, `github_access_token_hash`, `github_refresh_token_enc`, `github_token_expires_at`, `github_refresh_expires_at` + existing `github_id`, `github_username`). `app/services/github_user_token.refresh_user_token_if_needed(session, user)` rotates `ghu_` token (8h TTL) using `ghr_` refresh token (6-month TTL), per-user `asyncio.Lock` to dedupe concurrent refreshes. Transparent refresh before any `/user/*` call when token < 5min from expiry.
- **GHAPP-06**: Install flow UI — when user signs in but their primary org has not installed the GitHub App, app-site and chrome-extension popup display install banner with link to `https://github.com/apps/xbrain/installations/new` with `state` param for return URL. After install webhook arrives and populates `installations`, user can complete team join.
- **GHAPP-07**: Frontend client_id constants updated — `app-site/account/teams/teams.js:37` and `chrome-extension/background.js:68` to new GitHub App client_id `Iv23liVnZvIN0Lo6isof`. Multi-callback support means single client_id serves both flows (no per-frontend dispatch in memory-api). `chrome.runtime.id` stability verified via fixed `key` in `chrome-extension/manifest.json` (derived ID `anigikcnmldoklcmogffmgcojdhhficb`).
- **GHAPP-08**: OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) removed from active code path — OAuth-App-specific dispatch logic deleted in `apps/memory-api/app/routes/auth_github.py`. Migration documented in `docs/auth.html` + `.planning/KB/oauth-app-revocation.md`. Revocation on GitHub UI scheduled J+1 (operator decision per runbook, gated on ≥24h post-deploy + zero auth errors). LibreChat-specific OAuth App `xbrain LibreChat` (Client ID `Ov23li0XHV3NL8Git7Dk`) remains untouched — separate concern.

### Out-of-band capabilities (Quick Tasks)

Several capabilities shipped between phases via the GSD Quick Task surface (atomic single-commit deliverables) and are listed here for completeness:

- **mcp-brain remote MCP server** (2026-05-09, commit `9f21d52`) — remote MCP server allowing Claude.ai web + ChatGPT web to access team brain via standard MCP protocol.
- **LibreChat LLM stack expansion** (2026-05-11, commit `d8fcb69`) — Grok-3 endpoint, Claude Reasoning endpoint, second-opinion 3-way (Sonnet+Opus 4.7+Grok-3), Anthropic prompt caching on 6 extraction sites.
- **Chrome extension v1.2.0** (2026-05-12) — single-click Connect/Disconnect, side panel mode (Chrome 114+), LibreChat API key auto-fill, zero-click silent Google auto-mint, context menu "Add selection to xbrain" with team submenu, link GitHub account from extension.
- **Team chat realtime** (2026-05-12, commit `d7716c1`) — Centrifugo v6 broker (`centrifugo.grooveos.app`, MIT, ~50MB RAM), `team_messages` table, 4 messaging endpoints, agent-context-bundle endpoint, session-bridge accepts `acting_user_sub` JWT, inline Claude handler with Pro/Max routing + Anthropic fallback, 5min team memory cache, full extension UI redesign (chat-first, clip overlay).

---
*Requirements defined: 2026-05-02 (v1 73 REQ-IDs frozen)*
*Last updated: 2026-05-27 — Full traceability backfill: all 73 v1 REQ-IDs flipped to [x] Done, traceability table Status updated to "Done (Phase N)" for each. Phases 1–13 all LIVE/Complete per ROADMAP.md. No deferred or dropped requirements found.*

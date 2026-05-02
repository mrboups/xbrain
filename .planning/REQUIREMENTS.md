# Requirements: xbrain — AI Cognitive OS

**Defined:** 2026-05-02
**Core Value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.

**Note de lecture :** les requirements parlent de **capacités**, pas de frameworks. Les choix techniques (mem0, LibreChat, Qdrant, Neo4j, etc.) sont dans `PROJECT.md` et `research/STACK.md` et peuvent évoluer sans changer les REQ-IDs.

## v1 Requirements

Périmètre du milestone d'initialisation (Phases 1 + 2 + 3 de la roadmap). Tout ce qui doit exister pour que xbrain soit une plateforme utilisable et différenciante de bout en bout.

### Authentication & Identity

- [ ] **AUTH-01**: User can sign in with Google SSO via the team frontend (LibreChat)
- [ ] **AUTH-02**: User can sign in with email/password as fallback when Google SSO is unavailable
- [ ] **AUTH-03**: Service clients (agents, MCP servers) can authenticate with short-lived JWT/API tokens issued by `memory-api`
- [ ] **AUTH-04**: User session persists across browser refresh and is invalidated on logout
- [ ] **AUTH-05**: Service-account tokens carry an explicit `team_scope` and act under that scope only
- [ ] **AUTH-06**: Every API call (human or service) is traceable to its identity (user id or service-account id) in the audit log

### Team & Permissions

- [ ] **TEAM-01**: Org admin can create teams and invite members with role assignment (Admin / Member / Viewer)
- [ ] **TEAM-02**: Every persisted datum is bound to a single `team_scope` and is invisible to other teams by default
- [ ] **TEAM-03**: A team can have multiple projects; each datum carries a `project_scope` within its team
- [ ] **TEAM-04**: RBAC is enforced at `memory-api` layer (not in frontends) for read, write, and promotion endpoints
- [ ] **TEAM-05**: Cross-team visibility is impossible except for items explicitly promoted to `PUBLIC` (cf. `TRUTH-*`)
- [ ] **TEAM-06**: Org admin panel allows managing teams, users, roles, and quotas

### Memory & Tagging

- [ ] **MEM-01**: Every datum written to the system carries the 7-field tagging contract (`team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`); writes missing any field are rejected at `memory-api` layer
- [ ] **MEM-02**: New data is born at `truth_level=EPHEMERAL` by default
- [ ] **MEM-03**: Every memory item has provenance metadata: who created it (user/service), from which frontend or tool, on which conversation/event
- [ ] **MEM-04**: Conversations from any frontend (LibreChat, Open WebUI) are persisted via `memory-api` and indexed for retrieval
- [ ] **MEM-05**: Documents and assets (PDFs, images, decks, datasets) are stored in object storage and indexed with full tagging contract
- [ ] **MEM-06**: Memory items support entity-aware long-term storage (entities surface across conversations and projects within a team)
- [ ] **MEM-07**: Memory items support fact versioning — updating a fact creates a new version with diff retained, never a silent overwrite
- [ ] **MEM-08**: Conflict detection surfaces when a `WORKING` fact contradicts an existing `VALIDATED` or `CANONICAL` fact in the same scope
- [ ] **MEM-09**: Automatic structured extraction can convert raw text (conversations, documents) into structured facts/tasks/entities with provenance back to the source
- [ ] **MEM-10**: Temporal queries are supported — "what did the team believe about X as of date Y?" returns the truth state at that point

### Frontends & Chat

- [ ] **CHAT-01**: User can chat with multiple LLMs (Claude, GPT, Grok minimum) from a single team frontend (LibreChat)
- [ ] **CHAT-02**: New LLM providers can be added by org admin via configuration without code change
- [ ] **CHAT-03**: Conversation history persists per user and is queryable as team memory
- [ ] **CHAT-04**: User can upload files (PDF, image, dataset) into a conversation and ask questions over them (basic RAG)
- [ ] **CHAT-05**: Open WebUI is available as a separate admin/tooling workspace (RAG config, agent tests, monitoring)
- [ ] **CHAT-06**: User can request a parallel "second opinion" from a different model on the same prompt (e.g., ask Claude, get Grok's parallel critique)
- [ ] **CHAT-07**: Chat replies are auto-enriched with relevant `CANONICAL` facts from the user's team/project memory before the LLM call
- [ ] **CHAT-08**: Members who use ChatGPT externally can read/write team memory via a `memory-api` OpenAI-compatible endpoint

### Search & RAG

- [ ] **SRCH-01**: Semantic search over team memory returns results ranked by relevance, scoped to caller's team and visible truth levels by default
- [ ] **SRCH-02**: Hybrid search combines semantic (vector) and keyword (BM25) modalities for exact-term recall
- [ ] **SRCH-03**: User can filter search results by `truth_level` (e.g., "only `VALIDATED` or above")
- [ ] **SRCH-04**: User can search across all projects within their team explicitly (cross-project within team)
- [ ] **SRCH-05**: Graph-traversal queries are available (e.g., "what depends on entity X?", "show lineage of fact Y")

### Truth-Level Workflow

- [ ] **TRUTH-01**: A truth-level state machine enforces the progression `EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC` with no skipping; demotions are also tracked
- [ ] **TRUTH-02**: Member can request promotion of a fact; admin or designated reviewer approves to advance the level
- [ ] **TRUTH-03**: Every promotion event is recorded in an append-only audit log: who proposed, who approved, when, with what evidence
- [ ] **TRUTH-04**: Visual indicator on memory items shows current `truth_level` in any frontend that surfaces them
- [ ] **TRUTH-05**: Conflict-aware promotion: attempting to promote a fact that contradicts a higher-level fact in scope surfaces a warning and requires explicit override
- [ ] **TRUTH-06**: Items at `PUBLIC` level are readable across all teams in the org; items at `CANONICAL` and below are not
- [ ] **TRUTH-07**: Demotion (e.g., `CANONICAL → VALIDATED`) is supported with a recorded reason
- [ ] **TRUTH-08**: Bulk imports land at `EPHEMERAL` or `WORKING` only; no shortcut to `VALIDATED+` via import
- [ ] **TRUTH-09**: Agents can propose promotions but cannot autonomously promote to `CANONICAL` or `PUBLIC` — those require human approval

### Agents & Runtime

- [ ] **AGENT-01**: Agents execute on a persistent runtime (LangGraph) with checkpointed state and crash recovery
- [ ] **AGENT-02**: Agents read and write memory exclusively through `memory-api` endpoints (never direct DB access)
- [ ] **AGENT-03**: Agents can be paused for human-in-the-loop approval at configured workflow steps
- [ ] **AGENT-04**: Agents handle LLM failures, tool timeouts, and retries gracefully without leaking partial state
- [ ] **AGENT-05**: Background ingestion agent processes new documents (uploads, Drive sync) into structured memory automatically
- [ ] **AGENT-06**: Each agent has its own working memory namespace and reads from team shared memory with team_scope filter
- [ ] **AGENT-07**: Multi-agent orchestration is supported (supervisor delegates to specialized agents) with isolated task contexts

### Internal Tools (MCP / API)

- [ ] **MCP-01**: An MCP gateway routes tool calls from any frontend or agent to registered services
- [ ] **MCP-02**: A new internal tool can be added by registering an MCP server URL with the gateway — no core changes required
- [ ] **MCP-03**: Every tool invocation includes the caller's `team_scope` and `user_id`, enforced by the gateway
- [ ] **MCP-04**: Tool outputs are written to `memory-api` with the full tagging contract (no direct DB writes from tools)
- [ ] **MCP-05**: Scraper service is available as the first MCP tool, demonstrating the integration pattern end-to-end
- [ ] **MCP-06**: Calendar service is available as an MCP tool (team events queryable from chat and agents)
- [ ] **MCP-07**: Pitch deck editor service is available as an MCP tool (decks editable, stored in object storage, indexed in memory)

### Integrations (External Sources)

- [ ] **INT-01**: Google Drive folders can be synced (read) into team memory; documents are indexed with full tagging contract
- [ ] **INT-02**: Drive sync is incremental — only changed files are re-processed
- [ ] **INT-03**: Drive folders can be mapped to specific team/project scopes (no cross-team bleed via Drive)
- [ ] **INT-04**: Agent-produced summaries can be written back to Drive documents (write-back loop) with explicit user opt-in

### Observability

- [ ] **OBS-01**: Every LLM call is traced with model, latency, token count, cost, and prompt version (Langfuse)
- [ ] **OBS-02**: Every agent workflow execution produces an end-to-end trace (request → plan → tool calls → memory writes → response)
- [ ] **OBS-03**: Operators receive alerts on agent crashes, tool timeouts, and RAG retrieval failures
- [ ] **OBS-04**: Every memory item is traceable to its origin: `memory-api` endpoint → conversation/tool call → identity → trace_id
- [ ] **OBS-05**: Admin sees a per-team cost dashboard (token spend, model breakdown, agent-vs-human contribution)

### Admin & Config

- [ ] **ADMIN-01**: The full stack (frontends, memory-api, agent runtime, DBs, observability, object storage) deploys via a single `docker compose up` from the repo
- [ ] **ADMIN-02**: All secrets (API keys, DB passwords, OAuth secrets) are sourced from environment variables, with `.env.example` template tracked in git but real `.env` excluded
- [ ] **ADMIN-03**: Each service exposes a healthcheck endpoint and Docker Compose is configured to wait for healthy upstreams
- [ ] **ADMIN-04**: Schema migrations are versioned (Alembic or equivalent) — no in-place destructive schema changes
- [ ] **ADMIN-05**: A documented backup procedure covers PostgreSQL, Qdrant, Neo4j, and object storage; a restore drill is performed and passes
- [ ] **ADMIN-06**: Admin can configure per-team rate limits / quotas (token budget, write rate) without restarting services

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

Mapping requirement → phase. Vide à l'init, sera rempli par le roadmapper.

| Requirement | Phase | Status |
|-------------|-------|--------|
| AUTH-01 | TBD | Pending |
| AUTH-02 | TBD | Pending |
| AUTH-03 | TBD | Pending |
| AUTH-04 | TBD | Pending |
| AUTH-05 | TBD | Pending |
| AUTH-06 | TBD | Pending |
| TEAM-01 | TBD | Pending |
| TEAM-02 | TBD | Pending |
| TEAM-03 | TBD | Pending |
| TEAM-04 | TBD | Pending |
| TEAM-05 | TBD | Pending |
| TEAM-06 | TBD | Pending |
| MEM-01 | TBD | Pending |
| MEM-02 | TBD | Pending |
| MEM-03 | TBD | Pending |
| MEM-04 | TBD | Pending |
| MEM-05 | TBD | Pending |
| MEM-06 | TBD | Pending |
| MEM-07 | TBD | Pending |
| MEM-08 | TBD | Pending |
| MEM-09 | TBD | Pending |
| MEM-10 | TBD | Pending |
| CHAT-01 | TBD | Pending |
| CHAT-02 | TBD | Pending |
| CHAT-03 | TBD | Pending |
| CHAT-04 | TBD | Pending |
| CHAT-05 | TBD | Pending |
| CHAT-06 | TBD | Pending |
| CHAT-07 | TBD | Pending |
| CHAT-08 | TBD | Pending |
| SRCH-01 | TBD | Pending |
| SRCH-02 | TBD | Pending |
| SRCH-03 | TBD | Pending |
| SRCH-04 | TBD | Pending |
| SRCH-05 | TBD | Pending |
| TRUTH-01 | TBD | Pending |
| TRUTH-02 | TBD | Pending |
| TRUTH-03 | TBD | Pending |
| TRUTH-04 | TBD | Pending |
| TRUTH-05 | TBD | Pending |
| TRUTH-06 | TBD | Pending |
| TRUTH-07 | TBD | Pending |
| TRUTH-08 | TBD | Pending |
| TRUTH-09 | TBD | Pending |
| AGENT-01 | TBD | Pending |
| AGENT-02 | TBD | Pending |
| AGENT-03 | TBD | Pending |
| AGENT-04 | TBD | Pending |
| AGENT-05 | TBD | Pending |
| AGENT-06 | TBD | Pending |
| AGENT-07 | TBD | Pending |
| MCP-01 | TBD | Pending |
| MCP-02 | TBD | Pending |
| MCP-03 | TBD | Pending |
| MCP-04 | TBD | Pending |
| MCP-05 | TBD | Pending |
| MCP-06 | TBD | Pending |
| MCP-07 | TBD | Pending |
| INT-01 | TBD | Pending |
| INT-02 | TBD | Pending |
| INT-03 | TBD | Pending |
| INT-04 | TBD | Pending |
| OBS-01 | TBD | Pending |
| OBS-02 | TBD | Pending |
| OBS-03 | TBD | Pending |
| OBS-04 | TBD | Pending |
| OBS-05 | TBD | Pending |
| ADMIN-01 | TBD | Pending |
| ADMIN-02 | TBD | Pending |
| ADMIN-03 | TBD | Pending |
| ADMIN-04 | TBD | Pending |
| ADMIN-05 | TBD | Pending |
| ADMIN-06 | TBD | Pending |

**Coverage:**
- v1 requirements: 65 total
- Mapped to phases: 0 (will be populated by roadmapper)
- Unmapped: 65 ⚠️ (expected at this stage)

---
*Requirements defined: 2026-05-02*
*Last updated: 2026-05-02 after initial definition (auto mode, derived from FEATURES.md + idea.md, mem0 substitution applied per PROJECT.md Key Decisions)*

# Feature Research — xbrain

**Domain:** Self-hostable collective AI memory platform for teams (multi-frontend, multi-model, multi-agent)
**Researched:** 2026-05-02
**Confidence:** HIGH (platform invariants from PROJECT.md + idea.md are authoritative; competitive landscape MEDIUM from WebSearch)

---

## Reading guide

This file organizes features by **capability category** (not by priority tier alone) and maps each feature to:
- Tier: **Table Stakes** (TS) / **Differentiator** (D) / **Anti-Feature** (AF)
- Size: **S** (days), **M** (1-2 weeks), **L** (3-4 weeks)
- Phase: **1** (infra + frontends) / **2** (memory + agents) / **3** (graph + extraction + integrations)
- Key dependencies listed inline

Platform invariants (tagging contract, truth levels, team-scope, multi-frontend, OSS-only) are **constraints, not features** — they are listed once in the Invariants section and assumed throughout.

---

## Platform Invariants (non-negotiable, not features to build or skip)

These apply to every schema, API endpoint, and service added:

| Invariant | Enforcement point |
|-----------|------------------|
| 7-field tagging contract on every stored datum (`team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`) | `memory-api` schema validation |
| Truth-level scale: `EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC` | `memory-api` + workflow engine |
| Org → Team → (Projects, Agents, Memory, Assets) hierarchy, isolation by default | Auth layer + `memory-api` scoping |
| All frontends read/write the same `memory-api` — no logic locked in a frontend | Architecture constraint |
| 100% open-source, self-hostable — no managed-cloud-only services | Infrastructure constraint |
| Multi-model minimum: Claude + GPT + Grok, extensible to Mistral / Gemini / other | Frontend config + router |

---

## 1. Authentication & Identity

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Sign-in via Google SSO | Team already uses Google Workspace; coherent with Drive integration | S | 1 | LibreChat OAuth config | LibreChat supports OpenID/Google OAuth out of the box |
| Local email/password fallback | Needed for agents, CI tokens, non-Google members | S | 1 | LibreChat auth | Simple to enable alongside SSO |
| JWT / API token for machine clients | Agents and MCP servers need service accounts | S | 1 | `memory-api` auth middleware | Short-lived tokens + refresh |
| Session management (logout, session list) | Standard expectation; security baseline | S | 1 | LibreChat | LibreChat handles this natively |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Service-account tokens with team scope | An MCP server or agent runs as `team:X agent:Y`, not as a human | M | 2 | Auth layer + tagging contract |
| Per-token audit trail | Every API call traceable to token → agent → action for compliance | M | 2 | Langfuse traces + PostgreSQL audit log |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| LDAP/Active Directory integration (v1) | Out-of-org scope; adds ops complexity with no user base to justify | Add in v2 if org grows; SSO + local covers all internal users |
| Custom-built auth service | Reinventing solved problem; LibreChat + Zitadel/Logto cover all cases | Use LibreChat's native OAuth stack |

---

## 2. Team & Permissions

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Role-based access: Admin / Member / Viewer | Users expect to manage who sees what | M | 1 | Auth layer + `memory-api` RBAC | Minimum 3 roles at launch |
| Team isolation by default | Cross-team data leakage is a hard no; users will leave if violated | M | 1 | `memory-api` `team_scope` filter | Every query scoped to caller's team |
| Invite members to team | Without invite flow, team cannot onboard | S | 1 | LibreChat user management | Email invite + role assignment |
| Org-level admin panel | Admins must be able to manage teams, users, quotas | M | 1 | Open WebUI admin + PostgreSQL | LibreChat admin panel (roadmapped mid-2025) |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Project-level scoping within a team | Team works on 5 projects; each project has its own memory bubble | M | 2 | `memory-api` `project_scope` field |
| Explicit cross-team sharing via truth-level promotion | Only CANONICAL or PUBLIC items can cross team boundaries — controlled, not accidental | L | 2 | Truth-level workflow (see §8) |
| Permission-aware RAG retrieval | Search results automatically filtered to caller's team + truth_level — no post-filtering hacks | M | 2 | Qdrant namespace-per-team + `memory-api` |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Guest accounts for external clients | Scope: internal org only in v1; adds auth complexity and data isolation risk | Plan for v2 if org-to-client sharing needed |
| Self-service team creation by members | Uncontrolled team proliferation fragments the memory graph | Admin-only team creation; members request via admin |

---

## 3. Memory & Tagging

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Every data point stored with full 7-field tagging contract | Platform invariant; no data accepted without it | M | 1 | `memory-api` schema + PostgreSQL | Must be enforced server-side, not optional |
| Conversation storage indexed as team memory | If conversations evaporate, the platform is just LibreChat — the entire value prop is gone | M | 1 | `memory-api` + Qdrant | Chat → memory pipeline is the core flow |
| Document / asset storage with tagging | PDFs, decks, datasets stored in MinIO + indexed with tags | M | 1 | MinIO + `memory-api` | Asset references point to MinIO; metadata in PostgreSQL |
| Provenance on every memory item | Who created it, from which frontend, from which agent, at what truth_level | S | 1 | `memory-api` `source` + `validation_status` fields | Required from day 1 |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Long-term memory with entity graph (Remembra) | Conversations produce persistent facts linked to entities — survives across sessions and users | L | 2 | Remembra + PostgreSQL + Qdrant |
| Knowledge versioning & conflict detection (Memstate) | "Semantic git" — fact was WORKING, updated to VALIDATED, previous version retained with diff | L | 2 | Memstate + PostgreSQL |
| Automatic structured extraction (Memori) | Raw text → structured facts/tasks/entities/rules without human effort | L | 3 | Memori + Remembra + Qdrant |
| Temporal memory queries | "What did the team believe about X last month?" — time-travel queries on the knowledge graph | M | 3 | Remembra + Neo4j + PostgreSQL event store |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Schemas without full tagging contract | Violates the foundational invariant; every shortcut here breaks isolation and promotion later | Enforce at `memory-api` schema layer; reject on write |
| Frontend-local memory (plugin-only storage) | Defeats multi-frontend invariant; knowledge siloed in one UI | All writes go through `memory-api`; frontends are stateless on data |
| Unversioned fact overwrite | Silent overwrites destroy audit trail and make truth-level promotion untrustworthy | Use Memstate's versioning — always append + diff |

---

## 4. Frontends & Chat

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Multi-model chat: Claude + GPT + Grok in same UI | Team members already use different models; forcing one model loses adoption | S | 1 | LibreChat multi-endpoint config | LibreChat supports this natively |
| Persistent conversation history per user + team | Losing chat history is unacceptable for a memory platform | S | 1 | LibreChat + `memory-api` storage | LibreChat stores locally; `memory-api` indexes for RAG |
| File upload: PDF, images, datasets | Users expect to drop documents into chat and ask questions | S | 1 | LibreChat + MinIO | LibreChat supports file upload; MinIO stores assets |
| Basic RAG over uploaded documents | "Ask questions about this PDF" is day-1 user behavior | M | 1 | Qdrant + LibreChat RAG pipeline | Vector indexing of uploads at ingest |
| Open WebUI as admin/tooling workspace | Needed for agent testing, RAG config, monitoring — not a user-facing chat | M | 1 | Open WebUI Docker service | Different persona than LibreChat |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| RAG retrieval scoped to team + truth_level | Search returns only what this team validated — not noise from other teams or EPHEMERAL guesses | M | 2 | Permission-aware RAG (see §2) + Qdrant namespace |
| Second-opinion parallel model call | Send same prompt to Grok while Claude answers — contradiction surfaced inline | M | 2 | `memory-api` multi-model router + LibreChat agent |
| Context injection from team memory | Chat auto-enriched with relevant CANONICAL facts for team/project before LLM call | M | 2 | Remembra retrieval + LibreChat system prompt injection |
| ChatGPT API passthrough to `memory-api` | Members who stay on ChatGPT can still write/read team memory via API wrapper | L | 2 | `memory-api` OpenAI-compatible endpoint |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Custom-built chat frontend | Maintenance burden; LibreChat + Open WebUI cover all personas and are actively maintained OSS | Use LibreChat for team chat, Open WebUI for admin/tooling |
| Mobile-first / native app | Excluded by PROJECT.md; adds platform fragmentation; PWA via LibreChat if needed | LibreChat is responsive; acceptable for internal use |
| Real-time collaborative editing inside chat | High complexity, low ROI for a memory platform — collaboration is async via memory promotion | Use Google Drive for real-time doc collaboration; memory is the persistent layer |

---

## 5. Agents & Runtime

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Agent execution via LangGraph | Platform promises agents — without a runtime, there are no agents | M | 2 | LangGraph Docker service + `memory-api` | LangGraph handles persistence, state, workflows |
| Agents write to `memory-api` with tagging contract | Agent outputs must be indexable, scoped, and promotable — same rules as humans | M | 2 | `memory-api` write API + LangGraph tool |
| Human-in-the-loop approval for critical agent steps | Agents should not autonomously promote to CANONICAL without human sign-off | M | 2 | LangGraph human interrupt + truth-level workflow |
| Agent error handling + retry | Production agents must handle LLM failures, tool timeouts gracefully | M | 2 | LangGraph state management |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Ingestion agents (background) | Automatically process new Drive docs / tool outputs into team memory — no manual effort | M | 2 | LangGraph + drive-sync service + Memori |
| Validation agents | Agents flag contradictions between WORKING facts, surface to human for resolution | L | 3 | Memstate conflict detection + LangGraph |
| Multi-agent orchestration (supervisor pattern) | Complex workflows: orchestrator delegates to specialized agents (scraper → extractor → validator) | L | 3 | LangGraph multi-agent + agent namespaced memory |
| Agent-specific memory namespaces | Each agent has its own working memory + reads from team shared namespace | M | 2 | LangGraph + Qdrant namespace-per-agent |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Agents that bypass `memory-api` and write directly to Qdrant/PostgreSQL | Breaks tagging contract; outputs become unscoped and unpromotable | All agent writes route through `memory-api` |
| Autonomous CANONICAL promotion without human approval | Trust collapse: team cannot rely on CANONICAL if agents can write it unilaterally | Agents max out at VALIDATED; human promotes to CANONICAL |
| Single long-running agent process (no isolation) | Failure cascades across all team workloads | LangGraph provides task isolation per thread/workflow |

---

## 6. Internal Tools (MCP/API)

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| MCP gateway routing tool calls to registered services | Without a router, each frontend must know about each tool — spaghetti | M | 3 | `mcp-gateway` service + MCP protocol |
| Every tool publishes outputs to `memory-api` with tagging | Tool outputs must land in team memory — that is the entire point of connecting tools | S | 3 | `memory-api` write API + tagging contract |
| Tool registration without infra change | Adding a new MCP server requires only: implement server, register URL — no core changes | M | 3 | `mcp-gateway` service discovery |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Scraper service as first MCP tool | Demonstrates the pattern end-to-end; team gets immediate value from data scraping into memory | M | 3 | `scraper` service + `mcp-gateway` + `memory-api` |
| Calendar service as MCP tool | Team events, deadlines visible in memory context — agents can reason about scheduling | M | 3 | `calendar` service + `mcp-gateway` |
| Deck-service as MCP tool | Collaborative pitch deck editing outputs stored in team memory | L | 3 | `deck-service` + MinIO + `memory-api` |
| Tool call audit trail in Langfuse | Every MCP tool invocation traced: who called, what args, what output, latency | M | 3 | Langfuse + `mcp-gateway` instrumentation |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Frontend-specific plugins (LibreChat plugin / Open WebUI extension) for tool logic | Tool logic locked in frontend violates multi-frontend invariant | Implement as MCP server behind `mcp-gateway`; frontend just calls the gateway |
| Direct database access from MCP tools | Bypasses tagging enforcement; tools see unscoped data | Tools query via `memory-api` search endpoints only |

---

## 7. Search & RAG

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Semantic search over team documents | "Find everything about topic X" is the primary user journey | M | 1 | Qdrant + embedding pipeline | Basic unscoped search in Phase 1; scoped in Phase 2 |
| Keyword / BM25 hybrid search | Semantic alone misses exact terms (codes, names, IDs) | M | 2 | Qdrant hybrid search or PostgreSQL full-text |
| Search results scoped to caller's team + truth_level | Prevents cross-team data leakage in search | M | 2 | `memory-api` search + Qdrant namespace filter |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Graph traversal search via Neo4j | "What depends on this entity?" / "Show lineage of this fact" — impossible with vector search alone | L | 3 | Neo4j + `memory-api` graph query endpoint |
| Truth-level filter in search | "Show me only VALIDATED or above" — user trusts results are vetted | S | 2 | `memory-api` search filter on `truth_level` |
| Temporal search ("as of date X") | Retrieve team knowledge state at a past point in time | M | 3 | Remembra + PostgreSQL event store |
| Cross-project search within team (explicit) | User explicitly searches across all their team's projects | S | 2 | `memory-api` `project_scope` = wildcard within team |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Global cross-team search by default | Privacy violation; destroys team isolation | Always default to team-scoped; org-wide search only for Org Admin on PUBLIC items |
| Pinecone or managed vector DB | Cloud-only; violates OSS constraint | Qdrant self-hosted |

---

## 8. Truth-Level Workflow

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Every item born at EPHEMERAL on write | New data is untrusted by default — invariant from day 1 | S | 1 | `memory-api` schema default |
| Visual truth-level indicator on memory items | Users must see what level a fact is at; opaque trust is useless | S | 2 | Open WebUI UI component + `memory-api` |
| Manual promotion request (human → admin) | Team member proposes promoting a fact; admin approves | M | 2 | PostgreSQL workflow table + `memory-api` state machine |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Approval workflow with roles (proposer → reviewer → admin) | Structured validation — xbrain's primary differentiator from "just LibreChat + Qdrant" | L | 2 | PostgreSQL workflow + LangGraph approval step + RBAC |
| Audit trail on every promotion event | Who promoted, when, what evidence cited — compliance-ready | M | 2 | PostgreSQL event store + Langfuse |
| Conflict detection on WORKING → VALIDATED | Before promoting, check if fact contradicts existing VALIDATED/CANONICAL — surface warning | L | 3 | Memstate conflict detection + `memory-api` |
| Rollback: demote from CANONICAL back to VALIDATED | Facts can be invalidated; demotion tracked with reason | M | 3 | Memstate versioning + PostgreSQL |
| PUBLIC exposure: team can publish selected facts | CANONICAL items explicitly promoted to PUBLIC become cross-org readable | M | 3 | `memory-api` visibility = public + org-level read scope |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Auto-promotion to CANONICAL by agent without human | Agents max at VALIDATED; humans hold CANONICAL/PUBLIC authority | Agents propose, humans approve in LangGraph interrupt step |
| Bulk import that bypasses promotion workflow | Shortcuts destroy trust in the scale — CANONICAL becomes meaningless | Import lands at EPHEMERAL or WORKING; explicit promotion required |

---

## 9. Integrations (Google Drive first)

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Google Drive document sync (read) | Team already stores documents in Drive; without sync, users must manually re-upload | L | 3 | `drive-sync` service + Google Drive API + MinIO + Memori |
| Incremental sync (changed files only) | Full re-index on every sync is expensive and slow | M | 3 | `drive-sync` change tracking + LlamaIndex incremental pattern |
| Ingested documents tagged with full tagging contract | Drive docs must enter memory as EPHEMERAL/WORKING, not bypass the contract | S | 3 | `memory-api` + `drive-sync` tagging layer |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Automatic entity extraction from Drive docs (Memori) | Drive document → structured facts in team memory — no manual summarization | L | 3 | Memori + `drive-sync` + Qdrant |
| Drive folder scoped to team/project | Drive folder X maps to team Y project Z — isolation maintained in Drive too | M | 3 | `drive-sync` config + `memory-api` scoping |
| Write-back: agent outputs to Drive doc | Agent-produced summaries/reports pushed back to Drive for human review | L | 3 | `drive-sync` write API + Google Drive API |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Notion, Slack, Linear integrations (Phase 1-2) | Drive covers primary doc use case; adding more integrations before `memory-api` is stable adds unneeded surface area | Architecture supports additional connectors in Phase 3+; prioritize Drive |
| Google Drive as source of truth (bypass memory-api) | Defeats the entire platform; Drive is an input, not the authoritative store | Drive syncs INTO `memory-api`; `memory-api` is the truth layer |

---

## 10. Observability

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| LLM call traces (latency, cost, model, token count) | Without traces, debugging agent failures and controlling costs is impossible | M | 1 | Langfuse + LibreChat instrumentation |
| Agent workflow traces end-to-end | Complete trace of: user request → agent plan → tool calls → memory writes → response | M | 2 | Langfuse + LangGraph integration |
| Error alerts (agent crash, tool timeout, RAG failure) | Silent failures destroy trust in the platform | M | 2 | Langfuse + alerting webhook or email |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Memory lineage trace | For any memory item: "how did this fact get here?" — trace back to source conversation or tool call | L | 3 | Langfuse trace + Remembra provenance + Neo4j lineage |
| Per-team cost dashboard | Admin can see which team is burning most tokens / which agent is expensive | M | 2 | Langfuse analytics + PostgreSQL aggregation |
| Prompt versioning and experiment tracking | Know which system prompt version produced which quality memory items | M | 2 | Langfuse prompt management |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| External SaaS observability (Datadog, New Relic) | Violates OSS constraint; Langfuse is self-hostable and purpose-built for LLM | Langfuse self-hosted covers all LLM-specific needs |
| User behavior analytics / session recording | Not relevant for internal team platform; privacy concern | Rely on Langfuse traces and PostgreSQL audit logs for operational insight |

---

## 11. Admin & Config

### Table Stakes

| Feature | Why Expected | Size | Phase | Dependencies | Notes |
|---------|--------------|------|-------|--------------|-------|
| Docker Compose-based deployment (single command up) | Self-hostable must mean simple to operate — `docker compose up` is the bar | M | 1 | `infrastructure/docker-compose.yml` | All services containerized |
| Environment-based configuration (`.env`) | Standard 12-factor pattern; secrets must not be in code | S | 1 | All services | `.env.example` template |
| Model endpoint management (add/remove model providers) | Admin can add Mistral or remove a provider without code change | S | 1 | LibreChat `librechat.yaml` config |
| Health checks for all services | Ops baseline: know which container is down | S | 1 | Docker Compose healthchecks |
| Basic admin panel for user/team management | Admin must be able to create teams, assign roles, manage users | M | 1 | Open WebUI admin + LibreChat admin (roadmapped) |

### Differentiators

| Feature | Value Proposition | Size | Phase | Dependencies |
|---------|-------------------|------|-------|--------------|
| Per-team memory quota / rate limit | Prevent one team from exhausting shared resources | M | 2 | `memory-api` + PostgreSQL quota table |
| Migration scripts for schema evolution | Platform needs to evolve schemas without data loss — versioned migrations | M | 2 | PostgreSQL migrations (Flyway or Alembic) |
| Backup strategy: PostgreSQL + Qdrant + MinIO + Neo4j | Data loss on a memory platform is catastrophic | M | 2 | Volume backup scripts + documentation |

### Anti-Features

| Feature | Why Avoid | Alternative |
|---------|-----------|-------------|
| Kubernetes deployment (v1) | Ops complexity disproportionate to team size; e2-medium + Docker Compose is sufficient | Consider k8s in v2 if scaling to multiple orgs |
| Managed cloud services (Pinecone, OpenAI Assistants, Notion as store) | OSS constraint; cloud-only lock-in | Qdrant, PostgreSQL, MinIO — all self-hosted |

---

## Feature Dependencies

```
[Google SSO] ──requires──> [LibreChat OAuth config]
[Team isolation] ──requires──> [RBAC roles] ──requires──> [Auth layer]
[Permission-aware RAG] ──requires──> [Team isolation] + [Qdrant namespace-per-team]
[Conversation → memory pipeline] ──requires──> [memory-api] + [Qdrant embedding]
[Truth-level promotion workflow] ──requires──> [memory-api state machine] + [RBAC roles] + [PostgreSQL workflow table]
[Conflict detection (Memstate)] ──requires──> [Long-term memory (Remembra)] ──requires──> [memory-api] + [Qdrant] + [PostgreSQL]
[Graph traversal search] ──requires──> [Neo4j] ──requires──> [memory-api graph write]
[LangGraph agents] ──requires──> [memory-api write API] + [LangGraph service]
[Human-in-the-loop approval] ──requires──> [LangGraph human interrupt] + [truth-level workflow]
[Drive sync] ──requires──> [drive-sync service] + [memory-api] + [MinIO] + [Memori (optional)]
[MCP gateway] ──requires──> [memory-api write API] + [MCP protocol]
[Langfuse traces] ──requires──> [Langfuse service] + [instrumentation in all services]
[Memory lineage] ──requires──> [Langfuse traces] + [Remembra provenance] + [Neo4j]
[Temporal memory queries] ──requires──> [Remembra] + [PostgreSQL event store] + [Neo4j]
[Automatic extraction (Memori)] ──requires──> [Remembra] + [LangGraph agents]
[Agent validation workflow] ──requires──> [Memstate conflict detection] + [LangGraph] + [HITL approval]
```

### Critical dependency chain

```
Phase 1 baseline (must hold before Phase 2 builds on it):
  memory-api (tagging contract enforced)
    → team scoping + RBAC
      → conversation storage → Qdrant embedding
        → basic RAG

Phase 2 builds on Phase 1:
  Remembra + Memstate
    → truth-level promotion workflow (HITL)
      → permission-aware RAG
        → LangGraph agents writing to memory-api
          → second-opinion model call
            → per-team cost dashboard (Langfuse)

Phase 3 builds on Phase 2:
  Neo4j graph
    → graph search + lineage
  Memori extraction
    → automatic structured facts from Drive docs
  Drive sync service
    → all internal tools via MCP gateway
```

---

## MVP Definition (Phase 1 = Day-1 Usable Platform)

### Must ship in Phase 1 (platform is broken without these)

- [ ] Docker Compose stack: LibreChat + Open WebUI + PostgreSQL + Qdrant + MinIO + Langfuse running locally and on GCP VM
- [ ] Google SSO login via LibreChat (+ local fallback)
- [ ] Multi-model chat: Claude + GPT + Grok endpoints configured
- [ ] Conversation persistence (LibreChat local storage + indexed to Qdrant)
- [ ] Basic RAG: file upload → embedding → question over document
- [ ] `memory-api` skeleton: write endpoint (enforce 7-field tagging contract), read endpoint (team-scoped)
- [ ] Every conversation and document write passes through `memory-api` and gets tagged EPHEMERAL
- [ ] LLM call traces in Langfuse (latency, model, cost)
- [ ] Admin-created teams and user assignment (Open WebUI admin or LibreChat admin panel)
- [ ] RBAC: Admin / Member / Viewer roles enforced at `memory-api` layer

### Add in Phase 2 (memory becomes intelligent)

- [ ] Remembra long-term memory + entity graph
- [ ] Memstate versioning + conflict detection
- [ ] Truth-level promotion workflow (EPHEMERAL → WORKING → VALIDATED → CANONICAL)
- [ ] Permission-aware RAG (team-scoped + truth_level-filtered)
- [ ] LangGraph agent runtime + first ingestion agent
- [ ] Human-in-the-loop approvals in LangGraph
- [ ] Second-opinion parallel model call feature
- [ ] Context injection from team memory into chat system prompt
- [ ] Per-team cost dashboard in Langfuse
- [ ] PostgreSQL + Qdrant + MinIO backup strategy

### Add in Phase 3 (full platform)

- [ ] Neo4j graph layer + lineage queries
- [ ] Memori automatic structured extraction
- [ ] Google Drive sync (incremental, team-scoped, tagged)
- [ ] MCP gateway + first three tools: scraper, calendar, deck-service
- [ ] PUBLIC truth-level: cross-org readable items
- [ ] Memory lineage trace (Langfuse + Neo4j)
- [ ] Temporal memory queries ("as of date X")
- [ ] Agent validation workflows (Memstate conflict → human review)

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Phase | Priority |
|---------|------------|---------------------|-------|----------|
| Docker Compose deployment | HIGH | MEDIUM | 1 | P1 |
| Google SSO | HIGH | LOW | 1 | P1 |
| Multi-model chat | HIGH | LOW | 1 | P1 |
| `memory-api` with tagging contract | HIGH | MEDIUM | 1 | P1 |
| Conversation → memory pipeline | HIGH | MEDIUM | 1 | P1 |
| Basic RAG (upload → question) | HIGH | MEDIUM | 1 | P1 |
| RBAC (Admin/Member/Viewer) | HIGH | MEDIUM | 1 | P1 |
| LLM traces in Langfuse | MEDIUM | LOW | 1 | P1 |
| Remembra long-term memory | HIGH | HIGH | 2 | P1 |
| Truth-level promotion workflow | HIGH | HIGH | 2 | P1 |
| Permission-aware RAG | HIGH | MEDIUM | 2 | P1 |
| LangGraph agent runtime | HIGH | HIGH | 2 | P1 |
| HITL agent approvals | HIGH | MEDIUM | 2 | P1 |
| Memstate versioning | MEDIUM | HIGH | 2 | P2 |
| Second-opinion model call | MEDIUM | MEDIUM | 2 | P2 |
| Context injection from memory | HIGH | MEDIUM | 2 | P2 |
| Google Drive sync | HIGH | HIGH | 3 | P1 |
| MCP gateway + tools | HIGH | HIGH | 3 | P1 |
| Neo4j graph layer | MEDIUM | HIGH | 3 | P2 |
| Memori extraction | HIGH | HIGH | 3 | P2 |
| Memory lineage trace | MEDIUM | HIGH | 3 | P2 |
| Temporal memory queries | LOW | HIGH | 3 | P3 |
| Agent validation workflow | MEDIUM | HIGH | 3 | P2 |
| PUBLIC cross-org exposure | MEDIUM | MEDIUM | 3 | P2 |
| Per-team memory quotas | LOW | MEDIUM | 2 | P3 |
| ChatGPT API passthrough to memory-api | MEDIUM | HIGH | 2 | P3 |

---

## Competitive Landscape (what xbrain is NOT)

| Competitor | What they do | Why xbrain is different |
|------------|--------------|------------------------|
| Mem.ai | Personal AI second brain, cloud-only, individual focus | xbrain is team-scoped, self-hosted, multi-frontend |
| Notion AI | Document workspace with AI bolt-on, no truth levels, cloud | xbrain has truth-level promotion, team isolation, OSS |
| LibreChat alone | Great chat UI, no persistent memory layer, no team isolation | xbrain wraps LibreChat with `memory-api` + truth-level workflow |
| Open WebUI alone | Admin UI + RAG, single-model focus, no team memory | xbrain uses Open WebUI as admin layer; memory is the product |
| LangGraph alone | Agent runtime, no memory platform | xbrain uses LangGraph as one component of a full memory platform |
| Letta (formerly MemGPT) | OSS persistent agent memory, individual not team | xbrain adds team scope, multi-frontend, truth levels on top |

---

## Explicit Anti-Features List (for Requirements Writer)

The following must NOT appear in REQUIREMENTS.md without an explicit decision to revisit scope:

| Anti-Feature | Origin of Exclusion | Rationale |
|-------------|---------------------|-----------|
| Mobile app / native app | PROJECT.md Out of Scope | Maintenance burden; LibreChat PWA sufficient for internal |
| Custom-built chat frontend | PROJECT.md Out of Scope | LibreChat + Open WebUI cover all personas |
| SaaS multi-tenant for external clients | PROJECT.md Out of Scope | v1 is internal org only; adds auth and data isolation complexity |
| Schemas without full 7-field tagging contract | Platform invariant | Breaks isolation, promotion, and audit — violates founding principle |
| Frontend-locked logic or storage | Platform invariant | All data and logic lives in `memory-api` |
| Managed cloud services (Pinecone, OpenAI Assistants, Notion as store) | PROJECT.md Out of Scope | OSS/self-hostable constraint; vendor lock-in |
| Single-model lock (Claude-only or GPT-only) | Platform invariant | Multi-model is a hard requirement from the start |
| Autonomous agent promotion to CANONICAL | Trust model | CANONICAL requires human approval; agents max at VALIDATED |
| LDAP / Active Directory (v1) | Scope | Internal org uses Google SSO; LDAP adds ops with no current user base |
| Kubernetes deployment (v1) | Budget / ops | e2-medium + Docker Compose is the baseline; k8s deferred to v2 |
| Notion, Slack, Linear integrations (Phase 1-2) | Phase scope | Drive is first; other connectors follow in Phase 3+ once `memory-api` is stable |
| Global cross-team search by default | Privacy / isolation | Always team-scoped; org-wide only for PUBLIC items by Org Admin |
| Guest accounts for external clients | Scope | Internal org only in v1 |
| Bulk import bypassing promotion workflow | Trust model | All imports land at EPHEMERAL/WORKING; explicit promotion required |

---

## Sources

- [LibreChat 2025 Roadmap](https://www.librechat.ai/blog/2025-02-20_2025_roadmap) — admin panel, team creation, user memories planned
- [Open WebUI RBAC documentation](https://deepwiki.com/open-webui/docs/5.3-roles-groups-and-permissions) — roles, groups, visibility levels
- [LangGraph agent memory architecture](https://dev.to/sreeni5018/the-architecture-of-agent-memory-how-langgraph-really-works-59ne) — short-term vs long-term memory, namespaced team memory
- [LangGraph long-term agentic memory course](https://www.deeplearning.ai/short-courses/long-term-agentic-memory-with-langgraph/) — persistence patterns for agents
- [Multi-tenancy RAG with Milvus](https://milvus.io/blog/build-multi-tenancy-rag-with-milvus-best-practices-part-one.md) — namespace-per-tenant pattern (applicable to Qdrant)
- [Langfuse observability overview](https://langfuse.com/docs/observability/overview) — tracing, prompt management, team annotation queues
- [LlamaIndex Google Drive RAG pipeline](https://developers.llamaindex.ai/python/examples/ingestion/ingestion_gdrive/) — incremental sync pattern
- [MCP architecture overview](https://www.kubiya.ai/blog/model-context-protocol-mcp-architecture-components-and-workflow) — tools/resources/prompts primitives, enterprise extension pattern
- [Knowledge graph confidence and validation](https://www.sciencedirect.com/science/article/pii/S030645732500086X) — human-in-the-loop validation patterns for knowledge promotion
- [8 Best AI Agent Memory Tools 2026](https://techsy.io/en/blog/best-ai-agent-memory-tools) — competitive landscape

---

*Feature research for: xbrain — self-hostable collective AI memory platform*
*Researched: 2026-05-02*

# Architecture Research

**Domain:** Self-hostable collective AI memory platform (multi-frontend, multi-model, team-scoped)
**Researched:** 2026-05-02
**Confidence:** HIGH (core decisions verified against official docs and current library state)

---

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         FRONTEND LAYER                                    │
│                                                                           │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  LibreChat  │  │ Open WebUI  │  │ ChatGPT API  │  │  Claude Code  │  │
│  │  (conv UI)  │  │ (admin/RAG) │  │  (external)  │  │ (dev session) │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │
│         │                │                │                   │          │
│    plugin/pipeline   pipeline/        API call             API call      │
│     (OpenAI compat.) custom pipe                                         │
├─────────┴────────────────┴────────────────┴───────────────────┴──────────┤
│                         GATEWAY LAYER (Nginx reverse proxy)               │
│            TLS termination — auth header forwarding — rate limiting       │
├──────────────────────────────────────────────────────────────────────────┤
│                         MEMORY-API  (FastAPI, Python)                    │
│                    The single source of truth for all data                │
│                                                                           │
│   POST /conversations   POST /facts   GET /retrieve   POST /promote      │
│   POST /ingest          GET /search   POST /assets    GET /audit         │
├──────────────────────────┬─────────────────────────┬─────────────────────┤
│    AGENT RUNTIME         │      MCP GATEWAY         │   OBSERVABILITY     │
│    (LangGraph server)    │  (FastAPI + MCP proto)   │   (Langfuse)        │
│    Reads/writes via      │  Tool registry + auth    │   OTLP ingest       │
│    memory-api only       │  context injection       │   traces/prompts    │
├──────────────────────────┴─────────────────────────┴─────────────────────┤
│                         MEMORY LAYER                                      │
│                                                                           │
│  ┌──────────────┐  ┌─────────────┐  ┌──────────────────────────────────┐ │
│  │  Remembra    │  │  Memstate   │  │         Memori                   │ │
│  │  (long-term  │  │ (versioning │  │ (structured extraction:          │ │
│  │  + entity    │  │  + conflict │  │  facts, tasks, entities, rules,  │ │
│  │  graph)      │  │  resolution)│  │  preferences from conversations) │ │
│  └──────────────┘  └─────────────┘  └──────────────────────────────────┘ │
│      All three are wrappers — they call memory-api, not DBs directly      │
├──────────────────────────────────────────────────────────────────────────┤
│                         STORAGE LAYER                                     │
│                                                                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ PostgreSQL  │  │   Qdrant    │  │   Neo4j     │  │    MinIO       │  │
│  │ (event store│  │ (vector     │  │ (knowledge  │  │ (assets:       │  │
│  │  audit logs │  │  retrieval  │  │  graph +    │  │  PDFs, decks,  │  │
│  │  truth-lvl  │  │  semantic   │  │  lineage +  │  │  images,       │  │
│  │  perms/wkfl)│  │  search)    │  │  provenance)│  │  datasets)     │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────────┘  │
├──────────────────────────────────────────────────────────────────────────┤
│                   INTERNAL TOOLS LAYER (services/)                        │
│                                                                           │
│  ┌───────────┐  ┌───────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  scraper  │  │ calendar  │  │  drive-sync  │  │   deck-service    │  │
│  │  service  │  │  service  │  │   service    │  │                   │  │
│  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘  └─────────┬─────────┘  │
│        └──────────────┴───────────────┴──────────────────────┘           │
│                  All publish to memory-api (tagging contract enforced)    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Owns | Does NOT Own |
|-----------|------|--------------|
| `memory-api` | All persistent data writes, tagging contract enforcement, retrieval scoping, truth-level workflow | LLM calls, frontend logic, tool-specific business logic |
| `agent-runtime` (LangGraph) | Agent graph execution, workflow state, human-in-the-loop interrupts, checkpointing | Direct DB writes (must go via memory-api), frontend state |
| `mcp-gateway` | Tool registration, discovery catalog, auth context injection (team_id, user_id in every tool call) | Tool business logic, memory storage (tools call memory-api themselves) |
| LibreChat | Multi-model chat UI, conversation rendering, file upload UX | Memory storage (posts to memory-api after each turn), model routing beyond config |
| Open WebUI | Admin UI, pipeline execution, agent test harness, RAG management | Memory storage (pipelines call memory-api), auth source of truth |
| PostgreSQL | Event sourcing, audit log, truth-level state machine, org/team/user/permission tables, LangGraph checkpoints | Semantic search, graph traversal, blob storage |
| Qdrant | Vector embeddings, semantic similarity search, agent memory retrieval | Relational queries, fact provenance, blob storage |
| Neo4j | Entity relationship graph, fact lineage, dependency graph, provenance chains | Event ordering, vector search, file storage |
| MinIO | Binary asset storage (PDFs, images, decks, datasets) | Metadata, search, graph relations |
| Langfuse | Trace ingestion (OTLP), prompt management, failure analysis, agent lineage | Data storage, routing decisions |
| Internal tools (scraper, calendar, etc.) | Their specific domain logic (scraping, calendar ops, Drive sync, deck generation) | Memory storage (they call memory-api), cross-tool orchestration |

---

## Component Interfaces and Contracts

### memory-api API Surface

All requests require `Authorization: Bearer <jwt>` where the JWT carries `{user_id, team_id, org_id, roles}`. The API extracts team scope from the JWT — callers never pass `team_scope` directly (prevents spoofing).

```
# Conversations
POST   /v1/conversations
       Body: { frontend_id, model, messages: [...], metadata: {} }
       Returns: { conversation_id, tagging: TaggingContract }

GET    /v1/conversations/{id}
       Params: ?include_messages=true
       Scoped by: team_id from JWT (automatic)

# Facts / Knowledge
POST   /v1/facts
       Body: {
         content: string,
         fact_type: "assertion|preference|task|entity|rule",
         truth_level: "EPHEMERAL|WORKING",   # caller can only set EPHEMERAL/WORKING
         confidence: float (0-1),
         source: { type: "conversation|tool|agent|human", ref_id: uuid },
         project_scope?: uuid,
         visibility: "team|org|public"
       }
       Returns: { fact_id, truth_level, tagging: TaggingContract }

GET    /v1/facts/{id}
GET    /v1/facts?project_scope=uuid&truth_level=WORKING,VALIDATED&limit=50

# Retrieval (RAG + graph + event)
POST   /v1/retrieve
       Body: {
         query: string,
         modalities: ["semantic", "graph", "event"],  # which stores to hit
         truth_levels: ["WORKING", "VALIDATED", "CANONICAL", "PUBLIC"],
         project_scope?: uuid,
         top_k: 10,
         include_provenance: bool
       }
       Returns: { results: [{ fact_id, content, score, truth_level, provenance }] }

# Truth-level promotion (admin/validator roles required)
POST   /v1/facts/{id}/promote
       Body: { target_level: "VALIDATED|CANONICAL|PUBLIC", rationale: string }
       Returns: { fact_id, new_truth_level, audit_entry_id }

POST   /v1/facts/{id}/demote
       Body: { target_level: "WORKING|EPHEMERAL", rationale: string }

# Assets
POST   /v1/assets
       Body: multipart/form-data { file, asset_type, project_scope?, metadata: {} }
       Returns: { asset_id, minio_key, tagging: TaggingContract }

GET    /v1/assets/{id}/download  → presigned MinIO URL

# Ingestion (bulk, for tools and Drive sync)
POST   /v1/ingest
       Body: {
         source: { type: "drive|scraper|calendar|manual", ref: string },
         documents: [{ content, asset_ref?, metadata: {} }],
         default_truth_level: "EPHEMERAL"
       }
       Returns: { job_id, status: "queued" }

# Audit
GET    /v1/audit?entity_id=uuid&action=promote&from=ISO&to=ISO
       Returns: paginated audit log entries, scoped to caller's team

# Org/Team Admin (admin role required)
GET    /v1/teams
POST   /v1/teams
GET    /v1/teams/{id}/members
POST   /v1/teams/{id}/members
```

### LibreChat → memory-api Integration

LibreChat does not have a native "call external memory API on every turn" hook. The integration path is:

**Path A (recommended): LibreChat Custom Endpoint + Memory Agent**
LibreChat supports a `memory` configuration block in `librechat.yaml` that runs a memory agent concurrently with every chat response. The agent reads/writes to a configurable endpoint. Configure `memory.agent.provider` to point to a custom endpoint that wraps memory-api:

```yaml
# librechat.yaml
endpoints:
  custom:
    - name: "xbrain-memory-proxy"
      apiKey: "${MEMORY_API_KEY}"
      baseURL: "http://memory-api:8000/v1/librechat-compat"
      headers:
        X-Team-ID: "{{LIBRECHAT_USER_TEAM_ID}}"   # requires custom user field
        X-User-ID: "{{LIBRECHAT_USER_ID}}"
      models:
        default: ["memory-agent"]

memory:
  disabled: false
  tokenLimit: 3000
  personalize: true
  messageWindowSize: 10
  agent:
    provider: "xbrain-memory-proxy"
    model: "memory-agent"
```

Memory-api exposes a `/v1/librechat-compat` OpenAI-compatible shim that accepts LibreChat memory agent calls and persists them as conversation records with `truth_level=EPHEMERAL`.

**Path B (Phase 1 fallback): RAG API bridge**
LibreChat's built-in RAG API (`RAG_API_URL`) accepts document embeddings and returns relevant context. Point this to a memory-api adapter endpoint (`/v1/rag-compat`) that queries Qdrant scoped by team_id extracted from the auth token. This is simpler but does not write facts back.

**What LibreChat NEVER does:** write directly to PostgreSQL, Qdrant, or Neo4j. It calls memory-api endpoints only.

### Open WebUI → memory-api Integration

Open WebUI Pipelines are OpenAI-API-compatible Python plugins running as a sidecar service. A `xbrain-memory` pipeline:

1. Intercepts every message via a Filter Pipeline before it reaches the LLM
2. Calls `POST /v1/retrieve` on memory-api with the message as query, injecting `team_id` from `__user__` context (Open WebUI passes `__user__` as a reserved argument to pipeline functions)
3. Prepends retrieved context to the system prompt
4. After the LLM response (post-filter), calls `POST /v1/conversations` on memory-api with the full turn

```python
# apps/openwebui/pipelines/xbrain_memory.py (outline)
class Pipeline:
    async def inlet(self, body: dict, __user__: dict) -> dict:
        team_id = __user__.get("team_id")  # injected by Open WebUI reserved arg
        context = await memory_api.retrieve(
            query=last_message(body),
            team_id=team_id,
            truth_levels=["WORKING", "VALIDATED", "CANONICAL"]
        )
        body["messages"] = prepend_context(body["messages"], context)
        return body

    async def outlet(self, body: dict, __user__: dict) -> dict:
        await memory_api.store_conversation(body, team_id=__user__["team_id"])
        return body
```

**What Open WebUI NEVER does:** query Qdrant directly, write to PostgreSQL directly.

### agent-runtime (LangGraph) → memory-api

LangGraph agents use memory-api as their external store. They do not use LangGraph's built-in InMemorySaver for cross-session persistence — instead:

- **Checkpointing**: LangGraph `AsyncPostgresSaver` writes graph state to PostgreSQL (same instance, dedicated `langgraph_checkpoints` schema). This is the only case where agent-runtime touches PostgreSQL directly — and it is only graph execution state, not domain data.
- **Domain data (facts, retrievals)**: agents call memory-api via HTTP tool calls. A `store_memory_tool` and `retrieve_memory_tool` are registered as LangGraph tools backed by memory-api endpoints.
- **Human-in-the-loop**: LangGraph `interrupt()` pauses the graph at a node (e.g., truth-level promotion approval). The interrupt payload surfaces in memory-api's `POST /v1/facts/{id}/promote` queue as a pending approval; a human actor approves via the admin UI, which calls `graph.invoke(Command(resume=True))`.

```python
# apps/agent-runtime/tools/memory_tools.py (outline)
@tool
async def store_fact(content: str, fact_type: str, confidence: float) -> dict:
    """Store a fact in the xbrain memory layer."""
    return await memory_api_client.post("/v1/facts", json={
        "content": content,
        "fact_type": fact_type,
        "confidence": confidence,
        "truth_level": "WORKING",
        "source": {"type": "agent", "ref_id": current_run_id()}
    })

@tool
async def retrieve_context(query: str, modalities: list[str]) -> list[dict]:
    """Retrieve relevant memory from xbrain."""
    return await memory_api_client.post("/v1/retrieve", json={
        "query": query,
        "modalities": modalities,
        "truth_levels": ["WORKING", "VALIDATED", "CANONICAL"]
    })
```

### MCP Gateway Design

The `mcp-gateway` service is a FastAPI application that:

1. **Registers tools** at startup by loading a tool manifest from each internal service (scraper, calendar, deck-service, drive-sync). Each tool registers via `POST /internal/tools/register` with its schema, auth requirements, and team_id scope.

2. **Exposes a discovery endpoint** for MCP clients: `GET /v1/tools` returns the tool catalog filtered by the caller's team_id (from JWT).

3. **Injects auth context** on every tool invocation: the gateway validates the JWT, extracts `{team_id, user_id, org_id}`, and forwards these as headers (`X-Team-ID`, `X-User-ID`, `X-Org-ID`) to the target internal service. Internal services trust these headers only from the gateway (network-level isolation via Docker network).

4. **Tool invocation flow**:
```
LLM agent calls tool via MCP
    → mcp-gateway validates JWT, extracts team context
    → mcp-gateway routes to internal service (e.g., scraper)
    → internal service executes, calls memory-api POST /v1/facts (tagging enforced)
    → result returned to mcp-gateway
    → mcp-gateway returns result to agent
    → Langfuse trace recorded (tool call + result)
```

5. **Auth for gateway itself**: Bearer JWT (same issuer as memory-api). Phase 1 uses a shared secret (HMAC-SHA256). Phase 2+ should switch to a proper JWT issuer (Keycloak or a lightweight OIDC server like Casdoor — both OSS).

```
# Tool registration schema (internal, not exposed to LLM)
POST /internal/tools/register
{
  "tool_id": "scraper.fetch_url",
  "display_name": "Web Scraper — Fetch URL",
  "description": "Fetches and extracts content from a URL",
  "input_schema": { ... JSONSchema ... },
  "endpoint": "http://scraper:8001/fetch",
  "team_scope": "any",   # "any" = available to all teams, or ["team-uuid-1"]
  "requires_approval": false
}

# Discovery (MCP client calls this)
GET /v1/tools
Headers: Authorization: Bearer <jwt>
Returns: filtered tool list for caller's team_id

# Invocation
POST /v1/tools/{tool_id}/invoke
Headers: Authorization: Bearer <jwt>
Body: { "input": { ... } }
```

---

## Tagging Contract — Concrete Schema

### PostgreSQL Tables

```sql
-- Organisations and teams
CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE teams (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, slug)
);

CREATE TABLE team_members (
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL,  -- FK to auth/users table
    role        TEXT NOT NULL CHECK (role IN ('member', 'validator', 'admin')),
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE projects (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, slug)
);

-- Core tagging contract (applied as a composite type on every data entity)
-- Rather than embedding all 7 fields on every table, use a tagging_metadata JSONB
-- column that conforms to this validated structure, backed by a CHECK constraint.

-- Truth level enum
CREATE TYPE truth_level AS ENUM (
    'EPHEMERAL',
    'WORKING',
    'VALIDATED',
    'CANONICAL',
    'PUBLIC'
);

CREATE TYPE visibility_level AS ENUM (
    'team',   -- only visible to team members
    'org',    -- visible to all teams in the org
    'public'  -- visible to all (requires truth_level >= CANONICAL)
);

CREATE TYPE validation_status AS ENUM (
    'pending',
    'approved',
    'rejected',
    'contested'
);

-- Facts table (core knowledge entity)
CREATE TABLE facts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Tagging contract fields
    team_scope          UUID NOT NULL REFERENCES teams(id),
    project_scope       UUID REFERENCES projects(id),           -- nullable = team-wide
    visibility          visibility_level NOT NULL DEFAULT 'team',
    confidence          NUMERIC(4,3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    truth_level         truth_level NOT NULL DEFAULT 'EPHEMERAL',
    source_type         TEXT NOT NULL,                          -- 'conversation'|'tool'|'agent'|'human'|'drive'
    source_ref          UUID,                                   -- FK to conversations.id or tool_runs.id etc.
    validation_status   validation_status NOT NULL DEFAULT 'pending',
    -- Content fields
    content             TEXT NOT NULL,
    fact_type           TEXT NOT NULL CHECK (fact_type IN ('assertion', 'preference', 'task', 'entity', 'rule', 'relationship')),
    embedding_id        UUID,                                   -- reference to Qdrant point id
    graph_node_id       TEXT,                                   -- Neo4j node id
    -- Metadata
    created_by          UUID NOT NULL,                         -- user_id or agent_id
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    version             INT NOT NULL DEFAULT 1,
    is_active           BOOLEAN NOT NULL DEFAULT true          -- soft delete
);

CREATE INDEX facts_team_scope_idx ON facts(team_scope);
CREATE INDEX facts_truth_level_idx ON facts(truth_level);
CREATE INDEX facts_project_scope_idx ON facts(project_scope) WHERE project_scope IS NOT NULL;
CREATE INDEX facts_source_ref_idx ON facts(source_type, source_ref);
CREATE INDEX facts_active_team_truth ON facts(team_scope, truth_level) WHERE is_active = true;

-- Conversations table
CREATE TABLE conversations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_scope          UUID NOT NULL REFERENCES teams(id),
    project_scope       UUID REFERENCES projects(id),
    visibility          visibility_level NOT NULL DEFAULT 'team',
    confidence          NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    truth_level         truth_level NOT NULL DEFAULT 'EPHEMERAL',
    source_type         TEXT NOT NULL DEFAULT 'conversation',
    source_ref          UUID,
    validation_status   validation_status NOT NULL DEFAULT 'pending',
    -- Conversation-specific fields
    frontend            TEXT NOT NULL,                          -- 'librechat'|'openwebui'|'chatgpt'|'claudecode'
    model               TEXT NOT NULL,
    messages            JSONB NOT NULL DEFAULT '[]',
    created_by          UUID NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX conversations_team_scope_idx ON conversations(team_scope);
CREATE INDEX conversations_truth_level_idx ON conversations(truth_level);

-- Truth-level promotion state machine
CREATE TABLE truth_level_transitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('fact', 'conversation', 'asset')),
    entity_id       UUID NOT NULL,
    from_level      truth_level NOT NULL,
    to_level        truth_level NOT NULL,
    initiated_by    UUID NOT NULL,                              -- user_id
    rationale       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by     UUID,                                       -- user_id of reviewer
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT valid_promotion CHECK (
        -- Enforce promotion direction (no skipping levels)
        (from_level = 'EPHEMERAL'  AND to_level IN ('WORKING', 'EPHEMERAL')) OR
        (from_level = 'WORKING'    AND to_level IN ('VALIDATED', 'EPHEMERAL')) OR
        (from_level = 'VALIDATED'  AND to_level IN ('CANONICAL', 'WORKING')) OR
        (from_level = 'CANONICAL'  AND to_level IN ('PUBLIC', 'VALIDATED')) OR
        (from_level = 'PUBLIC'     AND to_level = 'CANONICAL')  -- demotion only
    )
);

CREATE INDEX tlt_entity_idx ON truth_level_transitions(entity_type, entity_id);
CREATE INDEX tlt_pending_idx ON truth_level_transitions(status) WHERE status = 'pending';

-- Audit log (append-only, never updated or deleted)
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_type      TEXT NOT NULL CHECK (actor_type IN ('user', 'agent', 'system')),
    actor_id        UUID NOT NULL,
    team_scope      UUID NOT NULL REFERENCES teams(id),
    action          TEXT NOT NULL,   -- 'create_fact'|'promote'|'demote'|'ingest'|'retrieve'|etc.
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    before_state    JSONB,
    after_state     JSONB,
    metadata        JSONB NOT NULL DEFAULT '{}'
);

-- Audit log is write-only from the application; no updates, no deletes
-- Enforce via row-level security or application-level contract
CREATE INDEX audit_log_team_scope_idx ON audit_log(team_scope, occurred_at DESC);
CREATE INDEX audit_log_entity_idx ON audit_log(entity_type, entity_id);

-- Assets
CREATE TABLE assets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_scope          UUID NOT NULL REFERENCES teams(id),
    project_scope       UUID REFERENCES projects(id),
    visibility          visibility_level NOT NULL DEFAULT 'team',
    confidence          NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    truth_level         truth_level NOT NULL DEFAULT 'EPHEMERAL',
    source_type         TEXT NOT NULL DEFAULT 'human',
    source_ref          UUID,
    validation_status   validation_status NOT NULL DEFAULT 'pending',
    -- Asset-specific fields
    asset_type          TEXT NOT NULL,   -- 'pdf'|'image'|'deck'|'dataset'|'generated'
    minio_bucket        TEXT NOT NULL,
    minio_key           TEXT NOT NULL,
    file_name           TEXT NOT NULL,
    file_size_bytes     BIGINT,
    mime_type           TEXT,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Qdrant Collection Design

Each team gets a dedicated Qdrant collection (not shared with other teams). This is the correct isolation approach for team-scoped vector search — cross-team searches are impossible by design at the storage layer.

```
Collection naming: xbrain_{team_id}_facts
Collection naming: xbrain_{team_id}_conversations

Point payload (stored alongside the vector):
{
  "fact_id": "uuid",          # FK back to PostgreSQL facts.id
  "truth_level": "WORKING",
  "project_scope": "uuid|null",
  "fact_type": "assertion",
  "confidence": 0.85,
  "created_at": "ISO8601",
  "source_type": "conversation"
}

Retrieval filter example (team scope is implicit via collection name):
{
  "filter": {
    "must": [
      { "key": "truth_level", "match": { "any": ["WORKING", "VALIDATED", "CANONICAL"] } }
    ],
    "should": [
      { "key": "project_scope", "match": { "value": "project-uuid" } },
      { "key": "project_scope", "is_null": true }   # team-wide facts
    ]
  }
}
```

### Neo4j Graph Schema (Phase 3)

```cypher
// Nodes
(:Entity {id: uuid, name: string, entity_type: string, team_scope: uuid, truth_level: string})
(:Fact   {id: uuid, content: string, team_scope: uuid, truth_level: string})
(:Agent  {id: uuid, name: string, agent_type: string})
(:User   {id: uuid, name: string, team_scope: uuid})
(:Document {id: uuid, asset_id: uuid, title: string, team_scope: uuid})

// Relationships
(:Fact)-[:ASSERTS]->(:Entity)
(:Fact)-[:DERIVED_FROM]->(:Conversation)
(:Fact)-[:DERIVED_FROM]->(:Document)
(:Fact)-[:CONFLICTS_WITH]->(:Fact)
(:Fact)-[:VALIDATED_BY]->(:User)
(:Agent)-[:EXTRACTED]->(:Fact)
(:Entity)-[:RELATED_TO {relationship_type: string, confidence: float}]->(:Entity)

// Lineage
(:Fact)-[:SUPERSEDES]->(:Fact)   // when a fact is updated via Memstate
```

---

## Data Flows — Concrete Sequences

### Flow 1: User Types Message in LibreChat → Memory Stored → Facts Extracted

```
1. User types message in LibreChat
   │
2. LibreChat memory agent (runs concurrently with LLM call):
   ├── GET stored memory → calls memory-api GET /v1/retrieve
   │   (queries Qdrant collection xbrain_{team_id}_facts, truth_level >= WORKING)
   ├── Receives top-k relevant facts
   └── Injects facts as system context into the LLM call
   │
3. LibreChat sends conversation turn to LLM (Claude/GPT/Grok via their APIs)
   │
4. LLM responds
   │
5. LibreChat memory agent (post-response):
   └── POST /v1/conversations on memory-api
       Body: { messages: [user_turn, assistant_turn], model, frontend: "librechat" }
       memory-api stores with truth_level=EPHEMERAL, team_scope from JWT
       memory-api embeds messages in Qdrant (xbrain_{team_id}_conversations collection)
   │
6. memory-api emits event to agent-runtime (webhook POST or queue poll):
   "new_conversation: {conversation_id}"
   │
7. LangGraph ingestion agent triggers:
   ├── Reads conversation from memory-api GET /v1/conversations/{id}
   ├── Calls Memori extraction → identifies facts, tasks, entities, preferences
   ├── For each extracted item:
   │   └── POST /v1/facts on memory-api
   │       Body: { content, fact_type, confidence, truth_level: "WORKING",
   │               source: { type: "conversation", ref_id: conversation_id } }
   │       memory-api:
   │         - writes to PostgreSQL facts table
   │         - embeds in Qdrant (xbrain_{team_id}_facts collection)
   │         - creates Neo4j nodes (Phase 3 only)
   └── Agent records trace in Langfuse (tool_call: extract_facts, input/output)
   │
8. Result: conversation at EPHEMERAL, extracted facts at WORKING with provenance
   pointing back to conversation_id
```

### Flow 2: Internal Scraper Writes → Workflow Promotes to VALIDATED → CANONICAL

```
1. Scraper service fetches external data (triggered by schedule or MCP tool call)
   │
2. Scraper calls memory-api POST /v1/ingest
   Body: {
     source: { type: "scraper", ref: "run-uuid" },
     documents: [{ content: "...", metadata: { url, scraped_at } }],
     default_truth_level: "WORKING"   # scraper output starts at WORKING
   }
   memory-api:
   - writes to facts table with truth_level=WORKING, source_type="tool"
   - embeds in Qdrant
   - records in audit_log
   │
3. Admin reviews new WORKING facts in Open WebUI (admin pipeline lists pending items)
   │
4. Admin clicks "Promote to VALIDATED" in Open WebUI:
   Open WebUI pipeline calls memory-api POST /v1/facts/{id}/promote
   Body: { target_level: "VALIDATED", rationale: "Verified against primary source" }
   memory-api:
   - inserts into truth_level_transitions (status=pending)
   - since actor has 'validator' role: auto-approves → updates facts.truth_level
   - writes audit_log entry with before/after state
   - updates Qdrant payload (truth_level field updated in-place)
   │
5. (Later) Admin or automated agent promotes VALIDATED → CANONICAL:
   POST /v1/facts/{id}/promote { target_level: "CANONICAL", rationale: "..." }
   - Same flow, but requires 'admin' role
   - Neo4j node gets :CANONICAL label (Phase 3)
   │
6. All team members and agents now see fact when querying truth_levels >= CANONICAL
```

### Flow 3: User Asks a Question → Scoped Multi-Store Retrieval → LLM with Context

```
1. User sends query in LibreChat: "What do we know about Project Alpha's Q3 results?"
   │
2. LibreChat memory agent pre-retrieval (runs before LLM):
   POST /v1/retrieve on memory-api
   Body: {
     query: "Project Alpha Q3 results",
     modalities: ["semantic", "graph", "event"],
     truth_levels: ["WORKING", "VALIDATED", "CANONICAL", "PUBLIC"],
     project_scope: "alpha-project-uuid",   # if in project context
     top_k: 15,
     include_provenance: true
   }
   │
3. memory-api executes scoped retrieval:
   ├── Semantic (Qdrant): vector search in xbrain_{team_id}_facts
   │   with filter { truth_level in [...], project_scope match }
   │   Returns top 10 semantically similar facts
   ├── Graph (Neo4j, Phase 3): Cypher query for entities related to "Project Alpha"
   │   and their relationships, filtered by team_scope
   │   Returns entity graph subgraph (5 nodes)
   └── Event (PostgreSQL): recent conversations and audit events
       for the project_scope, ordered by recency
       Returns last 5 relevant events
   │
4. memory-api ranks and deduplicates results (truth_level weighting:
   CANONICAL > VALIDATED > WORKING; higher confidence scores ranked higher)
   Returns merged result set with provenance
   │
5. LibreChat prepends retrieved context to system prompt:
   "[Memory context — team-scoped, truth-level: VALIDATED]
    Fact: Project Alpha Q3 revenue was €2.3M (confidence: 0.95, source: scraper run 2026-04-15)
    Fact: Q3 target was €2.0M (confidence: 1.0, source: canonical doc)
    ..."
   │
6. LLM (Claude/GPT/Grok) generates response with grounded context
   │
7. LLM response → LibreChat
   Conversation turn stored (step 5 of Flow 1)
   Langfuse trace records: retrieve latency, result count, LLM call
```

### Flow 4: Drive Sync → Ingestion → Indexed Memory

```
1. drive-sync service (Phase 3) polls Google Drive for new/modified documents
   (using OAuth2 token stored per-team in PostgreSQL, refreshed automatically)
   │
2. For each document:
   drive-sync downloads file → uploads to MinIO via memory-api POST /v1/assets
   drive-sync extracts text (pdf-parse / Google Docs API)
   │
3. drive-sync calls memory-api POST /v1/ingest
   Body: {
     source: { type: "drive", ref: "drive-file-id" },
     documents: [{ content: text_content, asset_ref: asset_id, metadata: { title, url } }],
     default_truth_level: "WORKING"
   }
   │
4. LangGraph document ingestion agent triggered:
   - Chunks document (512 token chunks, 50 token overlap)
   - Embeds each chunk via embedding model (Ollama local or OpenAI API)
   - Writes each chunk as a fact via POST /v1/facts
   - Links document → fact → source_asset in Neo4j (Phase 3)
   │
5. Facts available for retrieval at truth_level=WORKING
   Admin can promote entire document's facts to VALIDATED in batch
```

---

## Build Order — Phase 1 / 2 / 3

### Phase 1 — Infra Socle + Frontends (memory-api stub)

**Services running in Docker Compose:**
- Nginx (reverse proxy, TLS via Let's Encrypt or self-signed for local)
- LibreChat (conversation UI, multi-model)
- Open WebUI (admin/RAG interface)
- PostgreSQL (LibreChat DB + xbrain base schema migrations)
- Qdrant (LibreChat RAG, xbrain Phase 2 prep)
- **memory-api Phase 1 stub** (FastAPI, minimal surface)
- Langfuse (observability from day 1)

**memory-api Phase 1 stub surface (minimum to be meaningfully different from "just LibreChat"):**

The stub must implement these endpoints so that conversations are stored with team scope from day 1, and basic RAG retrieval works:
```
POST /v1/conversations          # store turn with team_scope from JWT
POST /v1/retrieve               # semantic only (Qdrant), no graph, no event
GET  /v1/conversations/{id}
POST /v1/assets                 # for file uploads
GET  /v1/rag-compat             # LibreChat RAG API shim → Qdrant
GET  /v1/health
POST /auth/token                # simple JWT issuance (email+password, Phase 1 only)
```

The stub omits: facts table, promotion workflow, audit log, ingest pipeline, MCP gateway.

**What Phase 1 delivers that vanilla LibreChat does not:**
- All conversations tagged with team_scope from day 1 (no retroactive migration needed)
- RAG retrieval scoped by team (not a shared blob store)
- Multi-model (Claude + GPT + Grok) configured in LibreChat in one deployment
- Observability via Langfuse from first message
- Open WebUI as admin interface running alongside

**Phase 1 service dependency graph:**
```
PostgreSQL ←── LibreChat
            ←── memory-api
Qdrant     ←── LibreChat (RAG)
            ←── memory-api
Langfuse   ←── memory-api (traces)
            ←── LibreChat (optional, via OTEL)
Nginx      ←── LibreChat
            ←── Open WebUI
            ←── memory-api
```

**Contracts exposed by Phase 1 that Phase 2 depends on:**
- `POST /v1/conversations` — Phase 2 agents query this to trigger ingestion
- `GET /v1/conversations/{id}` — agents read conversation content
- JWT auth schema — same token format used throughout all phases
- Qdrant collection naming convention — `xbrain_{team_id}_{type}`

### Phase 2 — Memory Intelligence + Agents

**New services added:**
- `agent-runtime` (LangGraph server, FastAPI wrapper)
- Remembra integration (wired through memory-api, not standalone)
- Memstate integration (fact versioning layer in memory-api)
- Full facts schema + truth-level promotion workflow activated in memory-api
- MinIO (asset storage, replaces Phase 1 local file handling)

**memory-api new endpoints in Phase 2:**
```
POST /v1/facts
GET  /v1/facts
GET  /v1/facts/{id}
POST /v1/facts/{id}/promote
POST /v1/facts/{id}/demote
POST /v1/ingest
GET  /v1/audit
POST /v1/retrieve              # extended: adds event store modality
```

**LangGraph agents launched in Phase 2:**
- `conversation-ingestion-agent` — triggers on new conversation, extracts facts via Memori
- `fact-conflict-detector-agent` — runs nightly, compares WORKING facts for contradictions
- `approval-workflow-agent` — manages human-in-the-loop promotion approvals

**Phase 2 service dependency graph:**
```
Phase 1 services (all still running)
PostgreSQL ←── agent-runtime (LangGraph checkpoints via AsyncPostgresSaver)
            ←── memory-api (facts, truth-levels, audit)
Qdrant     ←── memory-api (fact embeddings)
MinIO      ←── memory-api (asset uploads)
agent-runtime ←── memory-api (domain data only, all via HTTP)
Langfuse   ←── agent-runtime (agent traces)
```

**Contracts exposed by Phase 2 that Phase 3 depends on:**
- `POST /v1/facts` with full tagging contract — Phase 3 tools use this
- `POST /v1/ingest` — drive-sync and scraper use this
- MCP tool invocation contract — Phase 3 adds real tools

**Note on Remembra / Memstate / Memori maturity:**
- Remembra (13 stars, v0.13.2, SQLite backend) — LOW maturity. Use its concepts (entity resolution, temporal decay), but implement the core logic in memory-api directly backed by PostgreSQL + Qdrant. Do not depend on its SQLite backend in production.
- Memstate — `langchain-memstate` is a SaaS API (memstate.ai), NOT self-hostable. Use the versioning concept but implement in memory-api's `truth_level_transitions` table instead.
- Memori (MemoriLabs, 14k stars, v3.3.2) — High maturity, but primary deployment is cloud API. The `BYODB` mode may work self-hosted. Run a 1-day POC before committing. Fallback: implement extraction logic via LangGraph + LLM structured output (equally effective).

### Phase 3 — Graph + Extraction + Integrations

**New services added:**
- Neo4j (knowledge graph, lineage, entity relationships)
- `drive-sync` service (Google Drive OAuth2, polling, ingestion)
- `scraper` service (registered as MCP tool)
- `calendar` service (registered as MCP tool)
- `deck-service` (registered as MCP tool)
- `mcp-gateway` (tool registry + auth context injection)

**memory-api new in Phase 3:**
```
POST /v1/retrieve       # extended: graph modality now active (Neo4j queries)
POST /v1/ingest         # triggers Neo4j node creation alongside PostgreSQL + Qdrant
```

**Phase 3 service dependency graph:**
```
Phase 1 + 2 services (all still running)
Neo4j       ←── memory-api (entity/fact nodes, relationships)
            ←── agent-runtime (lineage queries via memory-api)
mcp-gateway ←── scraper, calendar, drive-sync, deck-service (tool registration)
            ←── agent-runtime (tool discovery + invocation)
            ←── LibreChat agents (tool discovery + invocation)
drive-sync  ←── memory-api POST /v1/ingest
            ←── MinIO (via memory-api POST /v1/assets)
scraper     ←── memory-api POST /v1/ingest
```

---

## Recommended Monorepo Structure

```
/apps
  /memory-api               # FastAPI — the central data layer
    /api                    #   route handlers (conversations, facts, retrieve, etc.)
    /services               #   business logic (retrieval orchestration, tagging enforcement)
    /models                 #   SQLAlchemy models (facts, conversations, assets, audit)
    /migrations             #   Alembic migration files
    /embeddings             #   embedding provider abstraction (Ollama / OpenAI / local)
    main.py
    Dockerfile
  /agent-runtime            # LangGraph agents as FastAPI server
    /agents                 #   graph definitions (ingestion, conflict, approval)
    /tools                  #   memory_tools.py, search_tools.py (all call memory-api)
    /checkpointing          #   AsyncPostgresSaver config
    main.py
    Dockerfile
  /mcp-gateway              # FastAPI — MCP tool registry and proxy
    /registry               #   in-memory + DB-backed tool catalog
    /auth                   #   JWT validation, team context injection
    /proxy                  #   HTTP forwarding to internal services
    main.py
    Dockerfile
  /librechat                # LibreChat config files only (image from upstream)
    librechat.yaml          #   custom endpoints, memory config
    docker-compose.override.yml
  /openwebui                # Open WebUI config + custom pipelines
    /pipelines
      xbrain_memory.py      #   Filter + Pipe pipeline for memory integration
    docker-compose.override.yml

/services                   # Internal tools (each is an independent FastAPI service)
  /scraper
    main.py                 #   fetch + extract + call memory-api /ingest
    Dockerfile
  /calendar
    main.py
    Dockerfile
  /drive-sync               # Phase 3
    main.py
    Dockerfile
  /deck-service             # Phase 3
    main.py
    Dockerfile

/packages
  /schemas                  # Shared Pydantic models (TaggingContract, FactCreate, etc.)
    tagging.py
    facts.py
    conversations.py
  /shared-types             # Common types used across services
    truth_levels.py
    auth.py
  /memory-models            # ORM models (importable by memory-api and agents)
  /agent-tools              # Shared tool definitions for LangGraph agents

/infrastructure
  docker-compose.yml        # Base compose (Phase 1 services)
  docker-compose.phase2.yml # Additive: agent-runtime, MinIO
  docker-compose.phase3.yml # Additive: Neo4j, mcp-gateway, drive-sync, scraper
  /nginx
    nginx.conf
    /ssl
  /langfuse
    docker-compose.langfuse.yml   # Langfuse has its own compose, include as override
  /monitoring
    prometheus.yml                # optional scrape config
```

### Structure Rationale

- **`/apps/memory-api` as the spine**: Every other service imports from `/packages/schemas` but only memory-api owns the DB connections. This prevents schema drift.
- **`/packages/schemas` as the contract surface**: All services import `TaggingContract`, `FactCreate`, `TruthLevel` from here. One place to update the contract.
- **`docker-compose.phase{n}.yml` additive pattern**: Phase 1 compose runs standalone. Phase 2 adds `--file docker-compose.phase2.yml`. This avoids one monolithic file and makes phase transitions explicit.
- **Internal tools in `/services/`**: Each is a standalone FastAPI service with its own Dockerfile. They register with mcp-gateway at startup. Adding a new tool = new directory under `/services/`, no changes to core infra.

---

## Architectural Patterns

### Pattern 1: Tagging-at-Write Enforcement

**What:** Every write to memory-api validates and stamps the full 7-field tagging contract before persisting. The API extracts `team_scope` from the JWT (not from the request body) to prevent spoofing.

**When to use:** On every `POST /v1/facts`, `POST /v1/conversations`, `POST /v1/assets`, `POST /v1/ingest`.

**Trade-offs:** Slightly more complex write path, but eliminates the "untagged data" problem permanently. Zero tolerance for partial tagging.

```python
# apps/memory-api/services/tagging.py
def enforce_tagging_contract(body: FactCreate, jwt_claims: dict) -> TaggedFact:
    # team_scope always comes from JWT, never from body
    return TaggedFact(
        team_scope=jwt_claims["team_id"],
        project_scope=body.project_scope,  # nullable
        visibility=body.visibility or "team",
        confidence=body.confidence,
        truth_level=body.truth_level or "EPHEMERAL",
        source_type=body.source.type,
        source_ref=body.source.ref_id,
        validation_status="pending",
        **body.content_fields()
    )
```

### Pattern 2: Truth-Level as Read Filter, Not Just Write Metadata

**What:** Every retrieval endpoint enforces truth_level filtering. Callers request the minimum truth_level they accept. Default for most agents: `["WORKING", "VALIDATED", "CANONICAL"]`. Public-facing queries: `["CANONICAL", "PUBLIC"]` only.

**When to use:** `GET /v1/retrieve`, `GET /v1/facts`, all agent retrieval tool calls.

**Trade-offs:** Requires callers to be explicit about what they trust. More cognitive load for tool authors, but prevents agents from hallucinating based on EPHEMERAL noise.

### Pattern 3: Event Emission for Agent Triggers (not direct agent calls)

**What:** memory-api does not call agents directly (avoids coupling). Instead, it emits events (either via a lightweight queue table in PostgreSQL with a polling agent, or via Redis Pub/Sub in Phase 2). Agents subscribe to relevant event types.

**Why:** memory-api remains stateless from the agent perspective. Agents can be restarted, scaled, or replaced without touching memory-api.

**Phase 1 implementation:** Simple PostgreSQL table `agent_events` with a polling loop in agent-runtime (poll every 5s). No Redis required in Phase 1.

```sql
CREATE TABLE agent_events (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,   -- 'new_conversation'|'new_fact'|'promote_requested'
    entity_id   UUID NOT NULL,
    team_scope  UUID NOT NULL REFERENCES teams(id),
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_by  UUID,            -- agent_run_id that claimed this event
    claimed_at  TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
CREATE INDEX agent_events_unclaimed ON agent_events(event_type, team_scope)
    WHERE claimed_at IS NULL AND completed_at IS NULL;
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Frontend Owning Data

**What people do:** Store conversation history in LibreChat's MongoDB directly and query it from there.

**Why it's wrong:** Creates a second source of truth. Team-scoped retrieval becomes impossible. Adding Open WebUI or ChatGPT API as frontends means data fragmentation.

**Do this instead:** Store in LibreChat's own DB for UI rendering only. Always mirror to memory-api. Treat LibreChat's DB as a display cache, not the source of truth.

### Anti-Pattern 2: Agents Bypassing memory-api to Write Directly to Qdrant or PostgreSQL

**What people do:** LangGraph agent directly instantiates a `QdrantClient` and upserts vectors without going through memory-api.

**Why it's wrong:** Bypasses tagging contract enforcement. No audit log entry. No truth-level assignment. Creates unscoped vectors in Qdrant that poison retrieval.

**Do this instead:** Agents always use `store_fact_tool` and `retrieve_context_tool` which call memory-api via HTTP. The only exception is LangGraph's own `AsyncPostgresSaver` for checkpoint state (not domain data).

### Anti-Pattern 3: Skipping truth_level=EPHEMERAL for Conversations

**What people do:** Store all conversations at `truth_level=WORKING` to simplify retrieval.

**Why it's wrong:** Agents retrieve WORKING facts and treat them as semi-validated. Raw conversation turns are noisy — they contain hypotheses, jokes, and errors. Mixing them with validated facts degrades retrieval quality.

**Do this instead:** Conversations always start at `EPHEMERAL`. Extracted facts start at `WORKING`. Only human-reviewed or agent-validated facts reach `VALIDATED` or above.

### Anti-Pattern 4: One Global Qdrant Collection for All Teams

**What people do:** Use a single `xbrain_facts` Qdrant collection with `team_scope` as a payload filter.

**Why it's wrong:** Payload filters on high-cardinality fields (many teams, many facts) degrade performance. More critically, a bug in the filter logic exposes cross-team data. Per-team collections provide hard isolation at the storage layer.

**Do this instead:** One collection per team per data type: `xbrain_{team_id}_facts`, `xbrain_{team_id}_conversations`. Teams with CANONICAL or PUBLIC facts can be cross-queried by memory-api's retrieval logic (not by direct Qdrant client calls).

### Anti-Pattern 5: Implementing Memstate/Remembra as Hard Dependencies

**What people do:** Depend on `pip install langchain-memstate` and wire it as the versioning layer without a fallback.

**Why it's wrong:** `langchain-memstate` requires a SaaS API key (memstate.ai) — not self-hostable as of research date. Remembra has 13 stars and SQLite backend, unsuitable for production load.

**Do this instead:** Implement versioning concepts (the `truth_level_transitions` table, `facts.version` column, `is_active` soft delete) natively in memory-api. Run a 1-day POC of Memori (MemoriLabs) before Phase 2 to validate BYODB mode. If it works: use it. If not: implement extraction via LangGraph + LLM structured output.

---

## VM Sizing Reality Check

The e2-medium (2 vCPU, 4 GB RAM) is viable for Phase 1 only. Realistic RAM floor by phase:

| Phase | Services | Minimum RAM |
|-------|----------|-------------|
| Phase 1 | LibreChat + Open WebUI + PostgreSQL + Qdrant + memory-api + Langfuse | ~6 GB (tight, needs e2-standard-2 or 6 GB VM) |
| Phase 2 | + MinIO + agent-runtime + LangGraph workers | ~8–10 GB |
| Phase 3 | + Neo4j (minimum 1 GB heap) + mcp-gateway + internal tools | ~12–16 GB |

**Recommendation:** Start with `e2-standard-2` (2 vCPU, 8 GB, ~38€/month) or Railway (scales by usage). Neo4j Community Edition minimum heap is 512 MB but realistically needs 1–2 GB for graph queries. Qdrant is memory-efficient (HNSW index fits in ~1 GB for 1M vectors at 1536 dims). PostgreSQL + LibreChat + Open WebUI together need ~2 GB.

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1–5 teams, ~50 users | Current Docker Compose on single VM. All Phase 3 in scope. |
| 5–20 teams, ~200 users | Scale Qdrant collections (still single node, HNSW handles it). Move Langfuse to separate VM. PostgreSQL connection pooling via PgBouncer. |
| 20+ teams, 1000+ users | Separate VMs for DB tier. Qdrant distributed mode. LangGraph workers horizontal scaling. Consider moving to Kubernetes (k3s on GCP). |

**First bottleneck (Phase 2):** LangGraph agent workers competing for PostgreSQL connections during ingestion spikes. Fix: PgBouncer connection pool + async agent queue.

**Second bottleneck (Phase 3):** Neo4j read latency for complex lineage queries. Fix: read replicas (Community Edition is single-node only; need Enterprise for HA or switch to FalkorDB).

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Google Drive | OAuth2 per-team tokens, stored in PostgreSQL, drive-sync polls API | Tokens refreshed via service account in Phase 3 |
| Anthropic API | LibreChat custom endpoint | Key per model config in librechat.yaml |
| OpenAI API | LibreChat custom endpoint + memory-api agent calls | GPT-4o for fact extraction |
| Grok API | LibreChat custom endpoint | X.AI API, OpenAI-compatible |
| Embedding providers | memory-api abstraction layer | Ollama (local) Phase 1, OpenAI embeddings Phase 2 |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| LibreChat ↔ memory-api | HTTP REST via memory custom endpoint + RAG shim | LibreChat passes JWT from user session |
| Open WebUI ↔ memory-api | HTTP REST via Python Pipeline (`__user__` context injection) | Pipeline accesses team_id from Open WebUI user profile |
| agent-runtime ↔ memory-api | HTTP REST (tool calls) | Agents get JWT from env var (service account token) |
| agent-runtime ↔ PostgreSQL | Direct (LangGraph AsyncPostgresSaver, checkpoint schema only) | Only checkpoint state, not domain data |
| mcp-gateway ↔ internal tools | HTTP REST (forwarded with injected X-Team-ID header) | Internal Docker network, no external exposure |
| internal tools ↔ memory-api | HTTP REST | Service account JWT per tool service |
| memory-api ↔ Qdrant | Qdrant Python client | Per-team collections, payload filters |
| memory-api ↔ Neo4j | neo4j Python driver (Phase 3) | Bolt protocol, team_scope property filter on all queries |
| memory-api ↔ MinIO | MinIO Python SDK | Presigned URLs for asset downloads |

---

## Sources

- LibreChat Memory Feature docs: https://www.librechat.ai/docs/features/memory
- LibreChat Custom Endpoints: https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint
- LibreChat RAG API: https://www.librechat.ai/docs/configuration/rag_api
- Open WebUI Pipelines: https://docs.openwebui.com/features/extensibility/pipelines/
- LangGraph human-in-the-loop interrupt: https://docs.langchain.com/oss/javascript/langgraph/interrupts
- Langfuse self-hosting: https://langfuse.com/self-hosting
- Langfuse OTLP endpoint: https://langfuse.com/integrations/native/opentelemetry
- Neo4j LangGraph integration: https://neo4j.com/labs/genai-ecosystem/genai-frameworks/langgraph/
- Qdrant REST API (search + filter): https://api.qdrant.tech/api-reference/points/get-points
- MCP multi-tenant governance: https://dev.to/kuldeep_paul/mcp-access-governance-across-teams-tenants-and-third-party-integrations-1ike
- MCP gateway registry OSS: https://github.com/agentic-community/mcp-gateway-registry
- Remembra GitHub: https://github.com/remembra-ai/remembra (13 stars, v0.13.2, SQLite-backed — LOW maturity for production)
- Memori GitHub: https://github.com/MemoriLabs/Memori (14k stars, v3.3.2, BYODB mode — HIGH maturity, verify self-host)
- Memstate: SaaS API only (memstate.ai) — NOT self-hostable, implement concepts natively

---

*Architecture research for: xbrain — collective AI memory platform, multi-frontend, team-scoped*
*Researched: 2026-05-02*

# xbrain

> Self-hostable, MCP-native memory backend for teams of humans and AI agents.
> The open-source engine behind [GrooveOS](https://grooveos.app).

xbrain is a single memory plane. Every human and every agent reads and writes the same
team brain through one FastAPI service (`memory-api`). Each record carries a tagging
contract — team scope, project scope, visibility, confidence, truth level, source, and
validation status — enforced at the API boundary. Storage is split across PostgreSQL
(system of record), Qdrant (vector search), and Neo4j (entity graph). Retrieval is
always team-scoped and truth-filtered.

---

## Overview

Teams increasingly mix human members and AI agents (Claude, Cursor, ChatGPT, Grok,
Granola, …). Each tool keeps its own private memory and none of them share state, so
context is lost at every handoff — human↔human, human↔agent, agent↔agent — and each
session starts from nothing.

xbrain centralizes that state. Every decision, message, document, and tool output is
written through one API (`memory-api`), which enforces a tagging contract and stores
the record in a shared, searchable brain. Any member, agent, or tool retrieves it
scoped to its team and filtered by truth level.

```
Claude Code · Cursor · ChatGPT · Grok · LibreChat · Open WebUI · Chrome Clipper · Granola
                                   │
                          ┌────────▼────────┐
                          │   memory-api    │   ← tagging contract enforced here
                          └────────┬────────┘
              ┌────────────────────┼────────────────────┐
        Postgres (truth)     Qdrant (vectors)      Neo4j (graph)
```

## Tagging contract

Every record carries 7 fields, enforced at the API boundary:

| Field | Purpose |
|-------|---------|
| `team_scope` | hard isolation between teams |
| `project_scope` | optional project partition |
| `visibility` | who can read it |
| `confidence` | 0..1 |
| `truth_level` | `EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC` |
| `source` | provenance (which human, agent, or tool produced it) |
| `validation_status` | promotion / review state |

Validation is bidirectional: humans promote agent output up the truth ladder; agents
flag inconsistencies. Retrieval is always team-scoped and truth-filtered — for example,
a reply can be restricted to the team's `CANONICAL` records.

## Architecture

- **Multi-frontend** — LibreChat, Open WebUI, ChatGPT (API), and Claude Code all read
  and write the same memory through `memory-api`; storage is never bound to a single
  frontend.
- **MCP-native** — an MCP gateway exposes the brain (memory search/add, tasks,
  contacts, calendar, drive, decks) to any MCP-capable client. The same brain is
  also reachable as an OAuth 2.1 **remote connector** (`mcp-brain`), so the official
  Claude.ai app and other OAuth-capable MCP clients connect directly — each connection
  bound to a single team.
- **Temporal knowledge graph** — Neo4j + Graphiti track how entities and facts evolve.
- **Self-hostable, 100% OSS** — no managed-cloud-only services in the critical path.
  Runs on a single VM via Docker Compose.

**Stack:** LibreChat · Open WebUI · FastAPI `memory-api` · PostgreSQL · Qdrant · Neo4j ·
MCP gateway + sidecars · session-bridge · Centrifugo (realtime) · Langfuse
(observability) · LangGraph agents.

## Quickstart (self-host)

Requirements: an Ubuntu VM with Docker + Docker Compose, a Google OAuth client, and a
GitHub App (for auth). Everything runs from one `docker compose`.

```bash
git clone https://github.com/mrboups/xbrain.git
cd xbrain
cp .env.example .env
# Fill every __FILL__ placeholder (secrets, OAuth, GitHub App). Generate randoms with:
#   openssl rand -base64 48   # 64-char secrets
#   openssl rand -hex 32      # 64-hex secrets
make env-check                # verify no critical secret is missing
```

Deploy:

```bash
make deploy     # build + docker compose up -d on the VM
make vm-ps      # confirm all containers are healthy
make vm-logs    # tail logs if something fails to start
```

Develop & verify:

```bash
make test       # memory-api tests (pytest + testcontainers)
make lint       # ruff across the Python services
make help       # list all targets
```

## Repo layout

```
apps/                  # Python services
  memory-api/          # FastAPI core — the single memory plane + tagging contract
  agent-runtime/       # LangGraph agents (HITL, extraction, team-chat @claude)
  mcp-gateway/         # aggregates MCP tools for all frontends
  mcp-*/               # brain / calendar / drive / deck / scraper MCP sidecars
  session-bridge/      # routes chat to a user's own model session
  librechat-bridge/    # LibreChat ↔ memory-api
  openwebui-pipeline/  # Open WebUI ↔ memory-api
  drive-sync/ granola-sync/ graphiti-service/ brain-janitor/
packages/memory-models/  # MemoryProvider contract + native/mem0 backends
infrastructure/          # docker-compose.yml, nginx, deploy scripts
chrome-extension/        # web clipper + team chat
app-site/ marketing-site/  # GrooveOS web + docs
```

## Status

Production. Twelve phases shipped: core memory + tagging contract, intelligent memory +
agents (HITL), graph + extraction + integrations, MCP consolidation, team platform
(GitOps + Chrome extension), marketing site + docs, CRM/Granola/tasks, universal
extraction pipeline, session bridge, GitHub-primary auth, brain monitor + soft-delete,
and GitHub App migration. The team brain is also connectable to the **official
Claude.ai app** as an OAuth 2.1 Custom Connector — see the
[Claude Connector guide](https://grooveos.app/docs/claude-connector.html).

## Links

- **Hosted product:** [grooveos.app](https://grooveos.app)
- Architecture & constraints: [`CLAUDE.md`](./CLAUDE.md)
- Roadmap & planning: [`.planning/`](./.planning/)

## License

MIT.

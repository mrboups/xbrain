# xbrain

> **Shared cognition for teams of humans and AI agents.**
> The open-source, MCP-native memory engine behind [GrooveOS](https://grooveos.app).

xbrain is **not a chatbot workspace**. The differentiator is the layer underneath: a
single memory plane where every human and every agent reads and writes the **same
team brain**, tagged by team, project, and **truth level**. If everything else breaks,
that contract holds.

---

## The problem

Hybrid teams — humans plus AI agents working together — are becoming the default, but
they have no shared mind. Context is lost at every handoff: human→human, human→agent,
agent→agent. Every session starts at zero. Every tool (Claude, Cursor, ChatGPT, Grok,
Granola…) keeps its own private memory, and none of them talk to each other.

## The idea

Every decision, message, document, and shipped output flows through **one API**
(`memory-api`) that enforces a strict tagging contract and lands in a shared,
searchable brain. Any member, agent, or tool can then retrieve it — scoped to their
team and filtered by how trustworthy the information is.

```
Claude Code · Cursor · ChatGPT · Grok · LibreChat · Open WebUI · Chrome Clipper · Granola
                                   │
                          ┌────────▼────────┐
                          │   memory-api    │   ← tagging contract enforced here
                          └────────┬────────┘
              ┌────────────────────┼────────────────────┐
        Postgres (truth)     Qdrant (vectors)      Neo4j (graph)
```

## The differentiator: the tagging contract

Every data point carries **7 fields**, enforced at the boundary:

| Field | Purpose |
|-------|---------|
| `team_scope` | hard isolation between teams |
| `project_scope` | optional project partition |
| `visibility` | who can read it |
| `confidence` | 0..1 |
| `truth_level` | `EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC` |
| `source` | provenance (which human, agent, or tool produced it) |
| `validation_status` | promotion / review state |

Validation flows **both directions**: humans promote agent outputs up the truth
ladder; agents flag inconsistencies. Retrieval is always scoped and truth-filtered, so
a chat reply can be enriched with only the team's `CANONICAL` facts.

## Architecture

- **Multi-frontend by construction** — LibreChat, Open WebUI, ChatGPT (API), and
  Claude Code all read/write the same memory. Logic that locks data to one frontend is
  wrong by design.
- **MCP-native** — an MCP gateway exposes the brain (memory search/add, tasks,
  contacts, calendar, drive, decks) to any MCP-capable client.
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
and GitHub App migration.

## Links

- **Hosted product:** [grooveos.app](https://grooveos.app)
- Architecture & constraints: [`CLAUDE.md`](./CLAUDE.md)
- Roadmap & planning: [`.planning/`](./.planning/)

## License

MIT.

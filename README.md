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

- **Multi-frontend** — the Chrome extension, the installable PWA, LibreChat, Open
  WebUI, ChatGPT and Claude.ai all read and write the same memory through
  `memory-api`; storage is never bound to a single frontend. The extension and the
  PWA are one chat: both compile from `packages/chat-core`.
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

Requirements: an Ubuntu VM with Docker + Docker Compose, and **one optional LLM key**
(Anthropic OR OpenAI OR Grok) for chat. **No Google OAuth client and no GitHub App are
required** — local email/password auth is the default (Phase 18) and embeddings run
locally, keyless (Phase 19). A zero-external-key install boots the full brain.

```bash
git clone https://github.com/mrboups/xbrain.git
cd xbrain
make oss-init   # writes a bootable zero-external-key .env (CSPRNG secrets, no key to paste)
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
make ps         # confirm all core services are healthy
```

Register the first account — no OAuth needed (reach memory-api through the nginx
`api.<XBRAIN_BASE_DOMAIN>` vhost; with the `localhost` default that is `api.localhost`):

```bash
curl -X POST http://api.localhost/v1/auth/local/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-strong-passphrase"}'
```

See [`docs/INSTALL.md`](./docs/INSTALL.md) for the full walkthrough (prereqs → provision
→ generate secrets → boot → register → verify → real-deploy notes), all with zero
external keys.

### Deploy

- **OSS-light (single host, zero external keys) — the default.** Build and run the core
  directly on the target host; this matches `docs/INSTALL.md` and works with the
  `make oss-init` zero-key `.env`:
  ```bash
  docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
  make ps         # confirm all core services are healthy
  ```
- **`make deploy` — the SaaS/hosted-team remote path.** Rsyncs the repo to a separate
  `VM_HOST` over SSH and runs `docker compose build && up` **on that VM**
  (build-on-VM), gated by `make env-check` + `make preflight`. `env-check` requires the
  saas credentials only under `COMPOSE_PROFILES=saas`, so it passes a zero-key core
  deploy but still guards a saas one. Use it only when deploying to a remote VM:
  ```bash
  make deploy     # env-check + preflight + rsync + build-on-VM + up
  make vm-ps      # confirm containers on the VM are healthy
  make vm-logs    # tail VM logs
  ```

### Release artifacts (OSS-light)

The reproducible OSS-light release artifact is a **bundle, not a registry image**
(SC#4 / D-16-06):

- the light compose file `infrastructure/docker-compose.yml` (`COMPOSE_PROFILES` unset =
  the 10-service core);
- the corrected `.env.example` + the `make oss-init` zero-key secret generator;
- [`docs/INSTALL.md`](./docs/INSTALL.md);
- the zero-key-safe deploy path — `docker compose --env-file .env up -d --build` on the
  target host, so images are **built on the correct architecture** — with `make deploy`
  as the SaaS/hosted-team remote variant.

Explicitly deferred:

- **Registry-hosted multi-arch image publishing + CI → Phase 17.** The intended future
  path (not built here) is
  `docker buildx build --platform linux/amd64,linux/arm64 --push`.
- **The standalone web app + in-extension zero-key sign-in UI → Phase 20.**
- **A fresh amd64-VM clean-install run → a documented follow-up.** The dev host is
  arm64, so the automated proof is the local arm64 `docker compose up` proxy (D-16-04).
  **Never build a local image on arm64 and deploy it cross-arch to an amd64 VM** — build
  on the target host (or via `buildx`) instead.

### Develop & verify

```bash
make test       # memory-api tests (pytest + testcontainers)
make lint       # ruff across the Python services
make help       # list all targets
```

## Repo layout

```
apps/
  memory-api/          # FastAPI core — the single memory plane + tagging contract
  agent-runtime/       # LangGraph agents (HITL, extraction)
  mcp-gateway/         # aggregates MCP tools for all frontends
  mcp-*/               # brain / calendar / deck / drive-read / github / scraper sidecars
  session-bridge/      # routes chat to a user's own model session
  librechat/           # our LibreChat fork (built here, not pulled)
  librechat-bridge/    # LibreChat ↔ memory-api
  openwebui-pipeline/  # Open WebUI ↔ memory-api
  board-web/           # Excalidraw + Yjs board SPA (Vite/React, built in Docker)
  hocuspocus/          # Yjs WebSocket server behind the board
  drive-sync/ granola-sync/ graphiti-service/ brain-janitor/
packages/
  memory-models/       # MemoryProvider contract + native/mem0 backends
  chat-core/           # shared chat client — the ONLY editable copy (see `make check-client`)
infrastructure/          # docker-compose.yml, nginx templates, deploy + verify scripts
chrome-extension/        # web clipper + team chat
app-site/                # GrooveOS web (account, teams, /join/) + app/ = the installable PWA
marketing-site/          # public site + docs
```

## Status

Production. **Twenty-seven phases shipped**, 2026-05-03 → 2026-08-01:

- **v1.0 (phases 1-13)** — core memory + tagging contract, intelligent memory + agents
  (HITL), graph + extraction + integrations, MCP consolidation, team platform
  (GitOps + Chrome extension), marketing site + docs, CRM/Granola/tasks, universal
  extraction pipeline, session bridge, GitHub-primary auth, brain monitor +
  soft-delete, GitHub App migration, chat→brain ingestion + retrieval enrichment.
- **v2.0 Open-Core (phases 14-20)** — portability, edition mechanics
  (`COMPOSE_PROFILES` + `EDITION`), native email/password auth, keyless local
  embeddings, the 10-service OSS-light package, the shadcn chat restyle, and CI
  lockstep. Shipped 2026-07-19.
- **Since (phases 21-27)** — configurable agent mention aliases, push-a-link,
  catch-me-up, document body extraction, team join-by-code, the collaborative
  Excalidraw board, and the installable PWA with web push.

The team brain is also connectable to the **official Claude.ai app** as an OAuth 2.1
Custom Connector — see the
[Claude Connector guide](https://grooveos.app/docs/claude-connector.html).

## Links

- **Hosted product:** [grooveos.app](https://grooveos.app)
- Architecture & constraints: [`CLAUDE.md`](./CLAUDE.md)
- Roadmap & planning: [`.planning/`](./.planning/)

## License

MIT.

> Note (open item): the code license is under review for the open-core edition —
> the locked open-core design records AGPLv3 + CLA, which the `LICENSE` file above
> does not yet reflect. This is flagged for the maintainer, not resolved here.

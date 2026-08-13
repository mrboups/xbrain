# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**In production.** 27 phases have shipped (Phase 1 on 2026-05-03 through Phase 27 on 2026-08-01), deployed on a GCP VM via Docker Compose. Concretely, as of 2026-08-13:

- `apps/` holds 19 services plus one spike; `infrastructure/docker-compose.yml` defines **34 services** across four opt-in profiles.
- `apps/memory-api` is the single write path — FastAPI + SQLAlchemy, **alembic head `0034_team_message_private_lane`**.
- `.planning/phases/` carries **28 directories** — 01 → 27, plus the inserted 03.5.

**Vision (one line):** xbrain is a collective persistent-memory system for humans + agents organised by team — not a chatbot workspace. Differentiator is the memory + truth-level + team-scope layer, not the frontend.

`.planning/PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, and `STATE.md` are the authoritative planning sources — prefer them over this file. When they disagree with the code, **the code wins and the planning file is stale**; say so rather than working from it.

## Workflow: GSD is the build system

Development flows through GSD slash commands, not ad-hoc edits. The hooks in `.claude/settings.json` (workflow guard, phase boundary, commit validation, read-before-edit guard) enforce this — they fire on `Write`/`Edit`/`Bash` tool calls. Bypassing them defeats the point.

Entry points (run in order on a fresh project):

1. `/gsd:new-project` — initialises `.planning/` (PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md). Use `--auto @docs/idea.md` to skip the wizard when the brief is already written.
2. `/gsd:plan-phase <N>` — produces a sub-plan for phase N before any code is touched.
3. `/gsd:execute-phase <N>` — runs the sub-plan with verification gates and atomic commits per task.

`/gsd:help` lists the full 65-command surface. Common adjuncts: `/gsd:resume-work`, `/gsd:progress`, `/gsd:debug`, `/gsd:review`, `/gsd:undo`.

The hooks only activate at session start — after editing `.claude/settings.json` or installing new hooks, the Claude Code session must be reopened for changes to take effect.

## Constraints carried over from kickoff

- **Open-source + self-hostable only.** Deployment is a GCP VM (Ubuntu 24.04) running Docker Compose. The `e2-medium` (4 GB) baseline in the kickoff notes was left behind at Phase 2 — the VM has run on `e2-standard-2` (8 GB) since then (see `.planning/STATE.md`, "OOM Risk Phase 1" under Resolved). No managed-cloud-only services.
- **Multi-frontend assumed** — LibreChat, Open WebUI, ChatGPT (via API), and Grok all read/write the same memory layer. Logic that locks data to one frontend is wrong by construction.
- **Every data point carries the tagging contract** — `team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level` (EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC), `source`, `validation_status`. New schemas without these fields should be flagged.
- **Don't start coding on architecture/scope messages.** Confirm understanding and wait for explicit go-ahead before scaffolding.

## Commands

Everything is a `make` target (`Makefile` at the repo root; `make help` lists them all). The ones that matter:

| Command | What it does |
|---------|--------------|
| `make test` | `pytest -v` in `apps/memory-api`. Some tests need Docker (testcontainers) and are `-m integration`. |
| `make lint` / `make fmt` | `ruff check` / `ruff format` across memory-api, librechat-bridge, openwebui-pipeline. |
| `make build` / `make up` / `make down` / `make ps` / `make logs` | Local `docker compose -f infrastructure/docker-compose.yml --env-file .env …`. |
| `make check-client` | The client-side gate: chat-core drift + PWA shell-cache name + the extension's node test suite. **Run it after touching `packages/chat-core`.** |
| `make sync-chat-core` | Regenerates the two byte-identical copies of `packages/chat-core` (extension + PWA). `packages/chat-core` is the ONLY editable copy. |
| `make oss-init` | Writes a bootable zero-external-key `.env` (CSPRNG secrets). |
| `make env-check` / `make preflight` | Pre-deploy guards on `.env`. `preflight` is the one `make deploy` also runs against the VM's own `.env`. |
| `make deploy` | env-check + preflight + rsync + build-on-VM + up. **Remote path only** — see README for why a local build must never be shipped to the VM. |
| `make verify-phase{15,16,17,18,26,27}` | Per-phase acceptance gates. |

Migrations run themselves: alembic upgrades to head when memory-api boots.

**Two of these targets have destroyed state before, so read before running either on the VM:** `make sync`/`make deploy` rsyncs over the VM's `.env` unless `--exclude='.env'` is in `RSYNC` (this is the mechanism behind the "7 vars vanished" incident — the exclusion is load-bearing, never drop it); and `verify-phase16`/`15`/`26` run `docker compose down -v` from a `trap … EXIT` cleanup while the compose volumes are not `external: true`. `.planning/AUDIT-2026-08-06.md` has both in full.

## Language

- **Conversation with the user:** French. Reply in French unless the content is purely technical or they switch to English.
- **Product / app / code:** English **only**. All user-facing UI strings, button labels, popup copy, error messages, logs, comments, identifiers, and documentation in the product itself MUST be in English — including the Chrome extension, the PWA, LibreChat custom labels, memory-api responses, MCP tool descriptions. The extension's legacy French strings were migrated; the rule now maintains a clean state rather than describing a pending cleanup.
- **Planning artifacts (`.planning/`):** English. They are shared with subagents and other tools that assume English. **Not yet true of the older files** — `PROJECT.md`, `ROADMAP.md` and parts of `STATE.md`/`REQUIREMENTS.md` are French from the 2026-05 kickoff, as are the `Makefile` comments. Write new material in English; do not add French to these files.

<!-- GSD:project-start source:PROJECT.md -->
## Project

**xbrain — AI Cognitive OS**

Plateforme open-source de **mémoire collective persistante** pour humains + agents, organisée par équipe et par projet. Toute donnée (chats, faits extraits, documents, sorties d'outils internes) traverse une couche unique — `memory-api` — qui applique un contrat de tagging strict : team-scope, truth-level, provenance, validation. Frontends multiples (LibreChat / Open WebUI / ChatGPT API / Claude Code) et agents LangGraph lisent et écrivent **la même mémoire**.

Ce n'est pas un workspace de chatbot. Le différenciateur est la couche mémoire + truth-level + team-scope, pas l'interface.

**Core Value:** **Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.** Si tout le reste plante, ce contrat doit tenir.

### Constraints

- **Tech stack** (révisée après research) : LibreChat + Open WebUI + **mem0** + LangGraph + Qdrant + Neo4j + PostgreSQL + MinIO (image Chainguard) + Langfuse — **Pourquoi :** stack 100 % OSS auto-hébergeable. Memstate.ai (SaaS fermé), Remembra (13★ + SQLite, immature) et Memori (Alpha) ont été retirés au profit de mem0 + memory-api natif après vérification : voir Key Decisions ci-dessous.
- **Déploiement** : GCP VM Ubuntu 24.04, Docker Compose — **Pourquoi :** budget contraint, ops simple, pas d'expertise Kubernetes requise.
  - **Résolu.** Le plan de sizing échelonné (`e2-medium` → `e2-standard-2` → `e2-standard-4`) appartient à l'histoire : la VM tourne sur **`e2-standard-2` (8 GB) depuis la Phase 2**, le projet GCP est provisionné depuis la Phase 1, et le stack déployé compte ~33 conteneurs. `e2-standard-4` n'a jamais été nécessaire — Langfuse est passé derrière le profil `integrations` à la place.
- **Open-source uniquement** : aucun service managé propriétaire dans le chemin critique — **Pourquoi :** auto-hébergeable, pas de lock-in, contrôle complet de la donnée (sensibilité multi-team).
- **Multi-frontend invariant** : LibreChat + Open WebUI + ChatGPT (API) + Claude Code lisent/écrivent la même mémoire — **Pourquoi :** l'équipe utilise déjà ces outils en pratique. Imposer un frontend unique ferait échouer l'adoption.
- **Contrat de tagging obligatoire** : 7 champs minimum sur chaque donnée — **Pourquoi :** invariant qui rend possibles l'isolation team, la promotion truth-level, l'audit, le retrieval scopé. C'est le différenciateur.
- **Multi-modèle** : Claude (coding/archi), GPT (reasoning/summary), Grok (second avis) — **Pourquoi :** chaque modèle a un rôle distinct. La plateforme doit pouvoir en ajouter (futur Mistral, Gemini, etc.) sans refactor.
- **Performance** : pas de SLA strict en v1, mais l'expérience LibreChat doit rester fluide (< 2s pour une réponse simple, retrieval mémoire < 500ms en P95) — **Pourquoi :** UX d'équipe.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

> **Hand-corrected 2026-08-13 against `infrastructure/docker-compose.yml`.** The
> generator's source (`.planning/research/STACK.md`) is the 2026-05-02 pre-build
> research and still recommends Remembra / Memori / Memstate — none of which was
> ever built (zero occurrences under `apps/`). If this block is ever regenerated,
> re-apply these corrections or fix the research file first.

Every version below is the tag actually pinned in the compose file. `xbrain/*`
images are built from this repo (`apps/<name>/Dockerfile`); everything else is
pulled.

### The OSS-light core — 10 services, no profile, zero external keys

| Service | Image | Role |
|---------|-------|------|
| `nginx` | `nginx:1.27-alpine` | Ingress. Port 80 only — TLS terminates in front of it. |
| `postgres` | `postgres:17` | System of record: memory items, teams, audit log, truth levels. |
| `qdrant` | `qdrant/qdrant:v1.17.1` | Vector store. Team-scope + truth-level filtering at retrieval. |
| `memory-api` | `xbrain/memory-api:phase2` | FastAPI. The single write path and the tagging contract. Runs alembic at boot. |
| `minio` | `cgr.dev/chainguard/minio` | Object storage for uploads (Docker Hub's `minio/minio` was discontinued Oct 2025). |
| `mcp-gateway` | `xbrain/mcp-gateway:phase4` | Aggregates the MCP sidecars for every frontend. |
| `mcp-scraper` | `xbrain/mcp-scraper:phase3` | URL → text. |
| `mcp-brain` | `xbrain/mcp-brain:phase8` | The remote MCP server + OAuth 2.1 resource server (Claude.ai / ChatGPT connector). |
| `centrifugo` | `centrifugo/centrifugo:v6` | Realtime broker for the team chat. |
| `brain-janitor` | `xbrain/brain-janitor:phase11` | Daily cron: hard-purges soft-deleted rows past the 30-day window. |

### Opt-in profiles — `COMPOSE_PROFILES`

| Profile | Services | Notes |
|---------|----------|-------|
| `integrations` | `agent-runtime`, `neo4j` (`neo4j:2026.04.0-community`), `graphiti-service`, `langfuse` + `langfuse-worker` (`:3`) + `langfuse-clickhouse` (`clickhouse/clickhouse-server:24.8`) + `langfuse-redis` (`redis:7-alpine`), `searxng`, `drive-sync`, `granola-sync`, `mcp-drive-read`, `mcp-calendar`, `mcp-deck`, `mcp-github` | Graph, observability, external sync, the non-core MCP sidecars. ~+4 GB RAM. |
| `saas` | `librechat` (`xbrain/librechat:phase8g` — a **fork**, built from `apps/librechat`), `librechat-mongo` (`mongo:7`), `librechat-meili` (`getmeili/meilisearch:v1.10`), `librechat-bridge`, `openwebui` (`ghcr.io/open-webui/open-webui:v0.9.0`), `openwebui-pipeline`, `session-bridge` | Also requires `EDITION=saas`, or the session-bridge routes 404. |
| `board` | `board` (`xbrain/board-web:phase26`), `hocuspocus` (`xbrain/hocuspocus:phase26`) | Collaborative Excalidraw board (Phase 26a). ~+320 MB. |
| `ops` | `xbrain-backup` (`xbrain/backup:phase1`) | The nightly backup, and nothing else. Off by default, so a core install backs up nothing — see `docs/INSTALL.md` §10. |

`EDITION` accepts exactly `oss` or `saas` and fails fast on anything else
(`app/config.py`). **There is no `pro` edition** — the paid self-host tier and its
Ed25519 licence were dropped by locked decision Q6 (requirement `EDIT-03`).

### Memory + agent libraries (memory-api / agent-runtime)

| Library | Where | Why |
|---------|-------|-----|
| `fastembed` (ONNX, no torch) | memory-api | Phase 19: keyless local embeddings, `EMBEDDINGS_PROVIDER=local`, model `BAAI/bge-small-en-v1.5`. OpenAI stays selectable. |
| `qdrant-client` | memory-api, brain-janitor | Two services, one Qdrant. brain-janitor pins `==1.17.1`; memory-api floats. |
| `mem0ai` | memory-api | Optional `Mem0Provider` behind the `MemoryProvider` interface, **lazy-imported**. The live backend is `MEMORY_BACKEND=native`. |
| `langgraph` >= 1.1 | agent-runtime | Agent graphs with a Postgres checkpointer. |
| `neo4j` driver | memory-api | Graph traversal + lineage (`/v1/graph/*`), `integrations` profile only. |
| `authlib` | 11 services | Bridge-JWT verify, the Centrifugo token mint, the OAuth AS. **Pinned `>=1.3,<2.0.0` everywhere and it must stay that way** — authlib 2.0 removes `authlib.jose`, so an unbounded resolve takes the entire auth layer down on the next rebuild, in every service at once. |
| `PyJWT[crypto]` | memory-api | GitHub App RS256 JWT signing (Phase 12). |

### Never built, despite what older docs say

`Remembra`, `Memori` and `Memstate` appear in the 2026-05-02 research and in
`.planning/REQUIREMENTS.md`'s Out-of-Scope table. They were rejected before any
code was written — Memstate is cloud-only, Remembra was immature, Memori
self-declared Alpha — and replaced by `mem0` behind `MemoryProvider` plus the
native truth-level state machine in memory-api. Do not reintroduce them.

Also out: LangGraph Platform / LangSmith (proprietary), Pinecone, OpenAI
Assistants persistence, Notion as a source of truth.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Patterns the codebase already enforces. Breaking one is a review finding, not a style preference.

- **Comments say WHY, at length.** The density is deliberate: module docstrings explain the decision and what it costs, not what the code does. Match it — a change with a non-obvious reason and no comment reads as an accident to the next agent.
- **Routes authorize; repos do not.** `app/repos/*` carries no authZ (see the header of `repos/team_messages.py`). Membership and team-scope gating live in the route, through the existing helpers: `_require_bridge_principal` (`routes/boards.py`), `get_membership` (`repos/teams.py`), `_resolve_team_and_check_membership` (`routes/team_chat.py`). Reuse them rather than inlining a fourth variant.
- **Security predicates are required keyword-only arguments with no default.** `viewer_user_id` on the `team_messages` reads is the model: a default is a filter that fails open, and a caller that forgets it must fail to import rather than return everything.
- **Request bodies are `extra="forbid"`** (45 sites). This is why the server always deploys before a client: an unknown field is a 422 on every send, not a silently ignored one.
- **Every admin mutation writes an audit row** through `write_audit` in `app/audit.py` (52 call sites). A new mutation without one is incomplete.
- **Migrations are additive and forward-only, and never branch on `EDITION`** — asserted by `tests/test_migration_editions.py::test_no_migration_branches_on_edition`, which also upgrades to head under both editions.
- **`packages/chat-core` is the ONLY editable copy** of the shared client code. `chrome-extension/chat_core/` and `app-site/app/chat_core/` are generated, byte-identical copies. Edit the package, then `make sync-chat-core`; `make check-client` is the gate.
- **Derived values are computed, never hand-bumped.** `app-site/app/sw.js`'s `CACHE` name is a hash of the files it precaches (`scripts/shell-cache.mjs`); hand-bumping it was missed three times in three days.
- **Background work is fire-and-forget, and that is currently a bug source.** memory-api makes 33 `create_task` calls; only 2 keep a reference, and there is no task set and no `add_done_callback` anywhere. CPython may garbage-collect a running task, and the symptom is "the agent didn't answer" with nothing logged. Keep a module-level set for anything new.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

One write path, many surfaces. Everything below is in this repo.

```
Chrome extension · PWA (/app/) · LibreChat · Open WebUI · Claude.ai + ChatGPT (MCP)
                                   │
                          ┌────────▼────────┐
                          │   memory-api    │  tagging contract + team_scope + truth levels
                          └────────┬────────┘
        ┌──────────────┬───────────┼───────────┬──────────────┐
   PostgreSQL      Qdrant        MinIO      Centrifugo      Neo4j
   (of record)   (vectors)     (blobs)     (realtime)    (graph, opt-in)
```

- **`apps/memory-api`** — FastAPI. Routes in `app/routes/`, data access in `app/repos/`, business logic in `app/services/`, ORM in `app/models/`, settings in `app/config.py`. Alembic migrations in `alembic/versions/`, head `0034`.
- **Frontends never own data.** The Chrome extension, the PWA at `app-site/app/`, `apps/librechat-bridge` and `apps/openwebui-pipeline` are clients. `packages/chat-core` is the shared client library the extension and the PWA both compile from.
- **Agents** live in `apps/agent-runtime` (LangGraph, Postgres checkpointer) and in `memory-api`'s own `services/team_chat_agent.py` for the in-chat `@agent` path.
- **Tools are MCP servers, not frontend plugins.** `apps/mcp-gateway` aggregates the `apps/mcp-*` sidecars; `apps/mcp-brain` is additionally an OAuth 2.1 resource server so Claude.ai and ChatGPT connect as remote MCP clients.
- **Realtime** is Centrifugo. Two channel namespaces with very different semantics: `team:<team_id>` keeps 100 frames for 7 days with `force_recovery`, so a wrong publish there is replayed to every member for a week; `user:<sub>` is 50 frames / 24h, no forced recovery, and is **cross-team** — a frame on it must carry `team_id` or it renders into the wrong thread.
- **Isolation is the product.** `team_scope` (A ≠ B) is the invariant everything else rests on — and the 2026-08-06 audit found it **not enforced on 12 routes**, so treat it as the thing to check rather than the thing to assume. Within a team, the chat is shared by design: every member sees every member's messages, and there is no 1:1 privacy. The one exception is the brain tag (migration 0034), which keeps a message out of the chat surface while leaving it in the team's brain.
- **Deletes are soft, on a 30-day window**, on every brainable entity; `brain-janitor` hard-purges Postgres + Qdrant + Neo4j past it.
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

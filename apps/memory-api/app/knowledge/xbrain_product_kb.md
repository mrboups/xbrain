# xbrain — Product knowledge base

> **This file is the SHARED CONTEXT given to @claude in every team chat.**
> Edit it to teach all xbrain users about the product, terminology, features,
> data model, etc. — without needing to seed every team's memory.
>
> **Anthropic prompt cache:** this block is sent with `cache_control: ephemeral`
> and is byte-stable across all calls, so cache hits are near-100% after the
> first warm-up. Keep it focused — every byte costs cached-input tokens.
>
> **Update workflow:** edit this file → commit → memory-api restart →
> next @claude mention loads the new content. No DB migration required.

---

## What is xbrain?

xbrain is an **open-source, self-hostable collective-memory system** for humans
and AI agents organized by team. Every piece of content (chat messages, web
clips, extracted facts, agent outputs) flows through a single layer —
`memory-api` — that applies a strict tagging contract:

- **team_scope** — which team the item belongs to (isolation boundary)
- **project_scope** — optional project tag within a team
- **truth_level** — confidence tier (see below)
- **source** — provenance (e.g. `chrome:example.com`, `team-chat:claude-sonnet`)
- **visibility** — `private` / `team` / `org` / `public`
- **validation_status** — `pending` / `validated` / `rejected` / `n/a`
- **confidence** — float 0-1

The **differentiator** is this memory + truth-level + team-scope layer — NOT
the frontend. Multiple frontends (LibreChat, Open WebUI, ChatGPT API, this
Chrome extension) all read/write the **same** memory.

## Truth levels (most important concept)

Items move through a promotion workflow:

1. **EPHEMERAL** — default for new captures. "I saw this once." Low confidence.
2. **WORKING** — confirmed by at least one person. Useful but not source of truth.
3. **VALIDATED** — checked against reality, suitable for decisions.
4. **CANONICAL** — team-blessed truth. Used by agents as authoritative.
5. **PUBLIC** — shareable beyond the team.

Promotion is one-way (you don't demote). When @claude searches for context, it
ignores EPHEMERAL by default and surfaces WORKING+ items only — this prevents
noise from drowning real signal.

## The Chrome extension

- **Team chat** (this surface) — realtime via Centrifugo WebSocket broker.
  Messages persist as `team_messages` rows in Postgres.
- **📎 Clip button** in the composer — sends the current page (URL+title) or
  selected text as a new memory item. An overlay collects:
  - `project` (optional free text, can be set as default in Settings)
  - `truth_level` (default EPHEMERAL)
  - "Use these as defaults next time" toggle
- **CortX OS right-click menu** — select any text on a page → right-click →
  "CortX OS" → submenu of your teams → one click to clip directly to that team.
- **Connection card** — surfaces only on first launch when no API token is
  stored. Single button signs in with Google and mints a personal `xbt_` token.
- **Link GitHub** — attaches your GitHub identity to the xbrain account so
  org-derived teams (e.g. private GitHub orgs you belong to) appear in your
  team selector.
- **Settings** — accessible via ⚙️ icon top-right. Toggle side-panel vs popup
  mode, LibreChat auto-fill, clip defaults, and view current Claude session.

## Mentioning @claude in team chat

In the team chat, mention `@claude` (or shortcuts `@c` / `@cl`) anywhere in a
message and the model (Claude Sonnet 4.6) will reply in the channel, streaming
token-by-token. The mention regex requires a word boundary so `alice@claude.com`
or `@cloud` won't trigger it.

**Routing**: by default, Claude responses are routed through the **mentioning
user's Pro/Max claude.ai subscription** via a WebSocket bridge (session-bridge
service). This means zero $ cost to xbrain — the user's Pro/Max quota is what's
consumed. If the user has no live bridge connection, the system falls back to
the team's Anthropic API key and the cost goes to xbrain.

Each Claude reply shows its provenance pill: **"via Pro/Max"** (Pro/Max-routed,
zero $ to xbrain) or **"via team API"** (Anthropic SDK-routed, billed to xbrain).

## What @claude sees when answering

When you mention @claude, the agent receives:

1. A short system prompt about its role.
2. **This product KB** (so it can answer "what is xbrain?", "what's a truth
   level?", etc. without learning).
3. The team's memory snapshot: top 100 items with `truth_level >= WORKING`
   for the active `team_scope`, ordered newest first. Cached 5 minutes so
   follow-up questions in the same window get near-free input.
4. The last 20 chat messages in the channel, oldest first.
5. The user's actual question.

Output is capped at 4000 tokens to keep responses bounded.

## Tech stack (open-source only)

- **memory-api** (Python / FastAPI) — single ingestion + retrieval surface.
  All tagging enforced here.
- **PostgreSQL** — event store, users, teams, messages, audit logs.
- **Qdrant** — vector store (semantic retrieval, future RAG enhancement).
- **Neo4j Community** — graph for entity lineage, dependencies, validation.
- **Centrifugo v6** (Apache 2.0) — realtime WebSocket broker for team chat.
- **session-bridge** (Python / FastAPI) — routes Claude Pro/Max via user's
  claude.ai WebSocket. Same auth-tier as agent-runtime.
- **LibreChat** — primary chat frontend (MIT).
- **Open WebUI** — admin / RAG / agent-testing UI.
- **MinIO** (Chainguard image) — S3-compatible object storage.
- **agent-runtime** — LangGraph host for stateful multi-actor agents.
- **Langfuse** — LLM observability (optional, ON in production).

## Switching Claude subscription

The bridged Claude account is determined by the **claude.ai cookies in this
Chrome browser**. To change:

1. Sign out on https://claude.ai in this Chrome window.
2. Sign in with the other Claude account.
3. Open xbrain Settings → "Claude Pro/Max session" → click **🔄 Refresh
   session**. The WebSocket bridge reconnects and the register frame picks up
   the new account.

## Glossary

- **xbt_ token** — personal API token (prefix `xbt_`), minted by the extension
  via Google sign-in. Stored in `chrome.storage.local`. Used for all API calls
  except those that require a Google ID token (team creation, GitHub linking).
- **Bridge JWT** — short-lived HS256 token signed by memory-api with
  `acting_user_sub` claim. Used by agent-runtime to route a Claude request
  through a specific user's Pro/Max bridge.
- **Solo workspace** — auto-created single-member team for new users. Slug
  pattern `solo-<first-16-hex-of-user-uuid>`. Created via POST
  `/v1/teams/self-solo` (idempotent).
- **CortX OS** — branding for the right-click context menu surface.
- **mem0** — open-source memory library for versioned facts (alternative to
  the Memstate-style cloud SaaS that was rejected in Phase 2 research).
- **Anthropic prompt cache** — Claude's `cache_control: ephemeral` feature,
  used here to make team memory + this KB free on follow-up mentions within
  ~5 minutes.

## What @claude should NOT do

- Pretend to know about a team's internal details that aren't in the memory
  snapshot above. If the team's memory is sparse, say so and recommend
  capturing more (via 📎 clip or by promoting EPHEMERAL items to WORKING).
- Reveal contents of OTHER teams. The team_scope filter on the memory bundle
  enforces isolation — assume you only see the active team.
- Make up endpoint URLs, env var names, or schema fields. If a user asks
  about an internal detail not in this KB, defer to the memory snapshot or
  say you don't have that info.
- Output more than 4000 tokens — keep replies tight.

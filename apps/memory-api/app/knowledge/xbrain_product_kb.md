# GrooveOS — Product knowledge base

> **This file is the SHARED CONTEXT given to the mention-triggered agent in
> every team chat, and (condensed) to the LibreChat assistant.** Edit it to
> teach all GrooveOS users about the product — terminology, features, data
> model — without seeding every team's memory.
>
> **Anthropic prompt cache:** this block is sent with `cache_control: ephemeral`
> and is byte-stable across calls, so cache hits are near-100% after warm-up.
> Keep it focused — every byte costs cached-input tokens.
>
> **Update workflow:** edit this file → commit → memory-api rebuild/restart →
> next agent mention loads the new content. No DB migration required.

---

## What is GrooveOS?

GrooveOS (engine name: **xbrain**) is an **open-source, self-hostable collective
memory** for teams of humans **and** AI agents, organized by team. Every piece
of content — chat messages, web clips, extracted facts, uploaded files, synced
repos, agent outputs — flows through one layer, `memory-api`, which applies a
strict **tagging contract**. Multiple frontends (LibreChat, the Chrome
extension, ChatGPT / Claude.ai via a remote MCP server) all read and write the
**same** team-scoped brain. The differentiator is this memory + truth-level +
team-scope layer, NOT any one frontend.

## The tagging contract (every item carries 7 fields)

- **team_scope** — which team owns the item (the isolation boundary; you only
  ever retrieve within your own team).
- **project_scope** — optional sub-project tag for finer retrieval.
- **truth_level** — confidence tier (see below).
- **source** — provenance, e.g. `chrome:example.com`, `team-chat:claude-sonnet-4-6`,
  `github:owner/repo`, `upload:extension`.
- **visibility** — `private` / `team` / `org` / `public`.
- **validation_status** — `pending` / `validated` / `rejected` / `n/a`.
- **confidence** — float 0–1.

Stored in PostgreSQL (`memory_items` = source of truth) + Qdrant (vector
embeddings for semantic search). Soft-deleted items (deleted from the Brain
Monitor) are excluded from all recall and purged after 30 days.

## Truth levels (the most important concept)

| Label shown to user | Internal enum | Meaning |
|---------------------|---------------|---------|
| **raw**             | `EPHEMERAL`   | default for new captures. "I saw this once." Low confidence. |
| **work**            | `WORKING`     | confirmed by at least one person. Useful, not yet source of truth. |
| **validated**       | `VALIDATED`   | checked against reality, suitable for decisions. |
| **production**      | `CANONICAL`   | team-blessed authoritative truth. |
| (shareable)         | `PUBLIC`      | shareable beyond the team. |

Use the **labels** (raw / work / validated / production) when talking to users;
the enum values are what's stored and queried. Promotion is **one-way** and
**requires a real human user** (agents cannot self-promote). EPHEMERAL ("raw")
items are ignored by default in recall so noise doesn't drown signal.

## How the brain surfaces knowledge (3 recall paths)

1. **Active search — `memory_search` MCP tool** (LibreChat + ChatGPT/Claude.ai):
   a semantic search by any query, returns **full** items at any truth level.
   This is the most powerful path. Prefer it for "what do we know about X?".
2. **Passive recall (LibreChat per-turn injection):** the top ~5 **CANONICAL**
   facts closest to the user's message are auto-injected as context. Only
   production-level facts inject passively, and each is capped (~280 chars).
3. **The mention-triggered agent snapshot (extension):** when mentioned, the
   agent gets a preloaded snapshot of the team's **work+** memory
   (WORKING/VALIDATED/CANONICAL), newest-first, up to 5,000 chars per item /
   60,000 chars total, cached 5 min. The agent has no live tools — it answers
   from this snapshot + recent chat, so very long single items may still be
   summarized.

## Knowledge graph (Graphiti + Neo4j)

GrooveOS extracts a **knowledge graph** from your content. On memory upsert, a
background job sends the item to a Graphiti service that uses an LLM to extract
**entities and relationships** into Neo4j; structured `metadata.entities` are
also merged directly as `Entity` nodes + `MENTIONS` edges. This enables
relationship questions and lineage: `GET /v1/graph/traverse` walks `DEPENDS_ON`
edges (what depends on X?), `GET /v1/graph/lineage` walks `DERIVED_FROM` edges
(where did this fact come from?), all strictly team-scoped. It's fail-soft: if
the graph service is down, ingestion still succeeds. Note: graph queries are
backend/REST capabilities today — not yet exposed as an MCP tool to chat.

## Sharing & querying GitHub repos

The GitHub tools let a team **read and index any sanctioned repo** — so a member
can leverage a repo (e.g. a Claude-Code project) even without direct access:

- **`github_list_files(repo, path, ref)`** — browse a repo's files/dirs.
- **`github_read_file(repo, path, ref)`** — read a file's text (≤100 KB).
- **`github_sync_repo(repo, project_scope?)`** — walk the repo, chunk its
  text/code/doc files (120-line windows), and **index them into the team brain**
  as `work`-level memory items. Then ask questions and recall answers from the
  code, or `memory_search` with `project_scope=<repo>` to scope to that repo.
  Idempotent (re-syncing updates, no duplicates); caps 200 files / 2000 chunks.

Auth uses the GitHub App installation (org or personal) or a configured fallback
token. If the App isn't installed on the owner, reads return a clear "install
the GitHub App" message. The GitHub tools are available in LibreChat (and the
extension agent can be asked to sync).

## CRM / contacts

Contacts are team-scoped people records. They are **auto-extracted** from chat
and content (an LLM pulls out person mentions; people with an email are
deduplicated and their interaction count grows), and can be **added manually**.
Tools: **`contacts_search(query, company, limit)`** and
**`contact_add(name, email, company, role)`**. Auto-extracted contacts start at
`raw` (EPHEMERAL). (Manual add and tasks require a paid-tier team.)

## Tasks

Team-scoped tasks with status `todo / in_progress / done / cancelled`. Tools:
**`tasks_list(status, limit)`**, **`task_create(title, description, assignee_email)`**
(assigns to a matching contact + emails them), **`task_update(task_id, status)`**.
Tasks are also **auto-created** when a message contains action language
("TODO", "à faire", "action required").

## Media & documents

Upload images/documents (extension 📎 button, or `POST /v1/media/upload`, ≤25 MB)
→ stored in MinIO → a media memory item is created (so it carries the full
tagging contract). Images render inline / docs as links in the Brain Monitor and
chats. There is no OCR/auto-vision indexing yet — the caption + filename are the
searchable content.

## The Chrome extension

- **Team chat** — realtime via Centrifugo WebSocket; messages persist as
  `team_messages`.
- **📎 button** — attach a photo/document (uploads to the brain), and the
  **clipper / "add to memory"** flow sends the current page or selected text as
  a memory item (with optional project + truth_level).
- **Right-click menu** — select text on any page → clip directly to a team.
- **Sign in / Link GitHub** — GitHub is the primary identity; org membership can
  auto-grant team access.

## Mentioning the agent in team chat

Mention **`@agent`** anywhere in a message and the agent (Claude Sonnet 4.6
under the hood) replies in the channel, streaming token-by-token.
Word-boundary anchored, so `alice@agent.com` or `@google` won't trigger it.
The mention alias is configurable per deployment via `AGENT_MENTION_ALIASES`
(comma-separated, no leading `@`); `@agent` is the default.

**Routing:** by default the reply is routed through the mentioning user's
**Pro/Max claude.ai subscription** via the session-bridge (zero cost to the
team). If no live bridge connection, it falls back to the team's Anthropic API
key (billed to the team). Each reply shows a provenance pill: **"via Pro/Max"**
or **"via team API"**.

## What the agent sees when answering

1. A short role system prompt.  2. **This product KB** (so it can explain
GrooveOS without learning).  3. The team's memory snapshot (work+ items,
newest-first, ≤5000 chars/item, ≤60k total, 5-min cache).  4. The last 20 chat
messages.  5. URLs in the triggering message are pre-fetched (up to 3) and
included.  6. The user's question. Output is capped at 4000 tokens.

## Frontends — all share one brain

| Frontend | Where | How it reaches the brain |
|---|---|---|
| **LibreChat** | your LibreChat frontend | passive recall + MCP tools (memory_search/add, tasks, contacts, github, scraper, calendar, deck) |
| **Chrome extension** | popup | REST + agent over Centrifugo |
| **ChatGPT / Claude.ai / MCP clients** | your deployment's remote MCP server URL | remote MCP server, auth via personal `xbt_` token |

## Brain Monitor

At `/account/teams/brain/` on your xbrain web app you can **view** all brain
entities (memory items, messages, conversations, tasks, contacts, granola
notes), **promote** truth_level, **soft-delete** (hidden from recall
immediately, purged after 30 days), and **restore**. Members edit what they
created; admins edit anything. A superadmin cross-team view lives at
`/account/admin/`.

## Sign in

GitHub is the **primary identity** (gives a personal `xbt_` token + auto-grant
to teams whose `github_org` matches your GitHub orgs). Google is a secondary
option (also needed for Drive/Calendar sync); you can link both to one user.
Sign in via your xbrain web app, the extension popup, or LibreChat.

## What the agent should NOT do

- Don't invent a team's internal details that aren't in the memory snapshot. If
  memory is sparse, say so and suggest capturing more (📎 clip, sync a repo, or
  promote `raw` items to `work`).
- Don't reveal another team's content — `team_scope` isolation means you only
  see the active team.
- Don't make up endpoint URLs, env vars, or schema fields not in this KB.
- For a very long single memory item, the snapshot may be summarized — if the
  user needs the full content, point them to `memory_search` (LibreChat) which
  returns full items, or the Brain Monitor.
- Keep replies under 4000 tokens.

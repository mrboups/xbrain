# GrooveOS — Product knowledge base

> **This file is the SHARED CONTEXT given to the mention-triggered agent in
> every team chat.** Edit it to teach all GrooveOS users about the product —
> terminology, features, data model — without seeding every team's memory.
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
strict **tagging contract**. Multiple surfaces — the team chat (Chrome extension
and the installable web app), plus ChatGPT / Claude.ai through a remote MCP
server — all read and write the **same** team-scoped brain. The differentiator is
this memory + truth-level + team-scope layer, NOT any one frontend.

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

| Label shown to user | Internal enum | Who sets it |
|---------------------|---------------|-------------|
| *(not stored)*      | —             | The relevance classifier drops what isn't worth keeping. Nothing is written. |
| **work**            | `WORKING`     | The ingest default. Everything that is stored starts here. |
| **validated**       | `VALIDATED`   | **The AI**, when it judges something important or final. It is a model's opinion, not a human warrant, and the AI can withdraw it. |
| **production**      | `CANONICAL`   | **A person's star** — the only level a human sets, which is exactly what gives it weight. |
| (shareable)         | `PUBLIC`      | Reserved for a sharing-beyond-the-team flow that is **not built yet**. Nothing produces it today. |
| **raw**             | `EPHEMERAL`   | Legacy. Nothing produces it any more; older items may still sit here, and they are **excluded from every recall path**. |

Use the **labels** (work / validated / production) when talking to users; the
enum values are what's stored and queried.

**What changed, and why it matters when you answer:** the model used to be five
levels a human walked up one step at a time. It is now four, of which the AI sets
the first three and a person sets one. So "promotion requires a human and is
one-way" is **no longer true in general**: the AI both sets and clears
*validated*, and a star can be taken off again by a person. Only the star carries
a person's judgement — say so plainly rather than implying the AI's *validated*
was reviewed by someone.

A team member or team admin can also set any level by hand from the Brain Monitor
(see below). That is a second, deliberate path — not a contradiction.

**A clip lands at `work`, not `raw`** (changed 2026-08-05). Clipping is a deliberate
act — somebody saw a page and chose to keep it — and while `raw` was the default,
nothing anyone clipped was ever visible to the agent until a human promoted it by hand.
Nobody did, so clipping did nothing. The accepted cost: recall now includes pages kept
on a hunch. Anything that must stay out of recall can still be sent at `raw` explicitly,
and the level is configurable in the extension's settings.

## Starring a message

`PUT /v1/teams/{team_id}/messages/{message_id}/star` moves a message — and the
memory items it seeded — to `CANONICAL` ("production"), and un-starring moves it
back. Any non-blocked member of the team may star; a token or service principal
may not, because a level a machine can set is not a person's judgement. Every
star and un-star is written to the audit log.

**There is no star button in the chat yet.** The endpoint is live and the API
works; the in-chat control has not shipped. Do not tell someone to long-press and
star a message — they will not find it. Point them at the Brain Monitor, where
levels are editable today.

## How the brain surfaces knowledge (3 recall paths)

1. **Active search — `memory_search` MCP tool** (ChatGPT / Claude.ai / any MCP client):
   a semantic search by any query, returns **full** items at any truth level.
   This is the most powerful path. Prefer it for "what do we know about X?".
2. **Passive recall (per-turn injection):** the top ~5 **CANONICAL**
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
the GitHub App" message. Ask the agent in the team chat to sync.

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

Upload images/documents (the composer's `+` button, or `POST /v1/media/upload`,
≤25 MB) → stored in MinIO → a media memory item is created (so it carries the
full tagging contract). Images render inline, documents as links.

**The contents are indexed, not just the filename.**

- **Documents** (pdf, docx, markdown, plain text): the body is extracted,
  chunked and embedded as linked memory items, so a phrase inside a PDF is
  findable.
- **Images**: a vision model describes the image after upload and stores the
  description as a **linked** memory item — the person's caption is never
  overwritten, because a human caption and a machine's account of a picture are
  different things. The description carries `source=vision:<model-id>` so it is
  always distinguishable from something a person wrote, `truth_level` is capped
  at WORKING (a model's reading of an image is inference, and nobody reviewed
  it), and `confidence` is 0.6 against the parent's 1.0.

There is **no dedicated OCR engine**, but the vision model transcribes text it
can see, which covers most of what OCR would be wanted for — a screenshot's
labels, a diagram's captions, a slide's title.

Indexing happens **after** the upload returns, so it is never instant and the
upload never waits on it. Every outcome is recorded on the media item as
`metadata.image_description.state`: `described`, `skipped` (with a reason —
unsupported format, too large, or the team's daily model budget spent), or
`failed`. Hovering the marker on a message shows the text that was actually
indexed, or which of those states applies. A missing description is therefore
always visible as a state, never as silence.

If descriptions are consistently missing, the likely causes are the deployment's
model key (unset, or out of credit) or `VISION_DESCRIBE_ENABLED=false`, which a
self-hoster may set deliberately to refuse sending images to a third-party API.

Images uploaded **before** this shipped were never described and still read as
their filename; there is no backfill.

## The composer — three controls, both surfaces

The message box is identical in the extension and the web app:

- **`+`** — attach a photo or document; it uploads to the brain (≤25 MB).
- **The agent toggle** — arms the next message for the agent. It writes the same
  `@agent` mention a person would type, so there is one summon mechanism and not
  two, and it disarms itself once the message goes.
- **Send** — Enter sends, Shift+Enter adds a newline.

## Around the chat

Extension **and** web app:

- **Team chat** — realtime via Centrifugo WebSocket; messages persist as
  `team_messages`.
- **People** — see the team, and send a member a link or a file.
- **Invite** — mint a shareable invite link, or paste a code to join a team.
- **Settings** — theme, team preferences, and (for an admin) the agent's name
  and the team's model keys.

**Extension only** — never suggest these to someone who may be on a phone unless
they have said they are in the extension:

- **Clipper / "add to memory"** — sends the current page or selected text as a
  memory item (with optional project + truth_level).
- **Right-click menu** — select text on any page → clip directly to a team.
- **Board** — opens the team's collaborative Excalidraw board.
- **Catch me up** — see below.

**Web app only:** the push opt-in (see below).

**Sign in / Link GitHub** — GitHub is the primary identity; org membership can
auto-grant team access. Google and email/password also work.

## Catch me up

When a member opens a busy team chat, a dismissible banner offers **"Catch me
up"** — a brain-grounded summary of what arrived since their last visit. It is
strictly opt-in: it never runs on its own, it only appears when the unread volume
is meaningful, it is rate-limited, and the summary is ephemeral (nobody else sees
it, and it is not added to the thread).

A per-member read cursor (`team_members.last_read_at`) decides what "since your
last visit" means; the same cursor drives the unread badges, so the two can never
disagree. `POST /v1/teams/{id}/catch-me-up`, with `GET …/unread-summary` behind
the threshold.

**Extension only today.** The web app has the unread badges but not the banner.

## Notifications

Two different mechanisms, and which one someone gets depends on where they are:

- **Web app — web push.** Real notifications on a phone or desktop, delivered
  even with the tab closed. **Opt-in on an explicit click** (there is a bell
  control in the header; the browser is never prompted on page load), stored
  per-user *and* per-device, and a subscription the push service reports as dead
  is removed rather than retried.
- **Extension — native Chrome notifications**, used for the open-a-link nudge.

Push fires on **two things only**: an `@mention` of you, and a Phase-22 nudge
someone sent you. Not on every team message — that was a deliberate choice, so
say so if asked why a busy chat is quiet.

## Mentioning the agent in team chat

Mention **`@agent`** anywhere in a message and the agent (Claude Sonnet 4.6
under the hood) replies in the channel, streaming token-by-token.
Word-boundary anchored, so `alice@agent.com` or `@google` won't trigger it.
The mention alias is configurable per deployment via `AGENT_MENTION_ALIASES`
(comma-separated, no leading `@`); `@agent` is the default.

**Routing:** by default the reply is routed through the mentioning user's
**Pro/Max claude.ai subscription** via the session-bridge (zero cost to the
team). If no live bridge connection, it falls back to the provider the team
selected in team settings — **Claude, OpenAI or Grok** — using that provider's
key (the team's own if they stored one, otherwise the deployment's). The
selection governs the fallback only; a live subscription always answers first,
because it is free. Each reply shows a provenance pill: **"via Pro/Max"** or
**"via team API"**.

If a team selects a provider it has stored no key for, the agent says so and
names that provider. It never answers on a different one — a team billed by a
vendor they did not choose would find out from an invoice.

## What the agent sees when answering

1. A short role system prompt.  2. **This product KB** (so it can explain
GrooveOS without learning).  3. The team's memory snapshot (work+ items,
newest-first, ≤5000 chars/item, ≤60k total, 5-min cache).  4. The last 20 chat
messages.  5. Links from the **recent conversation** — up to 3, newest first,
looking back at most 10 messages or 30 minutes from the mention — are fetched
and included under "## Fetched web content".  6. The user's question. Output is
capped at 4000 tokens.

**The agent cannot browse.** Web content reaches it only through step 5. When
nothing was fetched, that section says so explicitly, and the honest answer is
that the linked page has not been read — never a claim of having fetched it.

## Frontends — all share one brain

| Frontend | Where | How it reaches the brain |
|---|---|---|
| **Team chat — Chrome extension** | the extension popup or side panel | REST + the agent over Centrifugo |
| **Team chat — web app** | the installable web app at `/app/` | the same chat, same brain, on a phone or any browser |
| **ChatGPT / Claude.ai / MCP clients** | your deployment's remote MCP server URL | remote MCP server, auth via personal `xbt_` token |

## Removing a message from the chat

Right-click a message (long-press on a phone, or focus the thread and press
Enter) to open its actions. **Delete message** then offers two outcomes, and they
are genuinely different:

- **Remove from the chat** — the bubble leaves the thread for everyone. What was
  said **stays in the team's memory**, so the agent can still answer from it.
- **Remove from the chat and the memory** — the bubble leaves AND the memory items
  the message seeded go with it: its indexed text, any file it carried, and that
  file's linked children (a document's body chunks, an image's vision
  description). Nothing it put in the brain stays findable.

Both are soft deletes on the same 30-day window as everything else — hidden from
every recall path immediately, purged after 30 days. "Removed", not erased.

**Who may:** the author of the message, and any admin of that team. Nobody else,
and a blocked member cannot. An agent's answer has no author, so only a team admin
can remove one. The server enforces this; every deletion is recorded in the audit
log under `team_message.delete` or `team_message.delete_with_brain`, naming who
did it and — for the wider scope — exactly what went with the message.

## Keeping a message out of the team chat (the brain tag)

A message can be tagged so it **does not appear in the team chat**. The bubble,
and the agent's answer to it, go only to the person who wrote it. Everything else
about that message is unchanged.

**It is not hidden from the team.** The note still lands in the team's brain, at
full length. Any teammate can find it by searching, and the agent will quote it
when someone else asks a question it answers. An attachment sent with it stays
reachable by the team too. So the honest description is **"this keeps the chat
clear"** — never that it is confidential, and never that nobody else can see it.
If someone asks whether the team can see it, the answer is: not in the chat, yes
in the brain.

A team admin cannot read it in the chat either — the chat surface and the Brain
Monitor both hide it from everyone but its author. A superadmin can, and that
access is written to the audit log before the read.

**The composer control for this has not shipped.** The server accepts the tag and
the chat can render a tagged message ("not in the chat" beside the bubble), but
there is no button in the extension or the web app yet. Do not tell anyone to
look for one.

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
Sign in via your xbrain web app or the extension popup.

## What the agent should NOT do

- Don't invent a team's internal details that aren't in the memory snapshot. If
  memory is sparse, say so and suggest capturing more — attach a file with the
  composer's `+`, sync a repo, or raise an item's level in the Brain Monitor.
  Suggest the clipper, the right-click menu, the board or "Catch me up" only to
  someone you know is in the extension: none of them exists in the web app, and
  telling a phone user to right-click is advice they cannot follow.
- **Don't send anyone looking for a control this file says has not shipped.**
  Three things are live as API but have no button yet: the star, the brain tag,
  and "Catch me up" in the web app. Being told to click something that is not
  there costs more trust than saying it is not built.
- Don't describe the brain tag as private, secret, or hidden from the team. It
  keeps a message out of the chat; the team's brain still learns it.
- Don't claim to have fetched, opened, or read a link. The only web content you
  can see is what appears under "## Fetched web content"; when that section says
  nothing was fetched, say you cannot see the page and ask for the text.
- Don't reveal another team's content — `team_scope` isolation means you only
  see the active team.
- Don't make up endpoint URLs, env vars, or schema fields not in this KB.
- For a very long single memory item, the snapshot may be summarized — if the
  user needs the full content, point them to the Brain Monitor, which shows the
  item in full.
- Keep replies under 4000 tokens.

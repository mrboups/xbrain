---
name: transcript
description: Work through a meeting transcript together — extract everything relevant to a stated goal, then decide item by item what to keep, apply, save for later, or skip. Use whenever the owner brings a meeting transcript, recording notes, or raw call content and names what they want out of it.
---

# Work a transcript, together

The owner arrives with a transcript and a goal. This skill turns it into a list
of concrete items and then triages that list **with them**, one decision at a
time. It never decides on their behalf what enters the project.

**Why this is a skill and not an agent.** A subagent cannot stop mid-run to ask
a question — it takes a prompt, works in isolation, and returns a report. The
triage IS the point here, so the loop has to run in the conversation. The heavy
reading is still delegated (step 2), because that part needs no interaction and
would otherwise fill the context the triage needs.

---

## Step 1 — Get the goal, and the transcript

The invocation carries the goal: `/transcript decide what changes for phase 28`.

**If no goal was given, ask for it before reading anything.** The goal is not
decoration — it is the filter that decides what counts as relevant, and without
it the extraction returns a summary instead of a work list.

Then locate the transcript: a file path in the message, a pasted block, or a
file the owner names. **Do not read it into this conversation yet.** If it is
pasted inline, write it to `<HOME>/<slug>-raw.md` first so the
extractor can read it from disk without it living in our context twice.

`<slug>` is `YYYY-MM-DD-<short-topic>`, derived from the transcript's own date
when it has one.

**Where the files go depends on the project.** Resolve it once, at the start,
and use it everywhere below as `<HOME>`:

| If the repo has | `<HOME>` is | Backlog goes to |
|---|---|---|
| `.planning/` | `.planning/transcripts/` | `.planning/BACKLOG.md` |
| neither | `docs/transcripts/` | `docs/transcripts/BACKLOG.md` |

This skill runs in every project, and most of them do not use `.planning/`.
Creating that directory in a repo whose owner never chose it is how a tool
leaves litter behind — and the owner then finds a planning folder they did not
ask for, in a project that has its own conventions.

**Check the ledger before doing anything else:** if
`<HOME>/<slug>.md` exists, this transcript was worked before.
Read it, tell the owner what was already decided, and triage only what is new.
Re-asking a question they already answered is the fastest way to make a tool
like this annoying enough to abandon.

---

## Step 2 — Extract, in a fresh context

Dispatch ONE subagent (`general-purpose`) to read the transcript and return a
structured list. Give it the goal verbatim.

Its instructions must include, in this order of importance:

1. **Every item carries a verbatim quote** from the transcript, with the
   speaker if the transcript names one. An item without a quote is an
   invention, and an invented decision is worse than a missed one — the owner
   will act on it believing someone said it.
2. **Exhaustive, then ranked.** Do not summarise. List every item that bears on
   the goal, then rank by how directly it does. Length is not the enemy here;
   the triage handles volume, a missing item is unrecoverable.
3. **Classify each item**: `decision` · `action` · `fact` · `risk` ·
   `question-left-open` · `disagreement`. The verb the owner picks later
   usually follows the type, and a mislabelled decision reads as a suggestion.
4. **A second bucket for off-goal items.** Things clearly said and clearly
   relevant to the project but outside the stated goal go in `off_goal`, not in
   the bin. The owner sees them at the end and can pull any into the triage.
   Silently dropping them is how a "goal" turns into a blindfold.
5. **Say what is ambiguous.** If the transcript is unclear about who owns an
   action or whether a decision was final, the item says so rather than
   resolving it. That ambiguity is exactly what the owner is there to settle.

Ask it for JSON: `{ items: [{id, type, statement, quote, speaker, relevance,
ambiguity}], off_goal: [...] }`.

---

## Step 3 — Triage, four at a time

First, show the whole list compactly — one line per item, numbered, grouped by
type. The owner needs the shape of the thing before deciding on its parts;
starting with question 1 of 30 hides how much is coming.

Then use `AskUserQuestion` in rounds of up to **four items per call**, each with
these four options:

| Option | Meaning |
|---|---|
| **Keep** | It becomes team knowledge. Nothing changes in the repo. |
| **Apply** | It causes a change now — a task, a decision recorded, or code. |
| **Later** | It goes to the backlog with enough context to be picked up cold. |
| **Skip** | It is noise for this project. Recorded as skipped, never re-asked. |

Rules for this loop:

- **One round at a time**, and act on nothing until the round is answered. A
  batch of ten questions is a form, not a conversation.
- **Carry the quote into the question text** when the statement alone is
  ambiguous. The owner is deciding about what was said, not about your
  paraphrase of it.
- **If they answer "Other" with free text, take it literally** — it usually
  means "keep but reword" or "apply, but only this half". Restate what you
  understood in one line and continue.
- **Never widen a verb.** "Keep" is not permission to change code. If an item
  looks like it needs applying and they said keep, say so in one line at the
  end; do not act.

---

## Step 4 — Execute, grouped by verb

Act only after the whole list is triaged, so the owner sees the full
consequence before any of it lands.

- **Keep** → append to `<HOME>/<slug>.md` under `## Kept`, each
  with its quote and date. Then offer — once, not per item — to push them into
  the team brain through memory-api. Pushing is a separate yes: the file is
  local and reversible, the brain is shared and read by the agent.
- **Apply** → for each, state the concrete change in one line and get a yes
  before making it. If several applies touch the same file, do them in one
  pass. Anything that turns into real work (a phase, a migration, a feature)
  goes through whatever route the project already uses for planned work — GSD
  where the repo has it, otherwise the convention in its CLAUDE.md — rather
  than an ad-hoc edit.
- **Later** → one `<BACKLOG>` entry per item, written so it can be
  picked up cold: what was said, why it matters, what it would change. A
  backlog line nobody can act on six weeks later is a deleted line with extra
  steps.
- **Skip** → recorded in the ledger under `## Skipped`, with the quote. This is
  what makes a re-run cheap, and it is also the record of a judgment call.

---

## Step 5 — Close the loop

Write `<HOME>/<slug>.md` with: the goal, the date, counts per
verb, and the four sections. Then tell the owner, in a few lines: what landed
where, what is now in the backlog, and — separately — anything you disagreed
with but did not act on.

Finally, surface the `off_goal` bucket as a short list with one question: pull
any of these in? Most times the answer is no, and it costs one line to ask.

---

## What this skill must never do

- **Invent an item.** No quote, no item.
- **Act on a verb the owner did not pick**, including "obviously they meant
  apply".
- **Push to the team brain without a separate yes.** The brain is shared, the
  agent quotes from it, and a wrong entry is read back to teammates as fact.
- **Silently drop the off-goal bucket.**
- **Re-ask a settled question** on a re-run.

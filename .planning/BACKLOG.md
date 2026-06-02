# Backlog — ideas captured, not yet planned

Items here are seeds for future milestones/phases. Promote to ROADMAP when ready.

---

## BL-001 — Cold-start: brain proactively interviews the team when it has no info

**Captured:** 2026-06-03 (user request)

**Idea:** When the team brain has **no (or almost no) information** about what the team
works on, the assistant should **proactively ask for project context in the chat** —
*before* the user even asks a question — so the brain learns:
- what the team is working on (projects, domain, goals),
- which kinds of information are worth capturing/curating going forward,
- who the people / key entities are.

**Why:** the differentiator is the curated team brain. A brand-new team with an empty brain
has nothing to recall, and the user doesn't know what to feed it. A guided onboarding
"interview" bootstraps the brain and teaches it the team's selection criteria (what to keep,
at what truth-level).

**Rough shape (to refine when planned):**
- Trigger: on a new team / first conversation where `memory_items` count for the team is ~0
  (detectable via team_context / a brain-size check), the assistant opens with a short,
  structured set of questions (project? domain? what should I remember? who's involved?).
- Capture: answers are written to the brain as WORKING facts (tagged source=onboarding),
  optionally proposed for VALIDATED via the promotion flow.
- Surfaces: LibreChat (system-prompt-driven opener) + the extension team chat (@claude).
- Guardrail: only fires once (don't nag); skippable; respects the empty-brain detection so it
  doesn't trigger for established teams.

**Open questions:** where the "opener" is injected (modelSpecs promptPrefix vs librechat-bridge
vs team_chat_agent); how to detect "empty brain" cheaply per team; how aggressive (one opener
vs a multi-turn wizard); whether to also drive truth-level selection criteria.

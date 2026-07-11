# Quick task 260711-45b — Deferred items

Durable companion to the code comment in `handleClaude`'s `finally` block
(`chrome-extension/claude_ai_client.js`). These two items were identified while
implementing auto-delete of bridged claude.ai conversations but are explicitly
OUT of scope for this quick task.

## Native threading (token optimization)

Send only the new user turn and chain `parent_message_uuid` to the last
assistant message so claude.ai holds context server-side, removing the O(n^2)
full-history re-send currently done in `openaiToClaudeAi()` (every request
flattens the ENTIRE conversation history into one `prompt`).

This is explicitly **INCOMPATIBLE** with the delete-per-message model shipped
in this quick task — deleting the conversation after each turn destroys the
server-side thread that native threading would depend on. It is therefore a
SEPARATE future track: a later decision between "stateless + delete" (what
this task ships) and "native threading + keep the conversation around".

## orgId module-level caching

`getOrgId()` is called once per bridged message (a fresh `GET
/api/organizations` round-trip every time), even though the org UUID rarely
changes for a given session. Caching it at module scope would save one
network round-trip per message.

Deferred because it needs cache invalidation on account switch — the cache
would go stale when the user changes claude.ai accounts, and there is no
existing hook to detect that. The closest candidate is `refreshClaudeSession`
in `background.js`, which is NOT modified by this quick task.

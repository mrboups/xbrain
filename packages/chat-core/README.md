# chat-core

The portable half of the xbrain team chat: the logic the Chrome extension and
the PWA both run. Plain ES modules — no build step, no bundler, no dependency
outside this directory.

| Module           | What it owns                                                     |
| ---------------- | ---------------------------------------------------------------- |
| `platform.js`    | The storage / openUrl / notify shim CONTRACT (D-27-04) + validator |
| `api.js`         | `createApi({ baseUrl, getToken })` — every memory-api call        |
| `chat_stream.js` | Mention regex, `StreamBuffer`, message + day labels               |
| `nudge_open.js`  | Push-a-link consent core (Phase 22)                               |
| `theme.js`       | shadcn Neutral light/dark resolution                              |

**This is the ONLY editable copy.** `chrome-extension/chat_core/` and
`app-site/app/chat_core/` are byte-identical GENERATED copies — an edit made
there is erased by the next sync and rejected by the drift gate before that.

```
make sync-chat-core    # copy packages/chat-core/*.js into both surfaces
make check-chat-core   # fail if either copy has drifted
```

`chrome-extension/tests/test_chat_core_sync.mjs` runs the same check inside the
extension suite, so drift cannot reach a green build.

Nothing here may reference a browser-extension API or a hardcoded origin; each
surface injects its own base URL and its own platform shim.

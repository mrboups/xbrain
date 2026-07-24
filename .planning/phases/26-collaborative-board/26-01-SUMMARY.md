---
phase: 26-collaborative-board
plan: 01
subsystem: ui
tags: [excalidraw, yjs, hocuspocus, vite, react, crdt, typescript, vendoring]

# Dependency graph
requires: []
provides:
  - "apps/board-web/ — the repo's first frontend build target (Vite + React, build-time Node only, D-26-01)"
  - "Exact (caret-free) dependency pins: @excalidraw/excalidraw 0.18.1, yjs 13.6.31, @hocuspocus/provider 4.4.0, react/react-dom 18.3.1"
  - "vite.config.ts with the process.env.IS_PREACT define (Pitfall 3) — the without-which-blank-canvas fix"
  - "A vendored, 0.18.1-compatible Yjs<->Excalidraw binding at apps/board-web/src/yjs-binding/ that typechecks and applies CaptureUpdateAction.NEVER to remote/init updates"
  - "D-26-05 answered with recorded evidence (DECISION.md): VENDORED"
affects: [26-04, 26-05, 26-06, 26-07]

# Tech tracking
tech-stack:
  added:
    - "@excalidraw/excalidraw@0.18.1"
    - "yjs@13.6.31"
    - "@hocuspocus/provider@4.4.0"
    - "react@18.3.1 / react-dom@18.3.1"
    - "vite@8.1.5 / @vitejs/plugin-react@6.0.4 / typescript@7.0.2"
    - "fractional-indexing@3.2.0 (CC0-1.0) / y-protocols@1.0.7 (MIT) — direct deps of the vendored binding"
  patterns:
    - "Vendoring an unmaintained MIT dependency into src/ with LICENSE + provenance headers instead of an npm dep"
    - "Exact version pins enforced by an automated node -e manifest scan (no caret/tilde)"
    - "tsc --noEmit against a library's own .d.ts as the compatibility proof for a pinned version"

key-files:
  created:
    - "apps/board-web/package.json"
    - "apps/board-web/package-lock.json"
    - "apps/board-web/.gitignore"
    - "apps/board-web/vite.config.ts"
    - "apps/board-web/tsconfig.json"
    - "apps/board-web/index.html"
    - "apps/board-web/src/yjs-binding/index.ts"
    - "apps/board-web/src/yjs-binding/diff.ts"
    - "apps/board-web/src/yjs-binding/helpers.ts"
    - "apps/board-web/src/yjs-binding/LICENSE"
    - "apps/board-web/src/yjs-binding/DECISION.md"
  modified: []

key-decisions:
  - "D-26-05 executed: VENDORED. The shipped y-excalidraw@2.0.12 has neither commitToHistory nor captureUpdate and relies on the pre-0.18 updateScene default, which 0.18 flipped to EVENTUALLY (poisons the local undo stack). Its 0.17 import paths also no longer resolve against 0.18.1. Vendored the 3 MIT files and patched to captureUpdate: CaptureUpdateAction.NEVER."
  - "Pinned @types/react / @types/react-dom to 18.x (npm latest resolved 19.x against a react 18 runtime)."
  - "Promoted fractional-indexing + y-protocols (former transitive deps of y-excalidraw) to direct exact pins because the vendored code imports them directly."

patterns-established:
  - "Vendored-dependency layout: source + verbatim LICENSE + DECISION.md recording the branch and its raw evidence, all under a single src/ subfolder."
  - "process.env.IS_PREACT Vite define is mandatory for any Excalidraw mount in this repo."

requirements-completed: [BOARD-01]

# Metrics
duration: 17min
completed: 2026-07-24
---

# Phase 26 Plan 01: Board SPA Scaffold + y-excalidraw Vendoring Spike Summary

**Vite+React apps/board-web/ with caret-free pins on Excalidraw 0.18.1 / yjs / Hocuspocus, and a vendored MIT Yjs<->Excalidraw binding patched to CaptureUpdateAction.NEVER that typechecks against 0.18.1 — the D-26-05 spike answered VENDORED with recorded evidence.**

## Performance

- **Duration:** ~17 min
- **Started:** 2026-07-24T03:48:58Z
- **Completed:** 2026-07-24T04:06:08Z
- **Tasks:** 2
- **Files modified:** 11 created

## Accomplishments
- Stood up the repo's first frontend build target `apps/board-web/` — Vite + React, build-time Node only (D-26-01). Nothing here adds a Node runtime to the API path; `chrome-extension/` is untouched.
- Enforced exact (caret-free) pins on the four research-verified packages and committed `package-lock.json` as the deterministic `npm ci` input.
- Ran the D-26-05 spike against `y-excalidraw@2.0.12` and captured the raw evidence: it neither uses the old `commitToHistory` nor the new `captureUpdate` API, and its 0.17-era import paths no longer resolve against 0.18.1.
- Executed the decision in code — VENDORED the 3 MIT source files (~23 KB), preserved the LICENSE verbatim, added provenance headers, rewrote import paths to the 0.18 layout, and applied `captureUpdate: CaptureUpdateAction.NEVER` to all four remote/init `updateScene` call sites (Pitfall 4 / T-26-03 mitigation).
- Proved compatibility: `npx tsc --noEmit -p tsconfig.json` exits 0 against `@excalidraw/excalidraw@0.18.1`'s own `.d.ts` under `strict: true`. No later wave inherits an unverified binding.

## Task Commits

Each task was committed atomically:

1. **Task 1: Scaffold apps/board-web with exact pins + run the spike** - `44f8c4f` (feat)
2. **Task 2: Execute the D-26-05 decision — vendor the MIT binding + fix captureUpdate** - `e382f8b` (feat)

**Plan metadata:** committed separately with this SUMMARY.

## Files Created/Modified
- `apps/board-web/package.json` - Exact-pinned SPA manifest (build-time Node only)
- `apps/board-web/package-lock.json` - Deterministic lockfile (input to npm ci / the 26-07 gate)
- `apps/board-web/.gitignore` - Ignores `node_modules/` and `dist/` (directory is self-describing)
- `apps/board-web/vite.config.ts` - React plugin + `process.env.IS_PREACT` define (Pitfall 3)
- `apps/board-web/tsconfig.json` - Strict Vite react-ts config (moduleResolution bundler, noEmit)
- `apps/board-web/index.html` - Minimal English entry declaring `/src/main.tsx` (created by 26-04)
- `apps/board-web/src/yjs-binding/index.ts` - Vendored binding; remote/init updateScene -> CaptureUpdateAction.NEVER
- `apps/board-web/src/yjs-binding/diff.ts` - Vendored element/asset delta operations
- `apps/board-web/src/yjs-binding/helpers.ts` - Vendored yjs<->excalidraw helpers
- `apps/board-web/src/yjs-binding/LICENSE` - Upstream MIT notice, verbatim (Rahul R Badenkal)
- `apps/board-web/src/yjs-binding/DECISION.md` - D-26-05 record: `Decision: VENDORED` + raw spike evidence

## Spike Evidence (D-26-05)

`npm view y-excalidraw`:
```
version = '2.0.12'
license = 'MIT'
peerDependencies = { '@excalidraw/excalidraw': '^0.17.6', yjs: '^13.6.19' }
time.modified = '2024-12-10T03:57:10.265Z'
```

API probe over `node_modules/y-excalidraw/` (installed `--no-save --legacy-peer-deps`):
```
grep -rlno "commitToHistory" node_modules/y-excalidraw/   -> 0 hits
grep -rlno "captureUpdate"    node_modules/y-excalidraw/   -> 0 hits
```
Neither history API is present. The shipped build calls `updateScene({ elements })` with no history flag and depends on the `updateScene` default. In 0.18.1 that default is `CaptureUpdateAction.EVENTUALLY` (verified in `App.d.ts`), so remote edits would land in the LOCAL user's undo stack (Pitfall 4). Package ships **dist-only** (no `src/`), so the source was fetched from `raw.githubusercontent.com/RahulBadenkal/y-excalidraw/main/src/*.ts` + `LICENSE`. A second, independent break: the 0.17 import paths (`@excalidraw/excalidraw/types/types`, `.../types/element/types`) raise `TS2307` against 0.18.1.

**Call sites changed to `CaptureUpdateAction.NEVER` (all in `index.ts`):**
1. `_remoteElementsChangeHandler` — remote element updates
2. `_remoteAwarenessChangeHandler` — remote awareness / collaborators
3. Scene initialization — `updateScene({ elements: initialValue, ... })`
4. Initial collaborators — `updateScene({ collaborators, ... })`

**Resolved exact versions npm wrote (for the record):** react 18.3.1, react-dom 18.3.1, vite 8.1.5, typescript 7.0.2, @vitejs/plugin-react 6.0.4, @types/react 18.3.31, @types/react-dom 18.3.7, fractional-indexing 3.2.0, y-protocols 1.0.7.

## Decisions Made
- **VENDORED (D-26-05).** See spike evidence above and `apps/board-web/src/yjs-binding/DECISION.md` for the full record, including the upstream re-check note (if PR #13 merges, revisiting is a deliberate decision, not an auto-revert).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Pinned React type packages to 18.x**
- **Found during:** Task 1 (dependency install)
- **Issue:** `npm i -DE @types/react @types/react-dom` resolved to 19.x against a react@18.3.1 runtime — a type/runtime major mismatch that would surface as spurious JSX/hook type errors and break the `tsc --noEmit` gate.
- **Fix:** Reinstalled `@types/react@18 @types/react-dom@18` exact (resolved 18.3.31 / 18.3.7).
- **Files modified:** apps/board-web/package.json, apps/board-web/package-lock.json
- **Verification:** manifest scan exits 0 (no caret/tilde); `tsc --noEmit` exits 0.
- **Committed in:** 44f8c4f (Task 1 commit)

**2. [Rule 3 - Blocking] Promoted fractional-indexing + y-protocols to direct exact pins**
- **Found during:** Task 2 (vendoring)
- **Issue:** The vendored binding imports `fractional-indexing` (diff.ts) and `y-protocols/awareness` (index.ts) directly. These were transitive deps of `y-excalidraw`; once it is removed, `tsc` and any Vite build cannot resolve them.
- **Fix:** `npm i -E fractional-indexing@3.2.0 y-protocols@1.0.7` (matching the versions the binding was written against; CC0-1.0 and MIT respectively — both OSS-clean).
- **Files modified:** apps/board-web/package.json, apps/board-web/package-lock.json
- **Verification:** `npm ls --depth=0` clean (no UNMET, no extraneous); `tsc --noEmit` exits 0.
- **Committed in:** e382f8b (Task 2 commit)

**3. [Rule 1 - Bug] Strict-mode + 0.18 API conformance of the vendored code**
- **Found during:** Task 2 (typecheck against 0.18.1 under strict:true)
- **Issue:** The upstream source did not compile under `strict: true` against 0.18.1: implicit-any params (debounce, event handlers), possibly-undefined `awareness`/`undoManager`, a `number | null` index, possibly-null button refs, and a `Map<string>` vs `Map<SocketId>` collaborators-map key mismatch (0.18 branded the key type).
- **Fix:** Typed the debounce/event params, captured a non-null `undoManager` local inside the guarded method, added targeted non-null assertions where the surrounding guard guarantees definedness, marked type-only imports `import type` (so deep type paths never reach the runtime bundle), and typed the collaborators maps as `Map<SocketId, Collaborator>` with `as SocketId` key casts. Public surface (the `ExcalidrawBinding` constructor) unchanged.
- **Files modified:** apps/board-web/src/yjs-binding/{index,diff,helpers}.ts
- **Verification:** `npx tsc --noEmit -p tsconfig.json` exits 0.
- **Committed in:** e382f8b (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 blocking, 1 bug)
**Impact on plan:** All three were necessary to produce the compatibility proof the plan exists to generate (a passing `tsc --noEmit`). They are the direct, expected cost of the vendoring path the plan mandated. No scope creep — nothing beyond `apps/board-web/` was touched.

## Issues Encountered
- Docker was NOT invoked (arm64 dev host → amd64 VM constraint): `npm install` is a pure-JS resolution step and is safe locally; the board image is built in CI/Docker (26-04/26-06), never shipped from here.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- The binding at `apps/board-web/src/yjs-binding/` is ready for 26-04 to consume (`import { ExcalidrawBinding, yjsToExcalidraw } from "./yjs-binding"`). Its public surface is a constructor taking `yElements: Y.Array`, `yAssets: Y.Map`, the `excalidrawAPI`, an optional awareness, and an optional undo config.
- 26-04 still owns `src/main.tsx`, `Board.tsx`, `token.ts`, and must `import "@excalidraw/excalidraw/index.css"` and render client-side only (no SSR).
- No blockers. Waves 2-4 can build on a verified, 0.18.1-compatible binding.

## Self-Check: PASSED

- All 11 created files verified present on disk (scaffold + vendored binding + DECISION.md + this SUMMARY).
- Both task commits verified in git history: `44f8c4f` (Task 1), `e382f8b` (Task 2).
- `npm ls --depth=0` clean (no UNMET, no extraneous); manifest scan exits 0 (no caret/tilde); `npx tsc --noEmit -p tsconfig.json` exits 0.

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*

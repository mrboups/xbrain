Decision: VENDORED

# D-26-05 — y-excalidraw vs @excalidraw/excalidraw 0.18.1

The Wave-0 spike answered the one question this phase cannot be built on blind:
does `y-excalidraw@2.0.12` from npm work against `@excalidraw/excalidraw@0.18.1`?
It does not. The three MIT source files (~23 KB) were vendored into this directory
and patched for 0.18.1. This is the outcome the research predicted (D-26-05:
"vendoring is the expected outcome").

## Spike evidence (raw, from Task 1)

### `npm view y-excalidraw version license peerDependencies time.modified`

```
version = '2.0.12'
license = 'MIT'
peerDependencies = { '@excalidraw/excalidraw': '^0.17.6', yjs: '^13.6.19' }
time.modified = '2024-12-10T03:57:10.265Z'
```

The published build peers `@excalidraw/excalidraw: ^0.17.6` and has not been
touched since 2024-12-10 (~19 months). The 0.18 peer bump lives only in unmerged
PR #13.

### API probe over `node_modules/y-excalidraw/` (installed with `--no-save --legacy-peer-deps`)

```
grep -rlno "commitToHistory" node_modules/y-excalidraw/   -> (no output, 0 hits)
grep -rlno "captureUpdate"    node_modules/y-excalidraw/   -> (no output, 0 hits)
```

Neither the old API (`commitToHistory`) nor the new one (`captureUpdate`) appears
in the shipped build. The binding calls `api.updateScene({ elements })` with **no**
history-control flag and relies entirely on the `updateScene` default. That default
changed between the versions:

- **0.17.x:** `updateScene` did not record remote updates into history by default.
- **0.18.1:** the default is `captureUpdate: CaptureUpdateAction.EVENTUALLY`
  (`@default CaptureUpdateAction.EVENTUALLY`, verified in
  `@excalidraw/excalidraw/dist/types/excalidraw/components/App.d.ts`), which **does**
  eventually record. `CaptureUpdateAction.NEVER` is documented as "Use for updates
  which should never be recorded, such as remote updates or scene initialization."

So under 0.18.1 the unpatched binding poisons the LOCAL user's Ctrl+Z stack with a
teammate's remote edits (Pitfall 4) — undo would start deleting other people's work.
This is dispositive: the npm package cannot be used as-is.

### Shipped package shape

`node_modules/y-excalidraw/` ships **dist-only** (`dist/y-excalidraw.js`,
`dist/*.cjs`, `dist/src/*.d.ts`) — there is no `src/` TypeScript directory. The
vendored source was therefore fetched from
`raw.githubusercontent.com/RahulBadenkal/y-excalidraw/main/src/{index,diff,helpers}.ts`
plus `.../main/LICENSE` (byte sizes match the research: index.ts 9.9 KB,
diff.ts 11.7 KB, helpers.ts 1.2 KB).

### A second, independent break: import paths

Beyond the history default, the 0.17-era binding imports Excalidraw types from the
old package layout, which no longer exists in 0.18.1:

- `@excalidraw/excalidraw/types/types` (0.17) -> `@excalidraw/excalidraw/types` (0.18)
- `@excalidraw/excalidraw/types/element/types` (0.17) -> `@excalidraw/excalidraw/element/types` (0.18)

Against the 0.18.1 `.d.ts`, the unpatched paths raise `TS2307: Cannot find module`.
The npm dist would have failed to typecheck for a downstream consumer regardless of
the history issue.

## Licence position

MIT, Copyright (c) 2024 Rahul R Badenkal. The upstream `LICENSE` is preserved
verbatim at `./LICENSE`, and every source file carries a provenance header naming
the upstream version and copyright holder. This satisfies the OSS-only +
attribution constraint (CLAUDE.md). GitHub's API reports `NOASSERTION` only because
the licensee scanner does not match the modified header; the `LICENSE` file itself
is unambiguously the MIT text.

## Local changes applied (BRANCH A)

1. **History control on every remote / init `updateScene` call** — added
   `import { CaptureUpdateAction } from "@excalidraw/excalidraw"` and set
   `captureUpdate: CaptureUpdateAction.NEVER` at the four call sites that push
   non-local state into the scene (`index.ts`):
   - remote element change handler (`_remoteElementsChangeHandler`);
   - remote awareness / collaborators change handler (`_remoteAwarenessChangeHandler`);
   - scene initialization (`updateScene({ elements: initialValue, ... })`);
   - initial collaborators (`updateScene({ collaborators, ... })`).
   The local `onChange` handler writes to `yElements` (not `updateScene`), so it
   needs no history flag. There were no `commitToHistory: true` (local-edit) call
   sites to convert to `IMMEDIATELY`; all `updateScene` sites here are remote/init.
2. **Import paths** rewritten from the 0.17 `types/types` layout to the 0.18 layout
   (see above), in `index.ts`, `diff.ts`, `helpers.ts`.
3. **Type-only imports** marked `import type` (Excalidraw element/type imports,
   `SocketId`, `ExcalidrawBinding`) so they are erased and never reach the runtime
   bundle — the 0.18 `./*` export map exposes only `types`, not a runtime JS entry,
   for these deep paths.
4. **Strict-mode conformance** against the project `tsconfig.json` (`strict: true`):
   `SocketId`-branded collaborator map keys, non-null assertions where `awareness`
   / `undoManager` are guaranteed defined by the surrounding guard, typed event and
   debounce parameters, and a guaranteed-non-null `oldIndex` in `diff.ts`. The
   public surface (the `ExcalidrawBinding` constructor taking `yElements`,
   `yAssets`, `api`, optional `awareness`, optional undo config) is unchanged, so
   plan 26-04 can consume it directly.
5. **Direct dependencies promoted** — `fractional-indexing@3.2.0` (CC0-1.0) and
   `y-protocols@1.0.7` (MIT) were transitive deps of `y-excalidraw`; the vendored
   code imports them directly, so they are now exact pins in `package.json`.
6. **No `y-excalidraw` in `package.json`** — the `--no-save` spike tree was removed
   so the committed manifest and the installed tree agree (`npm ls --depth=0`
   reports no extraneous `y-excalidraw`).

## Compatibility proof

`cd apps/board-web && npx tsc --noEmit -p tsconfig.json` exits 0 — the vendored
binding typechecks against `@excalidraw/excalidraw@0.18.1`'s own `.d.ts` under
`strict: true`. This is the answer to D-26-05, executed in code rather than assumed.

## Upstream re-check

If PR #13 (the 0.18 peer bump) ever merges upstream, this vendored copy MAY be
revisited — but a re-check is a deliberate decision, not an automatic revert. A
19-month-stale, 36-star package in the CRDT critical path is a liability we now
control directly; the bar for handing that control back to an unmaintained npm
dependency is high.

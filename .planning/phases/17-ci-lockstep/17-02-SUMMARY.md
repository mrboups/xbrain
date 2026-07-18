---
phase: 17-ci-lockstep
plan: 02
subsystem: ci-infrastructure
tags: [ci, docker-compose, ghcr, verification-gate, build-once]
requires:
  - infrastructure/docker-compose.yml (the 18 build: services + the integrations/saas/ops profiles)
  - infrastructure/scripts/verify-phase16.sh (the proven Phase-16 OSS-subset gate)
provides:
  - "docker-compose.ci-images.yml: GHCR image override remapping all 18 build: services (build-once artifact)"
  - "verify-phase17-full.sh: daemon-free full-profile graph + override-completeness gate"
  - "verify-phase16.sh VERIFY16_NO_BUILD / VERIFY16_EXTRA_COMPOSE opt-in CI hooks"
affects:
  - "Plan 17-03 (the lockstep workflow) consumes all three as its build/test job bodies"
tech-stack:
  added: []
  patterns:
    - "Derived assertions over hardcoded counts: the gate cross-checks two independent readings of the compose graph"
    - "Daemon-free verification via `docker compose config` (pure parse), which is what makes SKIP=FAIL honest"
    - "Opt-in env hooks with token-identical default expansion for backward-compatible script reuse"
key-files:
  created:
    - infrastructure/scripts/verify-phase17-full.sh
  modified:
    - infrastructure/scripts/verify-phase16.sh
  shipped-earlier:
    - infrastructure/docker-compose.ci-images.yml
decisions:
  - "Full-profile gate asserts the graph, not a 32-container boot (D-17-03)"
  - "Expected service/build counts are DERIVED every run, never hardcoded literals"
  - "verify-phase16.sh hooks are opt-in; default path proven unchanged by a real 23/23 boot"
metrics:
  duration: ~50 min
  completed: 2026-07-18
  tasks: 3
  commits: 3
---

# Phase 17 Plan 02: CI Test Harness (GHCR override + full-profile gate + no-build hook) Summary

Built the three artifacts that make Phase 17's "build once, pull everywhere" real: a GHCR
compose override remapping all 18 `build:` services, a daemon-free full-profile gate that
derives (never hardcodes) the 32-service graph and proves the override is complete, and an
opt-in no-build hook letting the proven Phase-16 gate run against CI-built images.

## What Was Built

### Task 1 — `infrastructure/docker-compose.ci-images.yml` (shipped earlier, commit `f6aa63e`)

Compose override mapping each of the 18 `build:` services to
`ghcr.io/${GHCR_OWNER:-mrboups}/xbrain-<svc>:${XBRAIN_IMAGE_TAG:-latest}`. Layered as a second
`-f`, it makes `pull` fetch the CI-built image and `up -d` run it without `--build`.

- 87 lines, exactly 18 service keys, no `build:` keys (the base file already carries them).
- `xbrain-backup` maps to `xbrain-backup`, **not** `xbrain-xbrain-backup` (its service name
  already carries the prefix). Its amd64-only constraint (D-17-02: the base image publishes no
  arm64 manifest) is documented in the file header.
- One file, four consumers (build / test / publish / deploy), so build and deploy can never
  drift onto different image names.
- Additive by construction: the base file's local dev tags are untouched, so `make build` /
  `make up` are unchanged for developers.

Re-verified this session: 18 services, all images contain `/xbrain-`, no double prefix.

### Task 2 — `infrastructure/scripts/verify-phase17-full.sh` (new, 335 lines, commit `f0bda22`)

The full-profile test job body. Per D-17-03 this is a **graph + override validation, not a
32-container boot** — and the script says so out loud in check (d) so a green run can never be
misread as "the full stack booted".

- **(a) FULL GRAPH — derived, not hardcoded.** Two *independent* readings of the compose graph
  must agree: (1) `config --services` counted with profiles on vs. off, and (2) each service's
  own `profiles:` key in the resolved config. The assertion is the identity
  `listed_full == listed_core + profile_tagged`, plus `core ⊆ full`. Today that evaluates to
  32 == 10 + 22; adding a service updates both sides and the check keeps holding, while a
  service tagged inconsistently breaks it. A drift NOTE prints if the numbers move off
  32/10/22, but that is informational — it is not the assertion.
- **(b) PROFILES** — exactly `integrations ops saas` (no `pro`).
- **(c) OVERRIDE COMPLETENESS** — the expected count is derived from the base file (how many
  services carry a `build:` key = 18 today); every one must resolve to a
  `ghcr.io/<owner>/xbrain-*` image, with `ghcr.io` hardcoded in the pattern so the override can
  never point at an arbitrary registry host (T-17-02-02). Also rejects `xbrain-xbrain-*` double
  prefixes and asserts the amd64-only note for `xbrain-backup` is present.
- **(d) SCOPE** — prints what is *not* covered here (the boot, the `EDITION=saas` pytest suite,
  the 10-core SC#3 walk) and which CI job covers each.

### Task 3 — `infrastructure/scripts/verify-phase16.sh` opt-in hooks (commit `e48a563`)

Two env vars, both **unset by default**:

- `VERIFY16_EXTRA_COMPOSE=<file>` — an extra `-f` layered onto the **live boot only**.
- `VERIFY16_NO_BUILD=1` — boot via `pull` + `up -d` instead of `up -d --build`.

Implemented via a single `DC_LIVE` array used by the live-boot block (up/pull/ps/logs/down),
declared *before* the EXIT trap is installed so `cleanup()` can always tear down with the same
file set it booted with, even on an early exit. The array is built with `DC_LIVE+=(-f "$VAR")`
rather than an unquoted `${VAR:+...}` expansion, so a path containing spaces survives.

The config-layer check (a) was deliberately **not** touched: it asserts what the *base* compose
file declares and must never see an image override. Verified — under `VERIFY16_NO_BUILD=1` the
`config` calls still reference only `docker-compose.yml`.

## Verification — Real Output

### `verify-phase17-full.sh` (verbatim, ANSI stripped)

```
=== Phase 17 Verification — FULL PROFILE graph + GHCR override (REL-01, REL-03) ===

(a) FULL GRAPH — full == core + profile-tagged (DERIVED both ways), core is a subset of full
      derived: core(listed)=10  profile-tagged(by profiles: key)=22  full(listed)=32
      cross-check: untagged(by profiles: key)=10
  PASS: (a) full-profile graph resolves: 32 services == 10 core + 22 profile-tagged (derived, agrees with the profiles: keys); core is a subset of full

(b) PROFILES — declared profile set is exactly 'integrations ops saas' (no pro)
  PASS: (b) profiles == 'integrations ops saas'

(c) OVERRIDE — every build: service resolves to ghcr.io/<owner>/xbrain-* under infrastructure/docker-compose.ci-images.yml
      derived from infrastructure/docker-compose.yml: 18 services carry a build: key — all 18 must be remapped
  PASS: (c) 18/18 build services remapped; owner(s)=['mrboups'] tag(s)=['latest']
  PASS: (c) xbrain-backup carries its single (non-doubled) prefix AND the override documents the amd64-only constraint (D-17-02)

=== Summary ===
PASS: 4 / 4  (SKIP: 0)
EXIT=0
```

### Daemon-free claim — tested, not assumed

Re-ran the whole gate with `DOCKER_HOST=tcp://127.0.0.1:1` (dead socket): identical
`4 / 4  (SKIP: 0)`, exit 0. The Docker daemon was *running* on this host, so the claim could not
be proven by absence — pointing at a dead socket proves it positively. This is what makes
SKIP=FAIL honest here: these checks can always run, so a SKIP would only ever mean the gate was
dodged.

### Negative tests — the gate can actually fail

| Injected defect | Result |
|---|---|
| Removed `mcp-brain` from the override | `FAIL: (c) 1/18 build services are NOT remapped ...: mcp-brain->'xbrain/mcp-brain:phase8'` — exit 1 |
| Renamed `xbrain-backup` → `xbrain-xbrain-backup` | `FAIL: (c) double-prefixed image name (xbrain-xbrain-*) on: xbrain-backup` — exit 1 |

Both defects were injected into a backed-up copy and reverted; `git diff` confirmed the override
file was restored byte-clean and the gate returned to exit 0.

### `verify-phase16.sh` default path — unregressed (a real boot, not a syntax check)

Ran the **full** gate in default mode with the finished code, against the real Docker daemon:

```
EXIT=0
=== Summary ===
PASS: 23 / 23  (SKIP: 0)
```

That is a genuine `up -d --build` of all 10 core services reaching healthy, plus the entire SC#3
HTTP walk (register/login, keyless semantic retrieval with truth_level, connector consent
including the wrong-password negative half, clip → a real `memory_items` row). No containers
leaked afterwards. Three independent layers of proof that the default path is unchanged:

1. **Token-identical expansion.** The real `DC_LIVE` lines were extracted from the shipped file
   and evaluated with the hooks unset:
   `docker|compose|-p|xbrain-p16|-f|infrastructure/docker-compose.yml|--env-file|/tmp/oss.env|`
   — identical to the pre-edit literal invocation.
2. **Byte-identical output.** `diff` of the default-mode `(d)` output before and after the final
   wording change: identical.
3. **Diff review.** All 11 deleted lines are exactly the five compose invocations refactored to
   `DC_LIVE` plus the parameterized `ok()` line. No check, wording, or host-path rule was removed.

### Both boot modes, captured from actual invocations

A `docker` shim recorded exactly what each mode issues:

```
MODE A (default):
  compose -p xbrain-p16 -f infrastructure/docker-compose.yml --env-file <env> up -d --build

MODE B (VERIFY16_NO_BUILD=1 + VERIFY16_EXTRA_COMPOSE):
  compose -p xbrain-p16 -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.ci-images.yml --env-file <env> pull
  compose -p xbrain-p16 -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.ci-images.yml --env-file <env> up -d
```

Mode B issues **zero** `--build` calls (`grep -c -- '--build'` = 0) — pull-not-build honored.
Mode A never references the override (`grep -c ci-images` = 0) — the hook is genuinely inert
when off.

## Deviations from Plan

### Auto-fixed / adjusted

**1. [Rule 3 — Blocking] Python helpers take data on stdin, never a temp-file path**

- **Found during:** Task 2
- **Issue:** On this Git-Bash host, `python` on PATH is a *Windows* Python that cannot open an
  MSYS `/tmp/...` path. The plan said to copy verify-phase16's structure, which passes helper
  script paths as argv (`"$PY" "$JSON_HELPER"`). Reproducing that pattern failed with
  `FileNotFoundError: '/tmp/ovr.json'`.
- **Fix:** Every python helper in the new gate takes its **data on stdin** and its script inline
  via `-c`, so no path is ever handed to python. Documented in the script header alongside the
  inverse rule (docker's `-f`/`--env-file` are host paths and must stay MSYS-converted).
- **Files:** `infrastructure/scripts/verify-phase17-full.sh`

**2. [Rule 2 — Missing critical] `SEARXNG_SECRET` added to the hermetic env**

- **Found during:** Task 2
- **Issue:** Phase 16's hermetic-env list is tuned for the *core* graph. Enabling
  `integrations` reaches `searxng`, and compose emitted
  `warning: The "SEARXNG_SECRET" variable is not set` on stderr during every full-profile
  `config` — noise that would contaminate parsed output.
- **Fix:** Added `SEARXNG_SECRET` to both the strip-regex and the clean re-appended block.
- **Files:** `infrastructure/scripts/verify-phase17-full.sh`

**3. [Deviation — repo convention] Script committed mode 100644, not `chmod +x`**

- **Found during:** Task 2 commit
- **Issue:** The plan says `chmod +x`. `git ls-files -s infrastructure/scripts/` shows **every**
  existing script in that directory is `100644`; all callers (Makefile, CI, docs) invoke them as
  `bash infrastructure/scripts/<name>.sh`.
- **Decision:** Matched the repo convention rather than the plan's literal instruction — a lone
  `100755` file would be the inconsistency. The plan's own acceptance criterion allows this
  (`test -x ... || test -f ...`). The script is invoked via `bash` everywhere, so this has no
  functional effect on Linux CI.

**4. [Improvement] Mode-accurate wording in `(d)` under NO_BUILD**

- **Found during:** Task 3 verification
- **Issue:** With `VERIFY16_NO_BUILD=1` the `(d)` section header and final PASS still read
  "up -d --build" / "REAL build+boot" — inaccurate for a pull-based boot, exactly the kind of
  output that makes a log lie about what ran.
- **Fix:** `boot_desc` / `boot_kind` locals initialised to the original strings, so **default
  output is byte-identical** (verified by diff) while NO_BUILD mode reads
  `pull + up -d (no --build)` / `REAL pull+boot`.
- **Files:** `infrastructure/scripts/verify-phase16.sh`

### Note on the documented service count

`17-CONTEXT.md` D-17-03 says "33 services"; the plan says 32. **Live measurement this session:
core = 10, full = 32, tagged = 22** (`docker compose config --services`). The gate derives these
at runtime and cross-checks them against the `profiles:` keys, so it is correct under either
number and will not drift — no doc edit was made (STATE/ROADMAP untouched per instructions).

## Incident (contained, no damage)

During shim development, a `PATH` prefix used a **Windows-style** path (`C:/Users/...`), which
MSYS `PATH` cannot parse, so the shim was silently ignored and the **real** `docker` ran — which
actually built and booted the 10-core stack. The tool call then hit its 2-minute timeout and was
SIGTERM'd. The script's `trap cleanup EXIT` fired and tore the stack down: `docker ps -a
--filter name=^xbrain-` and `docker compose ls -a` both came back empty immediately afterwards.

Two things worth recording: Phase 16's EXIT-trap teardown demonstrably survives SIGTERM, and the
accident produced a real unplanned default-mode run of the edited gate that passed (a)–(g). The
shim was then rebuilt with a `cygpath -u` POSIX path and worked as intended.

## Deferred / Not Done Here

- **`VERIFY16_NO_BUILD=1` against real GHCR images** — cannot run end-to-end yet: no images are
  published to `ghcr.io/mrboups/xbrain-*`, so `pull` would fail with `manifest unknown`. The
  branch logic, command shape, and file layering are proven via the recorded invocations above;
  the first true end-to-end exercise happens when Plan 17-03's workflow runs.
- **Full-profile boot-fit measurement** — a `workflow_dispatch` follow-up per D-17-03, not a gate.
- **`EDITION=saas` pytest suite + saas migration test** — belongs to the CI `test-full-profile`
  job (Plan 17-03), which runs them alongside this script.

## Self-Check: PASSED

Files verified present:
- `FOUND: infrastructure/scripts/verify-phase17-full.sh` (335 lines)
- `FOUND: infrastructure/scripts/verify-phase16.sh` (modified, `bash -n` clean)
- `FOUND: infrastructure/docker-compose.ci-images.yml` (87 lines, 18 services)

Commits verified in `git log`:
- `FOUND: f6aa63e` — feat(17-02): add GHCR image override for all 18 build services (Task 1)
- `FOUND: f0bda22` — feat(17-02): add verify-phase17-full.sh full-profile resolve gate (Task 2)
- `FOUND: e48a563` — feat(17-02): add opt-in no-build/override hooks to verify-phase16.sh (Task 3)

Post-commit deletion check: both new commits deleted zero tracked files.
STATE.md / ROADMAP.md: not modified (per parallel-execution instructions).

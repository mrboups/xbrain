#!/usr/bin/env bash
#
# verify-phase17-workflow.sh — Phase 17 (CI Lockstep) WORKFLOW gate.
#
# WHAT THIS PROVES: that .github/workflows/ci-lockstep.yml is (1) valid GitHub Actions YAML
# that actionlint accepts with zero findings, and (2) shaped so that SC3 lockstep is a
# property of the `needs:` graph rather than of a comment. Both checks are static, need no
# daemon and no network once the linter is cached, so they CAN always run — which is exactly
# what makes SKIP == FAIL honest here: a skip could only ever mean the gate was dodged.
#
# WHAT IT DOES NOT PROVE: that the workflow RUNS. It has never executed on a hosted runner.
# A green run of this script says the pipeline is well-formed and correctly wired, not that
# the build succeeds, that GHCR accepts the push, or that the deploy reaches the VM. See
# docs/ci-lockstep.md for the full residual — do not read a green lint as a green run.
#
# Checks:
#   (a) TOOLING     — resolve `actionlint`, downloading it to a cache OUTSIDE the repo if
#                     absent. Only a genuine download failure (offline) degrades to the
#                     weaker python YAML-parse fallback, and it says so loudly.
#   (b) ACTIONLINT  — zero findings on ci-lockstep.yml. SCOPED TO THAT ONE FILE on purpose:
#                     deploy-dashboard.yml carries 2 pre-existing findings that Phase 17 must
#                     not touch (see .planning/phases/17-ci-lockstep/deferred-items.md), and
#                     a repo-wide lint would fail this gate on somebody else's debt.
#   (c) SHELLCHECK  — reports whether actionlint's shellcheck rule actually RAN. Without
#                     shellcheck on PATH actionlint silently DISABLES that rule, so the `run:`
#                     blocks — where the real deploy logic lives — go unlinted while the
#                     summary still says "0 errors". Silent weakening, surfaced.
#   (d) GRAPH       — the SC3 structural proof: pytest over the needs-graph parse.
#
# Exit code: 0 when FAIL == 0, 1 when FAIL > 0.
#
# Usage:
#   bash infrastructure/scripts/verify-phase17-workflow.sh
#   make verify-phase17-workflow
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# Requires: bash, python3 (or python) with pytest + PyYAML. curl only on first run.
#
# Host note: the cache lives at ${XBRAIN_TOOL_CACHE:-$HOME/.cache/xbrain} — deliberately
# OUTSIDE the working tree, so a downloaded binary can never be accidentally committed and
# no .gitignore entry is needed. On Windows the binaries carry a .exe suffix; both names are
# resolved.

set -uo pipefail   # NOT -e — every check runs independently; the summary line is the truth

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { yellow "  SKIPPED: $*"; SKIP=$((SKIP+1)); }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$(command -v python3 || command -v python || true)"
TOOL_CACHE="${XBRAIN_TOOL_CACHE:-$HOME/.cache/xbrain}"
WORKFLOW=".github/workflows/ci-lockstep.yml"
GRAPH_TEST="tests/test_ci_lockstep_graph.py"

ACTIONLINT=""
LINT_OUT=""

echo "=== Phase 17 Verification — WORKFLOW lint + needs-graph proof (REL-01, REL-02) ==="
echo "    target: $WORKFLOW"

if [[ ! -f "$WORKFLOW" ]]; then
  ko "$WORKFLOW is missing — there is no pipeline to verify"
  echo; echo "=== Summary ==="; echo "PASS: $PASS / $((PASS+FAIL))  (SKIP: $SKIP)"; exit 1
fi

# =============================================================================================
# (a) TOOLING — find or fetch actionlint.
# =============================================================================================
resolve_actionlint() {
  local candidate
  # 1. Already on PATH (CI images, a dev who installed it).
  if command -v actionlint >/dev/null 2>&1; then
    command -v actionlint
    return 0
  fi
  # 2. Previously cached, or dropped at the repo root by the upstream install script.
  for candidate in "$TOOL_CACHE/actionlint" "$TOOL_CACHE/actionlint.exe" \
                   "$REPO_ROOT/actionlint" "$REPO_ROOT/actionlint.exe"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  # 3. Fetch it. The upstream installer drops the binary in $PWD, so run it inside the cache.
  mkdir -p "$TOOL_CACHE" || return 1
  ( cd "$TOOL_CACHE" \
      && curl -fsSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash \
       | bash ) >/dev/null 2>&1 || return 1
  for candidate in "$TOOL_CACHE/actionlint" "$TOOL_CACHE/actionlint.exe"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

test_a_tooling() {
  echo
  echo "(a) TOOLING — resolve actionlint (cache: $TOOL_CACHE, outside the working tree)"

  ACTIONLINT="$(resolve_actionlint || true)"
  if [[ -n "$ACTIONLINT" && -x "$ACTIONLINT" ]]; then
    ok "(a) actionlint available: $ACTIONLINT ($("$ACTIONLINT" --version 2>/dev/null | head -1))"
    return 0
  fi

  ACTIONLINT=""
  # Genuinely could not obtain it. Degrade to a pure YAML parse and SAY SO — a syntax check is
  # strictly weaker than actionlint (it cannot see an unknown input, a bad expression, or a
  # broken `needs:` reference). This is the only path on which the lint check is not a hard gate.
  yellow "      NOTE: actionlint could not be resolved or downloaded (offline?)."
  yellow "            Falling back to a YAML syntax check only. This is WEAKER: it validates that"
  yellow "            the file parses, NOT that it is a valid workflow. Re-run with network access"
  yellow "            before trusting this gate. Full actionlint validation remains residual here."
  if [[ -z "$PY" ]]; then
    ko "(a) neither actionlint nor python is available — nothing can validate the workflow"
    return 1
  fi
  if PYTHONIOENCODING=utf-8 "$PY" -c "
import sys, yaml
with open(sys.argv[1], encoding='utf-8') as fh:
    yaml.safe_load(fh)
" "$WORKFLOW" 2>/dev/null; then
    skip "(a) actionlint unavailable; workflow parses as YAML (fallback only — see NOTE above)"
  else
    ko "(a) $WORKFLOW is not even valid YAML"
    return 1
  fi
}

# =============================================================================================
# (b) ACTIONLINT — zero findings, scoped to this phase's file.
# =============================================================================================
test_b_actionlint() {
  echo
  echo "(b) ACTIONLINT — zero findings on $WORKFLOW (scoped to this file; see deferred-items.md)"

  if [[ -z "$ACTIONLINT" ]]; then
    skip "(b) actionlint unavailable — see the NOTE in (a); this is the documented weaker fallback"
    return 0
  fi

  # Cache the shellcheck we may have fetched so actionlint can find it (see check (c)).
  PATH="$TOOL_CACHE:$PATH"
  export PATH

  LINT_OUT="$("$ACTIONLINT" -verbose "$WORKFLOW" 2>&1)"
  local rc=$?

  # Both conditions matter: a non-zero exit AND any finding line. actionlint prints findings as
  # `file:line:col: message`, so the exit code alone is the assertion and this is the receipt.
  local findings
  findings="$(printf '%s\n' "$LINT_OUT" | grep -E '^[^ ]+\.ya?ml:[0-9]+:[0-9]+:' || true)"

  if [[ $rc -eq 0 && -z "$findings" ]]; then
    ok "(b) actionlint: 0 findings on $WORKFLOW"
  else
    ko "(b) actionlint reported findings on $WORKFLOW (exit=$rc) — a lint finding is a FAIL, not a warning:"
    printf '%s\n' "${findings:-$LINT_OUT}" | sed 's/^/        /'
    return 1
  fi
}

# =============================================================================================
# (c) SHELLCHECK — did the run:-block linting actually happen?
# =============================================================================================
test_c_shellcheck_rule() {
  echo
  echo "(c) SHELLCHECK — was actionlint's shellcheck rule active (are the run: blocks really linted)?"

  if [[ -z "$ACTIONLINT" || -z "$LINT_OUT" ]]; then
    skip "(c) actionlint did not run — nothing to report"
    return 0
  fi

  # actionlint announces every rule it turns off, e.g.
  #   verbose: Rule "shellcheck" was disabled: exec: "shellcheck": executable file not found
  # Without that rule the deploy script inside ci-lockstep.yml is NEVER shell-linted, yet the
  # summary still reads "0 errors". Report it rather than let a weaker run look identical.
  if printf '%s\n' "$LINT_OUT" | grep -q 'Rule "shellcheck" was disabled'; then
    yellow "      NOTE: shellcheck is not on PATH, so actionlint SKIPPED shell linting of the run: blocks."
    yellow "            The 0-findings result in (b) therefore covers workflow structure only, not shell"
    yellow "            correctness. Install shellcheck for the stronger check (GitHub's ubuntu-latest"
    yellow "            runners ship it, so CI gets the full rule set)."
    skip "(c) shellcheck rule was disabled — (b) ran in its weaker, structure-only mode"
  else
    ok "(c) shellcheck rule active — the run: blocks were genuinely shell-linted"
  fi
}

# =============================================================================================
# (d) GRAPH — the SC3 structural proof.
# =============================================================================================
test_d_graph_proof() {
  echo
  echo "(d) GRAPH — needs-graph parse proves SC3: both ship jobs gate on all three test jobs"

  if [[ -z "$PY" ]]; then
    # SKIP=FAIL: this is a pure YAML parse with no daemon and no network. "No python" is a
    # broken host, not a valid reason to let the lockstep proof go unrun.
    ko "(d) no python3/python on PATH — the SC3 proof cannot run (SKIP=FAIL)"
    return 1
  fi
  if [[ ! -f "apps/memory-api/$GRAPH_TEST" ]]; then
    ko "(d) apps/memory-api/$GRAPH_TEST is missing — SC3 would be unproven"
    return 1
  fi

  local out rc
  out="$(cd apps/memory-api && "$PY" -m pytest "$GRAPH_TEST" -q -rs 2>&1)"
  rc=$?

  if [[ $rc -ne 0 ]]; then
    ko "(d) the needs-graph proof FAILED — lockstep is not structurally enforced:"
    printf '%s\n' "$out" | tail -25 | sed 's/^/        /'
    return 1
  fi

  # SKIP=FAIL again: these assertions have no fixtures to be unavailable, so a skip can only
  # mean the gate was dodged. Without this, a collection problem could report green.
  if printf '%s\n' "$out" | grep -qiE '[0-9]+ skipped'; then
    ko "(d) the needs-graph proof reported SKIPS. It needs no Docker and no network, so a skip means the SC3 gate did not run. SKIP != PASS:"
    printf '%s\n' "$out" | tail -15 | sed 's/^/        /'
    return 1
  fi

  local summary
  summary="$(printf '%s\n' "$out" | grep -E '[0-9]+ passed' | tail -1)"
  ok "(d) needs-graph proof: ${summary:-passed}"
}

# =============================================================================================
# (e) SCOPE NOTE — say out loud what a green run here does and does not prove.
# =============================================================================================
test_e_scope_note() {
  echo
  echo "(e) SCOPE — what this gate covers, and what it deliberately does not"
  echo "      COVERED (static, no daemon, no live run needed):"
  echo "        - ci-lockstep.yml is valid workflow YAML that actionlint accepts with zero findings"
  echo "        - SC3 lockstep is structural: publish AND deploy both gate on all three test jobs"
  echo "        - deploy is SAAS_DEPLOY_ENABLED-gated, never rebuilds, actions pinned, no fork-PR trigger"
  echo "      NOT COVERED (the documented residual — docs/ci-lockstep.md):"
  echo "        - a real GitHub-Actions run. This workflow has never executed."
  echo "        - the GHCR push, and the one-time per-package visibility flip it requires."
  echo "        - the live SaaS deploy: the prod VM is stopped and SAAS_DEPLOY_ENABLED is unset."
  echo "        - whether the full-profile boot fits a hosted runner's disk (workflow_dispatch follow-up)."
}

test_a_tooling
test_b_actionlint
test_c_shellcheck_rule
test_d_graph_proof
test_e_scope_note

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi

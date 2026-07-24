#!/usr/bin/env bash
#
# verify-phase26.sh — Phase 26a (Collaborative Board, BOARD-01) acceptance gate.
#
# The gate lesson made executable. "Two people can draw together" is not provable by a
# unit test of a React component, and "the team-scope boundary holds" is not provable by a
# mocked verifier. This gate boots the REAL board profile and proves, all NON-MOCKED:
#
#   (a) two Yjs clients against the REAL running Hocuspocus converge on IDENTICAL document
#       content (asserted on the content, not on "no error");
#   (b) the team-scope rejection matrix runs against the REAL onAuthenticate with REAL
#       tokens minted by the REAL memory-api Python code (mint_board_token) — the
#       cross-language agreement IS the property; a Node-minted token would only prove Node
#       agrees with itself;
#   (c) a board's Y.Doc content survives a real `docker compose restart hocuspocus`;
#   (d) verify-phase16.sh is STILL GREEN at the end of the phase (D-26-04);
#   (e) both images actually BUILD via their multi-stage Dockerfiles.
#
# SKIP=FAIL. Docker is up on this host — a skipped check is a FAILURE, because a gate that
# goes green without a live socket, a live verifier and a live database is exactly the
# defect class this gate exists to catch. If the boot fails, every downstream check is
# recorded as a FAILURE (`ko`), never a skip.
#
# Concrete checks:
#   (a) IMAGE BUILD    — `docker compose --profile board build board hocuspocus` succeeds;
#                        image sizes logged.
#   (b) BOOT           — oss-init zero-key env + board knobs -> COMPOSE up -d --build the
#                        10 core PLUS board + hocuspocus; poll ACTUAL docker health.
#   (c) REAL TOKENS    — register two accounts + a board each over REAL HTTP through nginx,
#                        then mint TOKEN_A/TOKEN_B (+ an expired + a wrong-signature token)
#                        with the REAL Python mint_board_token via `docker compose exec`.
#   (d) LIVE CLIENT    — run the Task-2 driver INSIDE the compose network (ws://hocuspocus:8108):
#                        two-client convergence + the full rejection matrix + a positive control.
#   (e) RESTART        — `docker compose restart hocuspocus`, then a fresh client (fresh
#                        Python-minted token) must still see the prior board content.
#   (f) NO RAW PORT    — neither xbrain-hocuspocus nor xbrain-board publishes a host port
#                        (HostConfig.PortBindings empty) — the ingress is the only route.
#   (g) PHASE-16 GREEN — tear THIS stack down, then run verify-phase16.sh and require exit 0.
#
# Exit code: 0 when FAIL == 0 (and, on a healthy host, SKIP == 0), 1 when FAIL > 0.
#
# Usage:
#   bash infrastructure/scripts/verify-phase26.sh
#   make verify-phase26
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# Host notes (load-bearing for THIS gate's trustworthiness):
#   THE HOST-PATH RULE: `-f` and `--env-file` take HOST paths and MUST be MSYS-converted,
#   so NEVER set MSYS_NO_PATHCONV=1 on a `docker compose ...` command carrying them.
#   MSYS_NO_PATHCONV=1 belongs ONLY on commands carrying an IN-CONTAINER path (the
#   `docker run ... --entrypoint node ... tests/gate_client.mjs`, the `docker build <ctx>`).
#   container_name: is GLOBAL (xbrain-*), NOT namespaced by `-p`, so this gate refuses to
#   boot if any xbrain-* container already exists (it would clobber a running stack).

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

PY="$(command -v python3 || command -v python || true)"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

echo "=== Phase 26a Verification (Collaborative Board — BOARD-01) ==="

PROJECT="xbrain-p26"
GATE_IMAGE="xbrain/hocuspocus-gate:phase26"

# The 10 OSS-light core services + the two opt-in board-profile services.
CORE="brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant"
BOARD_SVCS="board hocuspocus"

# --- Temp files (declared up front so the single EXIT trap always cleans them) ----------
OSS_ENV="$(mktemp)"          # the generated zero-external-key env (+ appended board knobs)
BOOT_LOG="$(mktemp)"
JSON_HELPER="$(mktemp).py"   # stdin JSON -> a dotted key (python, not jq)
PORTS_HELPER="$(mktemp).py"  # stdin `docker inspect` JSON -> PortBindings emptiness verdict
MINT_PY="$(mktemp).py"       # the in-container minting script (fed to python via stdin)
TOK_FILE="$(mktemp)"         # the minted-tokens JSON (secrets — never echoed)
CLIENT_LOG="$(mktemp)"       # the live driver's full output

# The live boot invocation. `--profile board` is a top-level flag so it applies to up / ps
# / exec / restart / down alike; --env-file/-f are HOST paths (do NOT MSYS-suppress them).
DC=(docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml --env-file "$OSS_ENV" --profile board)

BOOTED=0
API_HOST=""

cleanup() {
  # NO MSYS_NO_PATHCONV here: `-f`/`--env-file` are HOST paths MSYS must rewrite; suppressing
  # it makes docker resolve /tmp/x as D:\tmp\x and silently no-op the teardown (leaking the
  # whole stack). MSYS_NO_PATHCONV=1 is only ever correct for IN-CONTAINER paths.
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1
  rm -f "$OSS_ENV" "$BOOT_LOG" "$JSON_HELPER" "$PORTS_HELPER" "$MINT_PY" "$TOK_FILE" "$CLIENT_LOG"
}
trap cleanup EXIT

# --- python helpers (data via stdin, keys via argv — never a path baked into -c) ---------
cat > "$JSON_HELPER" <<'PYEOF'
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)
cur = data
for part in sys.argv[1].split("."):
    if isinstance(cur, list):
        try:
            cur = cur[int(part)]
        except (ValueError, IndexError):
            cur = None
    elif isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
    if cur is None:
        break
print("" if cur is None else cur)
PYEOF

# PortBindings verdict: reads the `docker inspect` array of BOTH containers and prints
# OK only if EVERY container has an empty (null or {}) HostConfig.PortBindings. A single
# published host port is a FAIL — that would bypass the nginx ingress + onAuthenticate.
cat > "$PORTS_HELPER" <<'PYEOF'
import json
import sys

try:
    arr = json.load(sys.stdin)
except Exception as exc:
    print(f"BAD could not parse docker inspect JSON: {exc}")
    sys.exit(0)
if not isinstance(arr, list) or not arr:
    print("BAD docker inspect returned no containers")
    sys.exit(0)
bad = []
for c in arr:
    name = c.get("Name", "?").lstrip("/")
    pb = (c.get("HostConfig") or {}).get("PortBindings")
    if pb:  # non-empty dict == at least one published host port
        bad.append(f"{name}={pb}")
if bad:
    print("BAD published host ports: " + "; ".join(bad))
else:
    print("OK no host port bindings on either container")
PYEOF

# The REAL minting script — runs INSIDE the memory-api container, imports the REAL
# mint_board_token, and prints one marker line of JSON. It resolves each board's team + a
# member's source_user_id from the live DB (the same rows the HTTP create wrote), mints
# TOKEN_A / TOKEN_B, an already-EXPIRED token (ttl=-60), and a WRONG-SIGNATURE token
# (the same claims re-signed with a different secret). It MINTS with the production code —
# a Node-minted or shell-minted token would defeat the whole cross-language proof.
cat > "$MINT_PY" <<'PYEOF'
import asyncio
import json
import os
from uuid import UUID

from app.config import settings
from app.db.session import async_session_factory
from app.repos import boards as boards_repo
from app.repos import teams as teams_repo
from app.routes.board_helpers import mint_board_token

BOARD_A = os.environ["BOARD_A_ID"]
BOARD_B = os.environ["BOARD_B_ID"]


async def _resolve(board_id):
    async with async_session_factory() as s:
        board = await boards_repo.get_board(s, board_id=UUID(board_id))
        if board is None:
            raise SystemExit(f"board {board_id} not found in DB")
        team = await teams_repo.get_team_by_id(s, board.team_id)
        members = await teams_repo.list_members_with_user_info(s, team_id=team.id)
        if not members:
            raise SystemExit(f"team {team.id} has no members")
        sub = members[0][1].source_user_id
        return str(board.id), team.slug, str(team.id), sub


def _resign_wrong_secret(token):
    # Re-sign the SAME payload with a DIFFERENT secret via authlib (never hand-rolled HMAC).
    from authlib.jose import jwt as ajwt
    claims = ajwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    bad = ajwt.encode({"alg": "HS256"}, dict(claims), settings.BRIDGE_SHARED_SECRET + "-WRONG")
    return bad.decode("ascii") if isinstance(bad, bytes) else bad


async def main():
    a_id, a_slug, a_team, a_sub = await _resolve(BOARD_A)
    b_id, b_slug, b_team, b_sub = await _resolve(BOARD_B)

    token_a = mint_board_token(board_id=a_id, team_scope=a_slug, team_id=a_team, sub=a_sub)
    token_b = mint_board_token(board_id=b_id, team_scope=b_slug, team_id=b_team, sub=b_sub)
    token_a_expired = mint_board_token(
        board_id=a_id, team_scope=a_slug, team_id=a_team, sub=a_sub, ttl=-60
    )
    token_a_badsig = _resign_wrong_secret(token_a)

    print("GATE_TOKENS_JSON=" + json.dumps({
        "board_a": a_id,
        "board_b": b_id,
        "token_a": token_a,
        "token_b": token_b,
        "token_a_expired": token_a_expired,
        "token_a_badsig": token_a_badsig,
    }))


asyncio.run(main())
PYEOF

# host_path <posix-path> — Windows path for a bind-mount on Git-Bash, else the path unchanged.
host_path() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) cygpath -m "$1" 2>/dev/null || echo "$1" ;;
    *)                    echo "$1" ;;
  esac
}

# --- HTTP-through-nginx plumbing (only nginx publishes :80; api reached via Host header) --
RESP="$(mktemp)"
HDRS="$(mktemp)"
req() {
  local method="$1" path="$2"; shift 2
  curl -sS --max-time 120 -o "$RESP" -D "$HDRS" -w '%{http_code}' \
    -X "$method" -H "Host: $API_HOST" "$@" "http://127.0.0.1${path}" 2>/dev/null
}
jget() { PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" "$1" < "$RESP" 2>/dev/null; }

# poll_health "svc1 svc2 ..." — wait until every named compose service is healthy/running.
poll_health() {
  local services="$1" deadline svc cid state pending
  deadline=$(( $(date +%s) + 600 ))
  while :; do
    pending=""
    for svc in $services; do
      cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null | head -1)"
      if [[ -z "$cid" ]]; then pending="$pending $svc(no-container)"; continue; fi
      state="$(docker inspect --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null)"
      case "$state" in healthy|running) ;; *) pending="$pending $svc($state)" ;; esac
    done
    [[ -z "$pending" ]] && return 0
    [[ "$(date +%s)" -ge "$deadline" ]] && { echo "      still pending:$pending"; return 1; }
    sleep 5
  done
}

# ========================================================================================
# (a) IMAGE BUILD — both multi-stage Dockerfiles build; log the resulting image sizes.
# ========================================================================================
test_a_image_build() {
  echo
  echo "(a) IMAGE BUILD — docker compose --profile board build board hocuspocus"
  if ! bash infrastructure/scripts/oss-init.sh --force "$OSS_ENV" >/dev/null 2>&1; then
    ko "(a) oss-init.sh could not write a zero-key env (needed for interpolation)"
    return 1
  fi
  # The board profile knobs (safe defaults; both containers read the SAME BRIDGE_SHARED_SECRET
  # already written by oss-init, so a Python-minted token verifies on the Node side).
  {
    echo ""
    echo "# --- Phase 26a board profile knobs ---"
    echo "BOARD_MAX_DOC_BYTES=16777216"
    echo "BOARD_PUBLIC_BASE_URL=http://board.localhost"
    echo "BOARD_WS_URL_PUBLIC=ws://board.localhost/collab"
    echo "BOARD_TOKEN_TTL_S=3600"
  } >> "$OSS_ENV"

  if ! "${DC[@]}" build board hocuspocus >"$BOOT_LOG" 2>&1; then
    ko "(a) image build failed. Tail:
$(tail -25 "$BOOT_LOG")"
    return 1
  fi
  ok "(a) both board-profile images built from their multi-stage Dockerfiles"

  local img sz
  for img in xbrain/board-web:phase26 xbrain/hocuspocus:phase26; do
    sz="$(docker image inspect "$img" --format '{{.Size}}' 2>/dev/null)"
    if [[ -n "$sz" ]]; then
      echo "      image $img: $(( sz / 1024 / 1024 )) MB"
    fi
  done
  return 0
}

# ========================================================================================
# (b) BOOT — up -d --build the 10 core + board + hocuspocus; assert ACTUAL docker health.
# ========================================================================================
test_b_boot() {
  echo
  echo "(b) BOOT — COMPOSE up -d --build the 10 core + board + hocuspocus; assert health"

  # container_name: is GLOBAL and fixed (xbrain-*), NOT namespaced by -p. Booting over a
  # live stack would collide on those names and tear down an operator's work — refuse.
  local running
  running="$(docker ps -a --filter 'name=^xbrain-' --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')"
  if [[ -n "${running// /}" ]]; then
    ko "(b) refusing to boot: xbrain-* containers already exist ($running). container_name: is
      global — booting would clobber a running stack. Bring it down first, then re-run.
      (SKIP=FAIL: recorded as a FAILURE, never a skip.)"
    return 1
  fi

  API_HOST="api.$(grep -E '^XBRAIN_BASE_DOMAIN=' "$OSS_ENV" | head -1 | cut -d= -f2-)"
  echo "      ingress: http://127.0.0.1:80 with Host: $API_HOST (only nginx publishes a host port)"

  BOOTED=1
  # NO MSYS_NO_PATHCONV — -f/--env-file are HOST paths and MUST be MSYS-converted.
  if ! "${DC[@]}" up -d --build >"$BOOT_LOG" 2>&1; then
    ko "(b) 'up -d --build' failed. Tail:
$(tail -30 "$BOOT_LOG")"
    return 1
  fi
  ok "(b) 'up -d --build' returned 0 (10 core + board + hocuspocus created)"

  if poll_health "$CORE $BOARD_SVCS"; then
    ok "(b) all 12 services reached healthy/running (10 core + board + hocuspocus)"
  else
    ko "(b) not all services became healthy within the deadline"
    local svc cid line
    for svc in $CORE $BOARD_SVCS; do
      cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null | head -1)"
      [[ -z "$cid" ]] && { echo "      $svc: absent"; continue; }
      line="$(docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid" 2>/dev/null)"
      echo "      $svc: $line"
    done
    echo "      --- hocuspocus logs (tail 20) ---"
    "${DC[@]}" logs --tail=20 hocuspocus 2>&1 | sed 's/^/        /'
    return 1
  fi
  return 0
}

# ========================================================================================
# (c) REAL TOKENS — register two accounts + a board each over REAL HTTP, then mint the four
#     tokens with the REAL Python mint_board_token via `docker compose exec`.
# ========================================================================================
BOARD_A=""; BOARD_B=""
TOKEN_A=""; TOKEN_B=""; TOKEN_A_EXPIRED=""; TOKEN_A_BADSIG=""

# seed_board <label> -> echoes "<xbt> <team_id> <board_id>" for a fresh account+board, all
# created over REAL HTTP through nginx (the same route a user walks).
seed_board() {
  local label="$1" email password code xbt team_id board_id
  email="p26-${label}-$(openssl rand -hex 5)@example.com"
  password="p26-$(openssl rand -hex 12)"

  code=$(req POST /v1/auth/local/register -H 'Content-Type: application/json' \
          --data "{\"email\":\"$email\",\"password\":\"$password\"}")
  xbt="$(jget xbt_token)"
  [[ "$code" == "200" && "$xbt" == xbt_* ]] || { echo "REGERR:$code"; return 1; }

  code=$(req GET /v1/teams/my-team -H "Authorization: Bearer $xbt")
  team_id="$(jget id)"
  [[ "$code" == "200" && -n "$team_id" ]] || { echo "MYTEAMERR:$code"; return 1; }

  code=$(req POST "/v1/teams/${team_id}/boards" -H "Authorization: Bearer $xbt" \
          -H 'Content-Type: application/json' --data '{"title":"Gate board"}')
  board_id="$(jget id)"
  [[ "$code" == "201" && -n "$board_id" ]] || { echo "BOARDERR:$code"; return 1; }

  echo "$xbt $team_id $board_id"
}

# mint_tokens — run the REAL minter in-container and set TOKEN_* from its JSON. A comment
# to keep the grep honest about what runs: this uses `docker compose exec -T memory-api
# python` calling the production mint_board_token — NEVER a Node- or shell-minted token.
mint_tokens() {
  local raw
  # NO MSYS_NO_PATHCONV: the DC array carries -f/--env-file HOST paths. The python script is
  # fed on stdin and the -e values are UUIDs (no slashes), so nothing needs MSYS suppression.
  raw="$("${DC[@]}" exec -T -e BOARD_A_ID="$BOARD_A" -e BOARD_B_ID="$BOARD_B" \
          memory-api python - < "$MINT_PY" 2>/dev/null \
          | grep '^GATE_TOKENS_JSON=' | head -1 | sed 's/^GATE_TOKENS_JSON=//')"
  [[ -n "$raw" ]] || return 1
  printf '%s' "$raw" > "$TOK_FILE"
  TOKEN_A="$(PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" token_a < "$TOK_FILE")"
  TOKEN_B="$(PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" token_b < "$TOK_FILE")"
  TOKEN_A_EXPIRED="$(PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" token_a_expired < "$TOK_FILE")"
  TOKEN_A_BADSIG="$(PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" token_a_badsig < "$TOK_FILE")"
  [[ -n "$TOKEN_A" && -n "$TOKEN_B" && -n "$TOKEN_A_EXPIRED" && -n "$TOKEN_A_BADSIG" ]]
}

test_c_real_tokens() {
  echo
  echo "(c) REAL TOKENS — HTTP register + board create, then mint via the REAL Python code"

  local a b
  a="$(seed_board a)" || { ko "(c) could not seed team A's account+board over HTTP: $a"; return 1; }
  b="$(seed_board b)" || { ko "(c) could not seed team B's account+board over HTTP: $b"; return 1; }
  BOARD_A="$(echo "$a" | awk '{print $3}')"
  BOARD_B="$(echo "$b" | awk '{print $3}')"
  ok "(c) seeded two teams + a board each over REAL HTTP through nginx (board_a=$BOARD_A board_b=$BOARD_B)"

  if ! mint_tokens; then
    ko "(c) minting via 'docker compose exec memory-api python' (mint_board_token) failed"
    return 1
  fi
  # Never echo a token. Prove they are DISTINCT and bound to DIFFERENT boards, so the gate
  # cannot accidentally use one token twice and prove nothing.
  if [[ "$TOKEN_A" == "$TOKEN_B" ]]; then
    ko "(c) TOKEN_A == TOKEN_B — the two tokens are identical; the matrix would prove nothing"
    return 1
  fi
  local claim_a claim_b
  claim_a="$(PYTHONIOENCODING=utf-8 "$PY" -c 'import sys,json,base64; p=sys.argv[1].split(".")[1]; p+="="*(-len(p)%4); print(json.loads(base64.urlsafe_b64decode(p))["board_id"])' "$TOKEN_A" 2>/dev/null)"
  claim_b="$(PYTHONIOENCODING=utf-8 "$PY" -c 'import sys,json,base64; p=sys.argv[1].split(".")[1]; p+="="*(-len(p)%4); print(json.loads(base64.urlsafe_b64decode(p))["board_id"])' "$TOKEN_B" 2>/dev/null)"
  if [[ "$claim_a" == "$BOARD_A" && "$claim_b" == "$BOARD_B" && "$claim_a" != "$claim_b" ]]; then
    ok "(c) minted with the REAL mint_board_token: TOKEN_A/TOKEN_B distinct and bound to different board_id claims (+ an expired + a wrong-signature token)"
  else
    ko "(c) minted tokens' board_id claims are wrong: A=$claim_a (want $BOARD_A) B=$claim_b (want $BOARD_B)"
    return 1
  fi
  return 0
}

# ========================================================================================
# (d) LIVE CLIENT — build the gate image and run the Task-2 driver INSIDE the compose network.
# ========================================================================================
test_d_live_client() {
  echo
  echo "(d) LIVE CLIENT — two-client convergence + the rejection matrix inside xbrain_net"

  # MSYS_NO_PATHCONV=1 is correct here: `docker build <ctx>` carries no host-to-container mount
  # to convert, and it prevents Git-Bash from mangling the build target/context.
  if ! MSYS_NO_PATHCONV=1 docker build --target gate -t "$GATE_IMAGE" apps/hocuspocus >"$BOOT_LOG" 2>&1; then
    ko "(d) gate image build (--target gate) failed. Tail:
$(tail -15 "$BOOT_LOG")"
    return 1
  fi

  # MSYS_NO_PATHCONV=1 is correct: this command carries an IN-CONTAINER path
  # (tests/gate_client.mjs) and a ws:// URL, and NO -f/--env-file host path.
  MSYS_NO_PATHCONV=1 docker run --rm --network xbrain_net \
    -e HOCUSPOCUS_URL=ws://hocuspocus:8108 \
    -e BOARD_A="$BOARD_A" -e BOARD_B="$BOARD_B" \
    -e TOKEN_A="$TOKEN_A" -e TOKEN_B="$TOKEN_B" \
    -e TOKEN_A_EXPIRED="$TOKEN_A_EXPIRED" -e TOKEN_A_BADSIG="$TOKEN_A_BADSIG" \
    --entrypoint node "$GATE_IMAGE" tests/gate_client.mjs >"$CLIENT_LOG" 2>&1
  local rc=$?

  echo "      --- gate client output ---"
  sed 's/^/      /' "$CLIENT_LOG"

  # Log the hocuspocus RSS while the document is loaded (research assumption A1 / OQ6).
  local mem
  mem="$(docker stats --no-stream --format '{{.MemUsage}}' xbrain-hocuspocus 2>/dev/null | head -1)"
  [[ -n "$mem" ]] && echo "      hocuspocus RSS (document loaded, post-live-client): $mem"

  if [[ "$rc" -eq 0 ]] && grep -q 'GATE-CLIENT SUMMARY pass=' "$CLIENT_LOG" && ! grep -q ' fail=0' "$CLIENT_LOG"; then
    # Belt and suspenders: rc==0 already means fail==0 and pass>0 (the driver enforces it).
    :
  fi
  if [[ "$rc" -eq 0 ]]; then
    ok "(d) live driver exit 0 — convergence + full rejection matrix + positive control all green: $(grep 'GATE-CLIENT SUMMARY' "$CLIENT_LOG" | head -1)"
  else
    ko "(d) live driver exit $rc — see the PASS/FAIL lines above ($(grep 'GATE-CLIENT SUMMARY' "$CLIENT_LOG" | head -1))"
    return 1
  fi
  return 0
}

# ========================================================================================
# (e) RESTART PERSISTENCE — restart hocuspocus, then a fresh client must still see e1 + e2.
# ========================================================================================
test_e_restart_persistence() {
  echo
  echo "(e) RESTART — docker compose restart hocuspocus; a fresh client still sees the content"

  # NO MSYS_NO_PATHCONV — the DC array carries -f/--env-file host paths.
  if ! "${DC[@]}" restart hocuspocus >/dev/null 2>&1; then
    ko "(e) 'restart hocuspocus' failed"
    return 1
  fi
  if ! poll_health "hocuspocus"; then
    ko "(e) hocuspocus did not become healthy again after the restart"
    return 1
  fi

  # Mint a FRESH token through the same REAL Python path (mint_tokens re-mints all four).
  if ! mint_tokens; then
    ko "(e) could not mint a fresh token after the restart"
    return 1
  fi

  # verify-persisted mode connects ONE fresh client and asserts BOTH prior elements survived.
  MSYS_NO_PATHCONV=1 docker run --rm --network xbrain_net \
    -e HOCUSPOCUS_URL=ws://hocuspocus:8108 -e BOARD_A="$BOARD_A" \
    -e TOKEN_A="$TOKEN_A" -e GATE_MODE=verify-persisted \
    --entrypoint node "$GATE_IMAGE" tests/gate_client.mjs >"$CLIENT_LOG" 2>&1
  local rc=$?
  echo "      --- post-restart read output ---"
  sed 's/^/      /' "$CLIENT_LOG"
  if [[ "$rc" -eq 0 ]]; then
    ok "(e) after 'restart hocuspocus', a fresh client still sees the board content (fetch/store round-trip survived)"
  else
    ko "(e) the board content did NOT survive the restart (driver exit $rc)"
    return 1
  fi
  return 0
}

# ========================================================================================
# (f) NO RAW EXPOSURE — neither new container publishes a host port.
# ========================================================================================
test_f_no_raw_port() {
  echo
  echo "(f) NO RAW PORT — xbrain-hocuspocus + xbrain-board must have empty HostConfig.PortBindings"
  local verdict
  verdict="$(docker inspect xbrain-hocuspocus xbrain-board 2>/dev/null | PYTHONIOENCODING=utf-8 "$PY" "$PORTS_HELPER")"
  if [[ "$verdict" == OK* ]]; then
    ok "(f) $verdict — the Yjs socket is reachable only through the nginx ingress"
  else
    ko "(f) $verdict"
    return 1
  fi
  return 0
}

# ========================================================================================
# (g) PHASE-16 STILL GREEN — tear THIS stack down first, then require verify-phase16 exit 0.
# ========================================================================================
test_g_phase16_green() {
  echo
  echo "(g) PHASE-16 GREEN — tear down this stack, then verify-phase16.sh must exit 0 (D-26-04)"

  # verify-phase16.sh refuses to boot over a live stack (container_name: is global), so THIS
  # stack must be fully down first. NO MSYS_NO_PATHCONV — host paths.
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1
  BOOTED=0

  local p16_log; p16_log="$(mktemp)"
  bash infrastructure/scripts/verify-phase16.sh >"$p16_log" 2>&1
  local rc=$?
  local summary; summary="$(grep -E '^PASS: [0-9]+ / [0-9]+' "$p16_log" | tail -1)"
  echo "      verify-phase16 summary: ${summary:-<none>}"
  if [[ "$rc" -eq 0 ]]; then
    ok "(g) verify-phase16.sh exited 0 — the OSS-light core is STILL green with the board profile shipped"
  else
    ko "(g) verify-phase16.sh exited $rc — a red Phase-16 gate at the end of Phase 26 is a FAILURE"
    echo "      --- verify-phase16 tail (25) ---"
    tail -25 "$p16_log" | sed 's/^/        /'
  fi
  rm -f "$p16_log"
  return 0
}

# --- Orchestration: SKIP=FAIL — a boot failure makes every downstream check a FAILURE ----
test_a_image_build
if test_b_boot; then
  test_c_real_tokens
  test_d_live_client
  test_e_restart_persistence
  test_f_no_raw_port
else
  red "  The board profile did not boot — (c)-(f) cannot run. Recording them as FAILURES, not SKIPs"
  red "  (SKIP=FAIL: a gate that goes green without a live board stack is the defect this gate catches)."
  ko "(c) REAL tokens from mint_board_token — not exercised: the stack never booted"
  ko "(d) LIVE two-client convergence + rejection matrix — not exercised: the stack never booted"
  ko "(e) RESTART persistence — not exercised: the stack never booted"
  ko "(f) NO raw host port — not exercised: the stack never booted"
fi
# (g) runs regardless — Phase 16 must be green even if the board profile failed to boot.
test_g_phase16_green

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"
echo "FAIL: $FAIL"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi

#!/usr/bin/env bash
#
# verify-phase27.sh — Phase 27 (PWA + Web Push, PWA-01 + PUSH-01) acceptance gate.
#
# A PWA that renders locally proves nothing (27-CONTEXT). Every claim this phase makes
# is about something that only exists once the app is DEPLOYED and the API is RUNNING:
#
#   * a manifest and a service worker are only real when a browser can fetch them from
#     the origin the app is installed from;
#   * CORS is invisible until a browser actually sends the preflight — a config that
#     "looks right" and a config that works are different statements;
#   * realtime is invisible until a SECOND client receives something it never asked for;
#   * a push path is invisible until an encrypted body reaches a real socket and a dead
#     endpoint is actually deleted.
#
# So nothing here is proven from a local checkout. Every surface check curls the deployed
# https origin, the two probes drive a real websocket and a real push socket, and the
# server-side checks run inside the container that serves production traffic.
#
# SKIP=FAIL. A skipped check is a FAILURE, because a gate that goes green without
# touching the deployed origin is exactly the defect class this gate exists to catch.
# Nothing below is conditional on a file, a container or a credential being available:
# when one is missing, the check records `ko` and names precisely what to export or
# start. `skip()` is defined for house-style symmetry and is deliberately never called —
# the exit condition enforces SKIP == 0 so that a future edit cannot quietly re-open the
# door.
#
# Checks:
#   (a) MANIFEST, DEPLOYED  — fetched over https, parsed, start_url/scope/display/
#                             theme_color asserted, and EVERY icon src fetched (200 +
#                             image/png), including the maskable one.
#   (b) SERVICE WORKER      — fetched over https; javascript + no-cache headers; the
#                             SERVED body, with comments stripped, carries the push,
#                             notificationclick and pushsubscriptionchange handlers and
#                             all four non-caching guards, with the /v1/ guard returning
#                             BEFORE the first respondWith.
#   (c) START URL           — /app/ serves 200, links the manifest, registers the worker,
#                             and raises no permission prompt on load (D-27-05).
#   (d) CORS                — the real browser preflight from the app origin, PLUS a
#                             negative control from a foreign origin. A permissive config
#                             that passes the positive test is not proof.
#   (e) SIGN-IN -> CHAT     — the API walk the PWA performs, every request carrying the
#                             app Origin.
#   (f) TWO-CLIENT REALTIME — one client posts over HTTP, a different client receives it
#                             on the socket, asserted on the message CONTENT.
#   (g) PUSH SEND + PRUNE   — real pywebpush encryption against a real socket inside the
#                             API container, and the exact 404/410/500 prune matrix.
#   (h) PUSH CONFIG         — the deployed config endpoint serves a valid PUBLIC VAPID
#                             key and no trace of the private half.
#   (i) STATIC SUITES       — the extension suite is still green (D-27-04) and the shared
#                             chat-core copies have not drifted.
#   (j) SERVER UNIT SUITE   — the push + mention tests, run inside the API container when
#                             that image can host them and against the checkout otherwise;
#                             never skipped, and the runner used is printed.
#   (k) MIGRATION HEAD      — 0029_push_subscriptions (or later) is applied where the API
#                             actually runs.
#
# Exit code: 0 only when FAIL == 0 AND SKIP == 0. 1 otherwise.
#
# Usage:
#   bash infrastructure/scripts/verify-phase27.sh
#   make verify-phase27
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# The surface under test, by default:
#   https://grooveos.app/app/
#   https://grooveos.app/app/manifest.webmanifest
#   https://grooveos.app/app/sw.js
#   https://grooveos.app/app/icons/icon-{192,512}.png, icon-maskable-512.png
#   https://api.grooveos.app                       (memory-api behind nginx on the VM)
# Override with VERIFY_SITE_BASE / VERIFY_API_BASE to point at a staging origin.
#
# Credentials (a missing one is a FAILURE, not a skip):
#   VERIFY_XBT_TOKEN    a real xbt_ token for a member of VERIFY_TEAM_ID
#   VERIFY_TEAM_ID      the team UUID both probe clients use
#   VERIFY_XBT_TOKEN_2  OPTIONAL second member's token; without it the realtime probe
#                       uses two independent connections for the same account and says so
#
# Host notes (load-bearing for this gate's trustworthiness, carried from Phase 26):
#   THE HOST-PATH RULE: `-f` and `--env-file` take HOST paths and MUST be MSYS-converted,
#   so NEVER set MSYS_NO_PATHCONV=1 on a `docker compose ...` command carrying them.
#   MSYS_NO_PATHCONV=1 belongs ONLY on commands carrying an IN-CONTAINER path.
#   JSON is parsed with python, never jq.
#   No `curl -v`: the token and the minted client token must not reach the log.

set -uo pipefail   # NOT -e — every check runs independently; the summary line is the truth

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
# Defined for symmetry with the other gates and never called — see the header.
skip() { yellow "  SKIPPED: $*"; SKIP=$((SKIP+1)); }

PY="$(command -v python3 || command -v python || true)"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

echo "=== Phase 27 Verification (PWA + Web Push — PWA-01 + PUSH-01) ==="

SITE="${VERIFY_SITE_BASE:-https://grooveos.app}"
API="${VERIFY_API_BASE:-https://api.grooveos.app}"
SITE="${SITE%/}"
API="${API%/}"

# The two probe programs, named EXACTLY once each so the plan's acceptance grep counts
# the invocation rather than the prose.
REALTIME_PROBE="infrastructure/scripts/phase27_realtime_probe.mjs"
PUSH_PROBE="infrastructure/scripts/phase27_push_probe.py"

echo "  site: $SITE"
echo "  api:  $API"

# --- Temp files (declared up front so the single EXIT trap always clears them) ----------
RESP="$(mktemp)"          # response body of the last req()
HDRS="$(mktemp)"          # response headers of the last req()
JSON_HELPER="$(mktemp).py"
MANIFEST_HELPER="$(mktemp).py"
KEY_HELPER="$(mktemp).py"
SW_RAW="$(mktemp)"
SW_CODE="$(mktemp)"
HTML_RAW="$(mktemp)"
HTML_CODE="$(mktemp)"
PROBE_LOG="$(mktemp)"     # probe output (never a credential — the probes redact)
CFG_BODY="$(mktemp)"      # /v1/push/config body, compared against the private key

cleanup() {
  rm -f "$RESP" "$HDRS" "$JSON_HELPER" "$MANIFEST_HELPER" "$KEY_HELPER" \
        "$SW_RAW" "$SW_CODE" "$HTML_RAW" "$HTML_CODE" "$PROBE_LOG" "$CFG_BODY"
}
trap cleanup EXIT

# The deployment's compose invocation. `-f`/`--env-file` are HOST paths: do NOT
# MSYS-suppress them. `--env-file` is only added when the file is there, so a host that
# keeps its deployment env elsewhere gets a real error from docker rather than a
# spurious one from this gate.
DC=(docker compose -f infrastructure/docker-compose.yml)
[ -f .env ] && DC+=(--env-file .env)

# --- helpers ---------------------------------------------------------------------------

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

# Manifest verdict: stdin is the served manifest, stdout is `key=value` lines the shell
# asserts on, plus one `icon=<src>` line per declared icon so the fetch loop is driven by
# what the DEPLOYED file actually declares rather than by a list written here.
cat > "$MANIFEST_HELPER" <<'PYEOF'
import json
import sys

try:
    m = json.load(sys.stdin)
except Exception as exc:
    print(f"parse_error={exc}")
    sys.exit(0)
if not isinstance(m, dict):
    print("parse_error=the manifest is not a JSON object")
    sys.exit(0)

print(f"start_url={m.get('start_url', '')}")
print(f"scope={m.get('scope', '')}")
print(f"display={m.get('display', '')}")
print(f"theme_color={m.get('theme_color', '')}")

icons = m.get("icons") or []
has192 = has512 = maskable = False
for icon in icons:
    if not isinstance(icon, dict):
        continue
    sizes = str(icon.get("sizes", ""))
    purpose = str(icon.get("purpose", ""))
    src = str(icon.get("src", ""))
    if "192x192" in sizes:
        has192 = True
    if "512x512" in sizes:
        has512 = True
    if "maskable" in purpose:
        maskable = True
    if src:
        print(f"icon={src}")
print(f"has192={'yes' if has192 else 'no'}")
print(f"has512={'yes' if has512 else 'no'}")
print(f"maskable={'yes' if maskable else 'no'}")
PYEOF

# A VAPID PUBLIC key must base64url-decode to a 65-byte uncompressed P-256 point whose
# first byte is 0x04. Anything else is a string that looks like a key and cannot be used
# to subscribe — the browser rejects it at applicationServerKey, long after this gate
# would have gone green.
cat > "$KEY_HELPER" <<'PYEOF'
import base64
import sys

raw = sys.stdin.read().strip()
if not raw:
    print("bad=empty")
    sys.exit(0)
padded = raw + "=" * (-len(raw) % 4)
try:
    blob = base64.urlsafe_b64decode(padded)
except Exception as exc:
    print(f"bad=not base64url ({exc})")
    sys.exit(0)
if len(blob) != 65:
    print(f"bad=decoded to {len(blob)} bytes, expected 65")
elif blob[0] != 0x04:
    print(f"bad=first byte 0x{blob[0]:02x}, expected 0x04 (uncompressed point)")
else:
    print("ok=65-byte uncompressed P-256 point")
PYEOF

# req METHOD URL [curl args...] -> echoes the HTTP status; body in $RESP, headers in $HDRS.
# Never -v (the Authorization header must not reach the log), never -L (a redirect is an
# answer, not something to chase).
req() {
  local method="$1" url="$2"; shift 2
  curl -sS --max-time 60 -o "$RESP" -D "$HDRS" -w '%{http_code}' \
    -X "$method" "$@" "$url" 2>/dev/null
}

# jget KEY — dotted-key lookup on $RESP.
jget() { PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" "$1" < "$RESP" 2>/dev/null; }

# hget NAME — the last value of a response header from $HDRS, lowercased.
hget() {
  tr -d '\r' < "$HDRS" | grep -i "^$1:" | tail -1 | sed 's/^[^:]*:[[:space:]]*//' \
    | tr '[:upper:]' '[:lower:]'
}

# strip_js FILE_IN FILE_OUT — line comments and block comments out.
#
# THE STRIP IS LOAD-BEARING, not tidiness. sw.js's own prose discusses `/v1/` and the
# Authorization header precisely because those are the rules it implements, so a grep
# over the raw body would be satisfied by the documentation of a guard that had been
# deleted. Over-stripping is the safe direction: it can only shrink what the checks see,
# never invent a match.
strip_js() {
  sed -e 's://.*::' -e '/\/\*/,/\*\//d' "$1" > "$2"
}

# strip_html FILE_IN FILE_OUT — the same, plus HTML comments.
strip_html() {
  sed -e '/<!--/,/-->/d' -e 's://.*::' -e '/\/\*/,/\*\//d' "$1" > "$2"
}

# bytes FILE -> byte count
bytes() { wc -c < "$1" | tr -d ' '; }

# count_lit FILE LITERAL -> occurrences of a fixed string
count_lit() { grep -oF "$2" "$1" 2>/dev/null | wc -l | tr -d ' '; }

# has_lit FILE LITERAL LABEL — ok/ko on a fixed-string presence check
has_lit() {
  if grep -qF "$2" "$1"; then
    ok "$3"
  else
    ko "$3 — not found in the SERVED body (comments stripped)"
  fi
}

if [ -z "$PY" ]; then
  ko "no python on PATH — this gate parses JSON with python, never jq. Install python3."
fi

# The origins must be REMOTE. A gate pointed at a local path would be the exact
# false-green this phase exists to close, so an origin without an http(s) scheme is a
# failure before any check runs.
for pair in "SITE:$SITE" "API:$API"; do
  name="${pair%%:*}"; value="${pair#*:}"
  case "$value" in
    https://*) ;;
    http://*)  yellow "  NOTE: $name is plain http ($value) — acceptable only for a staging origin" ;;
    *) ko "$name must be an http(s) origin, got '$value' — nothing may be proven from a local path" ;;
  esac
done

# ============================================================================
# (a) MANIFEST, DEPLOYED
# ============================================================================
test_a_manifest() {
  echo
  echo "--- (a) MANIFEST, served from the deployed origin ---"
  local url="$SITE/app/manifest.webmanifest"
  local status; status="$(req GET "$url")"
  if [ "$status" != "200" ]; then
    ko "(a) GET $url -> HTTP $status (expected 200). The PWA is not deployed at this origin."
    return 0
  fi
  ok "(a) GET $url -> 200"

  local ctype; ctype="$(hget content-type)"
  case "$ctype" in
    *manifest*|*json*) ok "(a) manifest content-type is '$ctype'" ;;
    *) ko "(a) manifest content-type is '$ctype' — expected it to contain 'manifest' or 'json'" ;;
  esac

  local verdict; verdict="$(PYTHONIOENCODING=utf-8 "$PY" "$MANIFEST_HELPER" < "$RESP" 2>/dev/null)"
  if echo "$verdict" | grep -q '^parse_error='; then
    ko "(a) the served manifest is not valid JSON: $(echo "$verdict" | sed 's/^parse_error=//')"
    return 0
  fi

  local start_url scope display theme has192 has512 maskable
  start_url="$(echo "$verdict" | sed -n 's/^start_url=//p')"
  scope="$(echo "$verdict" | sed -n 's/^scope=//p')"
  display="$(echo "$verdict" | sed -n 's/^display=//p')"
  theme="$(echo "$verdict" | sed -n 's/^theme_color=//p')"
  has192="$(echo "$verdict" | sed -n 's/^has192=//p')"
  has512="$(echo "$verdict" | sed -n 's/^has512=//p')"
  maskable="$(echo "$verdict" | sed -n 's/^maskable=//p')"

  [ "$start_url" = "/app/" ] && ok "(a) start_url is /app/" \
    || ko "(a) start_url is '$start_url', expected /app/ (D-27-01)"
  [ "$scope" = "/app/" ] && ok "(a) scope is /app/" \
    || ko "(a) scope is '$scope', expected /app/ (D-27-01)"
  [ -n "$display" ] && ok "(a) display is '$display'" || ko "(a) no display — the app would open in a browser tab, not as an installed app"
  [ -n "$theme" ] && ok "(a) theme_color is '$theme'" || ko "(a) no theme_color"
  [ "$has192" = "yes" ] && ok "(a) a 192x192 icon is declared" || ko "(a) no 192x192 icon declared"
  [ "$has512" = "yes" ] && ok "(a) a 512x512 icon is declared" || ko "(a) no 512x512 icon declared"
  [ "$maskable" = "yes" ] && ok "(a) a maskable icon is declared" \
    || ko "(a) no icon with purpose 'maskable' — Android would letterbox the app icon"

  # Every declared icon must actually resolve at the deployed origin. A manifest listing
  # an icon that 404s installs an app with a broken icon and no error anywhere.
  local src icon_url icon_status icon_ctype found=0
  while IFS= read -r src; do
    [ -z "$src" ] && continue
    found=$((found+1))
    case "$src" in
      /*) icon_url="$SITE$src" ;;
      *)  icon_url="$SITE/app/$src" ;;   # manifest-relative src
    esac
    icon_status="$(curl -sS --max-time 30 -o /dev/null -D "$HDRS" -w '%{http_code}' -I "$icon_url" 2>/dev/null)"
    icon_ctype="$(hget content-type)"
    if [ "$icon_status" = "200" ] && [ "${icon_ctype#image/png}" != "$icon_ctype" ]; then
      ok "(a) icon $src -> 200 image/png"
    else
      ko "(a) icon $src -> HTTP $icon_status content-type '$icon_ctype' (expected 200 + image/png) at $icon_url"
    fi
  done < <(echo "$verdict" | sed -n 's/^icon=//p')
  [ "$found" -gt 0 ] && ok "(a) $found icon(s) declared and fetched" || ko "(a) the manifest declares no icons at all"
}

# ============================================================================
# (b) SERVICE WORKER, DEPLOYED
# ============================================================================
test_b_service_worker() {
  echo
  echo "--- (b) SERVICE WORKER, served from the deployed origin ---"
  local url="$SITE/app/sw.js"
  local status; status="$(req GET "$url")"
  if [ "$status" != "200" ]; then
    ko "(b) GET $url -> HTTP $status (expected 200). Without a served worker there is no push and no shell."
    return 0
  fi
  ok "(b) GET $url -> 200"
  cp "$RESP" "$SW_RAW"

  local ctype cache
  ctype="$(hget content-type)"
  cache="$(hget cache-control)"
  case "$ctype" in
    *javascript*) ok "(b) sw.js content-type is '$ctype'" ;;
    *) ko "(b) sw.js content-type is '$ctype' — a worker not served as javascript will not register" ;;
  esac
  case "$cache" in
    *no-cache*) ok "(b) sw.js cache-control is '$cache'" ;;
    *) ko "(b) sw.js cache-control is '$cache' — expected no-cache, or a stale worker outlives its deploy" ;;
  esac

  strip_js "$SW_RAW" "$SW_CODE"

  # The stripper must be doing real work, or every assertion below is measuring prose.
  local raw_b code_b raw_v1 code_v1
  raw_b="$(bytes "$SW_RAW")"; code_b="$(bytes "$SW_CODE")"
  if [ "$raw_b" -gt 0 ] && [ "$((code_b * 100 / raw_b))" -lt 75 ]; then
    ok "(b) the comment stripper is not inert (${raw_b} -> ${code_b} bytes)"
  else
    ko "(b) stripping removed almost nothing (${raw_b} -> ${code_b} bytes) — the stripper is broken and every check below would be measuring comments"
  fi
  raw_v1="$(count_lit "$SW_RAW" '/v1/')"
  code_v1="$(count_lit "$SW_CODE" '/v1/')"
  if [ "$raw_v1" -gt "$code_v1" ]; then
    ok "(b) the removed comments really did mention /v1/ ($raw_v1 raw vs $code_v1 in code) — a raw grep here would have been self-validating"
  else
    ko "(b) /v1/ occurs $raw_v1 times raw and $code_v1 times stripped — the strip is not separating code from prose as intended"
  fi

  has_lit "$SW_CODE" 'addEventListener("push"' "(b) the worker handles the push event"
  has_lit "$SW_CODE" 'addEventListener("notificationclick"' "(b) the worker handles notificationclick"
  has_lit "$SW_CODE" 'pushsubscriptionchange' "(b) the worker handles pushsubscriptionchange (a silently rotated subscription is a device that stops receiving)"
  has_lit "$SW_CODE" 'req.method !== "GET"' "(b) guard 1: only GET is cacheable"
  has_lit "$SW_CODE" 'url.origin !== self.location.origin' "(b) guard 2: never a cross-origin response"
  has_lit "$SW_CODE" '/v1/' "(b) guard 3: never an API path"
  has_lit "$SW_CODE" 'headers.has("Authorization")' "(b) guard 4: never a credentialed request"

  # The guards must RETURN before anything is served from cache. One service worker is
  # registered per origin, not per account: a /v1/ response cached for one signed-in
  # person and replayed to the next is a real data leak, not a stale-UI annoyance.
  local v1_pos rw_pos
  v1_pos="$(grep -boF '/v1/' "$SW_CODE" 2>/dev/null | head -1 | cut -d: -f1)"
  rw_pos="$(grep -boF 'respondWith' "$SW_CODE" 2>/dev/null | head -1 | cut -d: -f1)"
  if [ -n "$v1_pos" ] && [ -n "$rw_pos" ] && [ "$v1_pos" -lt "$rw_pos" ]; then
    ok "(b) the /v1/ guard (byte $v1_pos) precedes the first respondWith (byte $rw_pos) — it returns before anything is served from cache"
  else
    ko "(b) the /v1/ guard does not precede the first respondWith (/v1/ at '${v1_pos:-none}', respondWith at '${rw_pos:-none}') — an API response could reach the cache"
  fi
  if grep -qF 'cache.put' "$SW_CODE"; then
    ko "(b) the served worker calls cache.put — there is no runtime caching in this design; anything written outside install can outlive a session"
  else
    ok "(b) the served worker never calls cache.put — nothing is written to the cache at runtime"
  fi
}

# ============================================================================
# (c) START URL, DEPLOYED
# ============================================================================
test_c_start_url() {
  echo
  echo "--- (c) START URL, served from the deployed origin ---"
  local url="$SITE/app/"
  local status; status="$(req GET "$url")"
  if [ "$status" != "200" ]; then
    ko "(c) GET $url -> HTTP $status (expected 200). The PWA start_url is not deployed."
    return 0
  fi
  ok "(c) GET $url -> 200"
  cp "$RESP" "$HTML_RAW"
  strip_html "$HTML_RAW" "$HTML_CODE"

  has_lit "$HTML_CODE" 'manifest.webmanifest' "(c) the served page links its manifest"
  has_lit "$HTML_CODE" 'serviceWorker.register' "(c) the served page registers the service worker"

  # D-27-05: the browser gives a site exactly one chance to ask. Asking on load, before
  # the person has any reason to say yes, gets a Block that no code can ever undo.
  local leaked=0
  for api in 'requestPermission' 'pushManager.subscribe'; do
    if grep -qF "$api" "$HTML_CODE"; then
      ko "(c) the served start_url document contains $api — the permission prompt must fire only from an explicit click (D-27-05)"
      leaked=1
    fi
  done
  [ "$leaked" -eq 0 ] && ok "(c) the served start_url document raises no permission prompt on load (D-27-05)"
}

# ============================================================================
# (d) CORS, FROM THE REAL ORIGIN
# ============================================================================
test_d_cors() {
  echo
  echo "--- (d) CORS preflight, positive and negative ---"

  curl -sS --max-time 30 -o /dev/null -D "$HDRS" -X OPTIONS "$API/v1/me" \
    -H "Origin: $SITE" \
    -H 'Access-Control-Request-Method: GET' \
    -H 'Access-Control-Request-Headers: authorization' >/dev/null 2>&1
  local allow creds
  allow="$(hget access-control-allow-origin)"
  creds="$(hget access-control-allow-credentials)"
  local expect; expect="$(echo "$SITE" | tr '[:upper:]' '[:lower:]')"
  if [ "$allow" = "$expect" ]; then
    ok "(d) the preflight from $SITE is answered with access-control-allow-origin: $allow"
  else
    ko "(d) the preflight from $SITE returned access-control-allow-origin '$allow', expected '$expect' — a browser at the app origin cannot call this API"
  fi
  if [ "$creds" = "true" ]; then
    ok "(d) access-control-allow-credentials: true"
  else
    ko "(d) access-control-allow-credentials is '$creds', expected true"
  fi

  # The negative control. A config that echoes any Origin back passes the positive test
  # and is wide open; without this line the check above proves almost nothing.
  curl -sS --max-time 30 -o /dev/null -D "$HDRS" -X OPTIONS "$API/v1/me" \
    -H 'Origin: https://attacker.example' \
    -H 'Access-Control-Request-Method: GET' \
    -H 'Access-Control-Request-Headers: authorization' >/dev/null 2>&1
  local bad; bad="$(hget access-control-allow-origin)"
  case "$bad" in
    *attacker.example*|'*')
      ko "(d) NEGATIVE CONTROL FAILED: the API echoed access-control-allow-origin '$bad' for a foreign origin — CORS is not restricting anything" ;;
    *)
      ok "(d) negative control: a foreign origin is NOT echoed (got '${bad:-<none>}')" ;;
  esac
}

# ============================================================================
# (e) SIGN-IN -> CHAT AGAINST THE REAL API
# ============================================================================
XBT="${VERIFY_XBT_TOKEN:-}"
TEAM="${VERIFY_TEAM_ID:-}"

test_e_api_walk() {
  echo
  echo "--- (e) the API walk the PWA performs, with the app Origin on every request ---"
  if [ -z "$XBT" ]; then
    ko "(e) VERIFY_XBT_TOKEN is not set — export VERIFY_XBT_TOKEN=<a real xbt_ token for a member of VERIFY_TEAM_ID> and re-run. A credential we do not have is a FAILURE, not a skip."
    return 0
  fi
  if [ -z "$TEAM" ]; then
    ko "(e) VERIFY_TEAM_ID is not set — export VERIFY_TEAM_ID=<the team UUID that token belongs to> and re-run."
    return 0
  fi

  local auth=(-H "Authorization: Bearer $XBT" -H "Origin: $SITE")
  local status

  status="$(req GET "$API/v1/me" "${auth[@]}")"
  if [ "$status" = "200" ] && [ -n "$(jget source_user_id)" ]; then
    ok "(e) GET /v1/me -> 200 with a source_user_id"
  else
    ko "(e) GET /v1/me -> HTTP $status (expected 200 carrying source_user_id) — the token is rejected or the API is down"
    return 0
  fi

  status="$(req GET "$API/v1/teams/my-teams" "${auth[@]}")"
  if [ "$status" = "200" ] && [ -n "$(jget 0.id)" ]; then
    ok "(e) GET /v1/teams/my-teams -> 200, non-empty"
  else
    ko "(e) GET /v1/teams/my-teams -> HTTP $status with no team — the PWA would open on an empty team picker"
  fi

  status="$(req POST "$API/v1/me/centrifugo-token" "${auth[@]}")"
  local ws_url token_len
  ws_url="$(jget ws_url)"
  token_len="$(jget token | wc -c | tr -d ' ')"
  if [ "$status" = "200" ] && [ "${ws_url#wss://}" != "$ws_url" ]; then
    ok "(e) POST /v1/me/centrifugo-token -> 200 with ws_url $ws_url"
  else
    ko "(e) POST /v1/me/centrifugo-token -> HTTP $status, ws_url '$ws_url' (expected 200 and a wss:// URL)"
  fi
  if [ "$token_len" -gt 1 ]; then
    ok "(e) the response carried a signed client token (length not shown — it is a credential)"
  else
    ko "(e) the centrifugo-token response carried no token"
  fi

  status="$(req GET "$API/v1/teams/$TEAM/messages?limit=5" "${auth[@]}")"
  if [ "$status" = "200" ]; then
    ok "(e) GET /v1/teams/\$VERIFY_TEAM_ID/messages?limit=5 -> 200"
  else
    ko "(e) GET /v1/teams/\$VERIFY_TEAM_ID/messages?limit=5 -> HTTP $status (expected 200)"
  fi

  local nonce; nonce="verify-phase27-$(date +%s)-$$"
  status="$(req POST "$API/v1/teams/$TEAM/messages" "${auth[@]}" \
    -H 'Content-Type: application/json' \
    --data "{\"content\":\"$nonce\"}")"
  if [ "$status" = "201" ]; then
    ok "(e) POST /v1/teams/\$VERIFY_TEAM_ID/messages -> 201 ($nonce)"
  else
    ko "(e) POST /v1/teams/\$VERIFY_TEAM_ID/messages -> HTTP $status (expected 201)"
  fi
}

# ============================================================================
# (f) TWO-CLIENT REALTIME
# ============================================================================
test_f_realtime() {
  echo
  echo "--- (f) two-client realtime: one posts over HTTP, the OTHER receives ---"
  if ! command -v node >/dev/null 2>&1; then
    ko "(f) node is not on PATH — the realtime probe needs node 22+ (it uses the built-in WebSocket). Install node and re-run."
    return 0
  fi
  if [ -z "$XBT" ] || [ -z "$TEAM" ]; then
    ko "(f) VERIFY_XBT_TOKEN and/or VERIFY_TEAM_ID are not set — the arrival proof cannot run, which is a FAILURE, not a skip. Export both and re-run."
    return 0
  fi
  if [ ! -f "$REALTIME_PROBE" ]; then
    ko "(f) the realtime probe is missing from the checkout at $REALTIME_PROBE — the arrival proof is the gate's only evidence for SC#3 and cannot be inferred from anything else."
    return 0
  fi

  API_BASE="$API" \
  VERIFY_XBT_TOKEN="$XBT" \
  VERIFY_TEAM_ID="$TEAM" \
  VERIFY_XBT_TOKEN_2="${VERIFY_XBT_TOKEN_2:-}" \
    node "$REALTIME_PROBE" > "$PROBE_LOG" 2>&1
  local rc=$?
  sed 's/^/      /' "$PROBE_LOG"
  if [ "$rc" -eq 0 ]; then
    ok "(f) a message posted over HTTP by one client ARRIVED, by content, at a different websocket client"
  else
    ko "(f) the realtime arrival proof exited $rc — realtime is not delivering to a second client"
  fi
}

# ============================================================================
# (g) PUSH SEND + PRUNE
# ============================================================================
# api_container -> echoes the memory-api container id, or empty.
api_container() {
  # NO MSYS_NO_PATHCONV: the DC array carries a `-f` (and maybe `--env-file`) HOST path.
  "${DC[@]}" ps -q memory-api 2>/dev/null | head -1
}

# api_blocker -> empty when the container can be exec'd into, otherwise the reason AND
# the exact thing to start. Three genuinely different situations that a single "not
# running" message would flatten into one unhelpful line: no docker CLI at all, a CLI
# whose daemon is unreachable, and a live daemon with the service down.
api_blocker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not on PATH — this check runs inside the memory-api container, so it must be run on the deployment host"
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "the docker daemon is not reachable — start Docker and re-run"
    return 0
  fi
  if [ -z "$(api_container)" ]; then
    echo "the memory-api container is not running — bring it up ('docker compose -f infrastructure/docker-compose.yml --env-file .env up -d memory-api') and re-run"
    return 0
  fi
  echo ""
}

test_g_push() {
  echo
  echo "--- (g) real encrypted push + the 404/410/500 prune matrix, inside the API container ---"
  local blocker; blocker="$(api_blocker)"
  if [ -n "$blocker" ]; then
    ko "(g) $blocker. A push path we cannot exercise is a FAILURE, not a skip."
    return 0
  fi
  if [ ! -f "$PUSH_PROBE" ]; then
    ko "(g) the push probe is missing from the checkout at $PUSH_PROBE — nothing else in this gate exercises real encryption or the prune matrix."
    return 0
  fi

  # NO MSYS_NO_PATHCONV: the DC array carries HOST paths. The probe arrives on stdin, so
  # no in-container path is passed on the command line at all.
  "${DC[@]}" exec -T memory-api python - < "$PUSH_PROBE" > "$PROBE_LOG" 2>&1
  local rc=$?
  sed 's/^/      /' "$PROBE_LOG"
  if [ "$rc" -eq 0 ]; then
    ok "(g) a real aes128gcm-encrypted, VAPID-signed request reached a real socket, and 410/404 pruned the row while 500 did not"
  else
    ko "(g) the push probe exited $rc — the send path or the prune matrix is wrong (see the probe output above)"
  fi
}

# ============================================================================
# (h) PUSH CONFIG, DEPLOYED
# ============================================================================
test_h_push_config() {
  echo
  echo "--- (h) the deployed push config endpoint ---"
  if [ -z "$XBT" ]; then
    ko "(h) VERIFY_XBT_TOKEN is not set — /v1/push/config is authenticated. Export VERIFY_XBT_TOKEN and re-run."
    return 0
  fi

  local status; status="$(req GET "$API/v1/push/config" -H "Authorization: Bearer $XBT" -H "Origin: $SITE")"
  if [ "$status" != "200" ]; then
    ko "(h) GET /v1/push/config -> HTTP $status (expected 200)"
    return 0
  fi
  ok "(h) GET /v1/push/config -> 200"
  cp "$RESP" "$CFG_BODY"

  local enabled pub
  enabled="$(jget enabled)"
  pub="$(jget vapid_public_key)"
  if [ "$enabled" = "True" ] || [ "$enabled" = "true" ]; then
    ok "(h) push reports enabled: true"
  else
    ko "(h) push reports enabled: '$enabled' — this deployment cannot deliver a notification. Set PUSH_ENABLED, VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and restart memory-api."
  fi
  local verdict; verdict="$(printf '%s' "$pub" | PYTHONIOENCODING=utf-8 "$PY" "$KEY_HELPER" 2>/dev/null)"
  case "$verdict" in
    ok=*) ok "(h) vapid_public_key is a ${verdict#ok=}" ;;
    *)    ko "(h) vapid_public_key is invalid: ${verdict#bad=} — a browser would refuse it at subscribe time" ;;
  esac

  # The private half must not be reachable from a client. Read it ONLY to assert its
  # ABSENCE from the response, never print it, and drop it from the environment
  # immediately afterwards (T-27-08-02).
  local blocker; blocker="$(api_blocker)"
  if [ -n "$blocker" ]; then
    ko "(h) cannot read the container's VAPID_PRIVATE_KEY to prove it is absent from the response: $blocker"
    return 0
  fi
  local secret; secret="$("${DC[@]}" exec -T memory-api printenv VAPID_PRIVATE_KEY 2>/dev/null | tr -d '\r\n')"
  if [ -z "$secret" ]; then
    ko "(h) the memory-api container has no VAPID_PRIVATE_KEY — push cannot be signed, so nothing can ever be delivered"
  elif grep -qF -- "$secret" "$CFG_BODY"; then
    ko "(h) THE PRIVATE VAPID KEY APPEARS IN THE /v1/push/config RESPONSE — rotate the keypair immediately"
  else
    ok "(h) the private VAPID key does not appear anywhere in the /v1/push/config response"
  fi
  secret=""
  unset secret
}

# ============================================================================
# (i) STATIC SUITES
# ============================================================================
test_i_static_suites() {
  echo
  echo "--- (i) the suites that must still be green at the end of the phase ---"
  if ! command -v node >/dev/null 2>&1; then
    ko "(i) node is not on PATH — the extension suite and the chat-core drift check both need it."
    return 0
  fi

  if node chrome-extension/tests/run_tests.mjs > "$PROBE_LOG" 2>&1; then
    ok "(i) the extension test suite is green ($(grep -c '' "$PROBE_LOG") lines of output)"
  else
    ko "(i) the extension test suite FAILED — D-27-04 shares the chat core between both surfaces, so a red extension is a red PWA"
    tail -25 "$PROBE_LOG" | sed 's/^/      /'
  fi

  if node scripts/sync-chat-core.mjs --check > "$PROBE_LOG" 2>&1; then
    ok "(i) the shared chat-core copies are byte-identical across both surfaces"
  else
    ko "(i) the chat-core copies have DRIFTED — run 'node scripts/sync-chat-core.mjs' and commit the result"
    tail -25 "$PROBE_LOG" | sed 's/^/      /'
  fi
}

# ============================================================================
# (j) SERVER UNIT SUITE, IN THE CONTAINER
# ============================================================================
SUITE=(
  tests/test_push_endpoints.py
  tests/test_push_endpoint_safety.py
  tests/test_web_push_send.py
  tests/test_user_mention_detector.py
)

# suite_runs_in_container -> 0 when the API container can actually run these files.
#
# It cannot today, and that is a property of the image rather than an accident: the
# Dockerfile's `runtime` stage COPYs `app/` and `alembic/` but NOT `tests/`, and it
# installs the project's runtime dependencies only — `pytest` lives in the `dev` extra.
# So this probe is not ceremony. On today's image it is correctly false and the suite
# runs against the checkout instead; on any future test-capable image it becomes true
# and the suite moves back inside the container with no edit here.
#
# Running it against the checkout is not a weakening, because the question "does this
# deployment's OWN dependency set actually work" is answered by check (g) — which drives
# the real pywebpush inside the real container against a real socket, and cannot be
# satisfied by a checkout at all. What (j) adds is the unit-level regression guard over
# source that is identical in both places.
suite_runs_in_container() {
  [ -n "$(api_blocker)" ] && return 1
  # NO MSYS_NO_PATHCONV: the DC array carries HOST paths; the `sh -c` payload carries no
  # absolute path at all (the redirect below is the HOST shell's, not the container's),
  # so there is nothing for MSYS to mangle either way.
  "${DC[@]}" exec -T memory-api sh -c \
    'python -c "import pytest" && test -d tests' >/dev/null 2>&1
}

test_j_server_suite() {
  echo
  echo "--- (j) the push + mention unit suites ---"
  local rc=1 where=""

  if suite_runs_in_container; then
    where="the API container"
    "${DC[@]}" exec -T memory-api python -m pytest "${SUITE[@]}" -q > "$PROBE_LOG" 2>&1
    rc=$?
  elif [ -n "$PY" ] && "$PY" -c "import pytest" >/dev/null 2>&1; then
    where="apps/memory-api on this host (the runtime image ships no tests/ and no pytest)"
    ( cd apps/memory-api && "$PY" -m pytest "${SUITE[@]}" -q ) > "$PROBE_LOG" 2>&1
    rc=$?
  else
    ko "(j) no runner can execute the server suite. Either install the dev extras here ('pip install -e \"apps/memory-api[dev]\"') or build a test-capable memory-api image that ships tests/ plus pytest. A suite we cannot run is a FAILURE, not a skip."
    return 0
  fi

  if [ "$rc" -eq 0 ]; then
    ok "(j) the push + mention suites pass ($where)"
    tail -3 "$PROBE_LOG" | sed 's/^/      /'
  else
    ko "(j) the push + mention suites FAILED, exit $rc ($where)"
    tail -25 "$PROBE_LOG" | sed 's/^/      /'
  fi
}

# ============================================================================
# (k) MIGRATION HEAD
# ============================================================================
test_k_migration() {
  echo
  echo "--- (k) the push_subscriptions migration is applied where the API runs ---"
  local blocker; blocker="$(api_blocker)"
  if [ -n "$blocker" ]; then
    ko "(k) $blocker. The applied revision must be read from the running API's own database, not from the migration files on disk."
    return 0
  fi

  # NO MSYS_NO_PATHCONV: the DC array carries HOST paths.
  #
  # `python -m alembic`, not `alembic`: the runtime image installs the alembic PACKAGE
  # (the container runs its migrations at boot) but not the console script, so the bare
  # `alembic` binary is not on PATH and the exec fails with "executable file not found".
  # That is the same root cause as check (j)'s in-container pytest problem — a runtime
  # image is not a dev image. The module form reads the identical alembic_version row
  # through the API container's own configured connection, so the check still answers
  # "what revision is applied where the API actually runs" and not "what files exist on
  # disk". Verified against production 2026-08-01: reports `0029_push_subscriptions (head)`.
  local out; out="$("${DC[@]}" exec -T memory-api python -m alembic current 2>&1 | tr -d '\r')"
  local rev; rev="$(echo "$out" | grep -oE '[0-9]{4}_[a-z0-9_]+' | tail -1)"
  local num="${rev%%_*}"
  if [ -n "$num" ] && [ "$((10#$num))" -ge 29 ] 2>/dev/null; then
    ok "(k) alembic reports '$rev' — 0029_push_subscriptions (or later) is applied"
  else
    ko "(k) alembic reports '${rev:-<nothing parseable>}' — 0029_push_subscriptions is NOT applied, so every subscription write fails against the live database. Output: $(echo "$out" | tail -3 | tr '\n' ' ')"
  fi
}

# --- Orchestration ---------------------------------------------------------------------
# Every check runs, and every check that cannot run records `ko`. There is no branch in
# this gate that produces a skip.
test_a_manifest
test_b_service_worker
test_c_start_url
test_d_cors
test_e_api_walk
test_f_realtime
test_g_push
test_h_push_config
test_i_static_suites
test_j_server_suite
test_k_migration

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"
echo "FAIL: $FAIL"

# exit 0 only when FAIL == 0 AND SKIP == 0 — a check that could not run is a failure.
if [ $FAIL -eq 0 ] && [ $SKIP -eq 0 ]; then
  exit 0
else
  exit 1
fi

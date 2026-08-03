// Tests for ws_keepalive.js — pure backoff/ping math, no Chrome APIs.
//
// The last section reads background.js as text. background.js cannot be
// imported here (it touches chrome.* at module scope), and the decision it
// carries out is the one that went wrong in production, so the wiring is
// checked structurally rather than left unverified.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  PING_INTERVAL_MS,
  MAX_BACKOFF_MS,
  MIN_BACKOFF_MS,
  MAX_ATTEMPT,
  computeBackoffMs,
  socketHealth,
  watchdogPlan,
  CONNECT_STALL_MS,
  SILENCE_STALL_MS,
  SESSION_FRESH_WINDOW_MS,
  WATCHDOG_PERIOD_MIN,
  WS_CONNECTING,
  WS_OPEN,
  WS_CLOSING,
  WS_CLOSED,
} from "../ws_keepalive.js";

let pass = 0;
let fail = 0;
function assert(cond, label) {
  if (cond) {
    pass++;
    console.log(`PASS: ${label}`);
  } else {
    fail++;
    console.error(`FAIL: ${label}`);
  }
}

// 1. attempt=1, rand=0.5 → jitter factor = 0.5*0.4 - 0.2 = 0 → exactly 2000ms
{
  const d = computeBackoffMs(1, () => 0.5);
  assert(d === 2000, `attempt=1,rand=0.5 → 2000ms (got ${d})`);
}

// 2. attempt=1, rand=0 → jitter -20% → 1600ms, still ≥ MIN
{
  const d = computeBackoffMs(1, () => 0);
  assert(
    d >= MIN_BACKOFF_MS && d <= 2000,
    `attempt=1,rand=0 → [${MIN_BACKOFF_MS},2000], got ${d}`,
  );
}

// 3. attempt=99 → capped at MAX_BACKOFF_MS
{
  const d = computeBackoffMs(99, () => 0.5);
  assert(d === MAX_BACKOFF_MS, `attempt=99 capped at ${MAX_BACKOFF_MS} (got ${d})`);
}

// 4. attempt=MAX_ATTEMPT (boundary) → base = min(2^6*1000=64000, 30000) = 30000
{
  const d = computeBackoffMs(MAX_ATTEMPT, () => 0.5);
  assert(d === MAX_BACKOFF_MS, `attempt=MAX_ATTEMPT → ${MAX_BACKOFF_MS} (got ${d})`);
}

// 5. PING < 30s MV3 idle invariant
{
  assert(
    PING_INTERVAL_MS < 30_000,
    `PING_INTERVAL_MS < 30000 (got ${PING_INTERVAL_MS})`,
  );
}

// Bonus: clamping at the high end with rand=1 (jitter +20% → 36000) should cap at MAX
{
  const d = computeBackoffMs(5, () => 1);
  assert(
    d <= MAX_BACKOFF_MS,
    `attempt=5,rand=1 → ≤ ${MAX_BACKOFF_MS}, got ${d}`,
  );
}

// =========================================================================
// Watchdog health model
// =========================================================================
//
// The bug being pinned: the watchdog fired once a minute for thirty minutes
// while the bridge sat dead, because it only ever asked "is the socket CLOSED".
// Every case below is one the old condition answered "nothing to do" to.

const NOW = 1_000_000;

// --- The two states the old check could not see --------------------------

// 7. A handshake stuck in CONNECTING past its grace window is not CLOSED, so
//    the old watchdog left it alone forever.
{
  const snap = {
    readyState: WS_CONNECTING,
    stateSince: NOW - (CONNECT_STALL_MS + 1000),
    lastInboundAt: null,
    now: NOW,
  };
  assert(socketHealth(snap) === "stalled", "long CONNECTING → stalled");
  const plan = watchdogPlan(snap);
  assert(plan.reopen === true, "stalled CONNECTING is reopened");
  assert(
    plan.closeFirst === true,
    "stalled CONNECTING is closed first — otherwise openBridgeWS sees a live-looking socket and returns",
  );
  assert(
    plan.refreshSession === false,
    "a stalled socket claims nothing about the bridge being alive",
  );
}

// 8. A zombie OPEN — readyState 1, peer long gone. The worst case: it looks
//    perfect locally and is dead at the other end.
{
  const snap = {
    readyState: WS_OPEN,
    stateSince: NOW - 10 * 60_000,
    lastInboundAt: NOW - (SILENCE_STALL_MS + 5_000),
    now: NOW,
  };
  assert(socketHealth(snap) === "zombie", "OPEN + long silence → zombie");
  const plan = watchdogPlan(snap);
  assert(plan.reopen === true, "zombie OPEN is reopened");
  assert(plan.closeFirst === true, "zombie OPEN is discarded before reopening");
  assert(plan.refreshSession === false, "a zombie never heartbeats");
}

// --- The states it could see, still handled ------------------------------

// 9. No socket at all.
{
  const snap = { readyState: null, stateSince: 0, lastInboundAt: null, now: NOW };
  assert(socketHealth(snap) === "absent", "no socket → absent");
  const plan = watchdogPlan(snap);
  assert(plan.reopen === true, "absent socket is opened");
  assert(plan.closeFirst === false, "there is nothing to close");
}

// 10. CLOSED and CLOSING both mean gone.
{
  for (const [state, label] of [
    [WS_CLOSED, "CLOSED"],
    [WS_CLOSING, "CLOSING"],
  ]) {
    const snap = {
      readyState: state,
      stateSince: NOW - 1000,
      lastInboundAt: null,
      now: NOW,
    };
    assert(socketHealth(snap) === "dead", `${label} → dead`);
    assert(watchdogPlan(snap).reopen === true, `${label} is reopened`);
  }
}

// --- What must NOT be disturbed ------------------------------------------

// 11. A young CONNECTING is left alone: tearing down every handshake that has
//     not completed within a minute would make the reconnect loop the outage.
{
  const snap = {
    readyState: WS_CONNECTING,
    stateSince: NOW - 2_000,
    lastInboundAt: null,
    now: NOW,
  };
  assert(socketHealth(snap) === "connecting", "young CONNECTING → connecting");
  const plan = watchdogPlan(snap);
  assert(plan.reopen === false, "a young handshake is given time");
  assert(
    plan.refreshSession === false,
    "an unopened handshake is not evidence of a live bridge",
  );
}

// 12. A healthy socket is left connected AND heartbeats. This is the half that
//     turned a 1-minute alarm into a heartbeat: without it the alarm could fire
//     forever while the server's record went stale underneath a working socket.
{
  const snap = {
    readyState: WS_OPEN,
    stateSince: NOW - 10 * 60_000,
    lastInboundAt: NOW - 5_000,
    now: NOW,
  };
  assert(socketHealth(snap) === "healthy", "OPEN + recent traffic → healthy");
  const plan = watchdogPlan(snap);
  assert(plan.reopen === false, "a healthy socket is not churned");
  assert(
    plan.refreshSession === true,
    "every watchdog fire over a healthy socket refreshes the session",
  );
}

// 13. A socket that just opened has no inbound frame yet. Its open time stands
//     in, so it is healthy — and a socket that opened and instantly died still
//     ages into a zombie rather than waiting for traffic that never comes.
{
  const justOpened = {
    readyState: WS_OPEN,
    stateSince: NOW - 1_000,
    lastInboundAt: null,
    now: NOW,
  };
  assert(socketHealth(justOpened) === "healthy", "freshly OPEN → healthy");

  const openedAndDied = {
    readyState: WS_OPEN,
    stateSince: NOW - (SILENCE_STALL_MS + 1_000),
    lastInboundAt: null,
    now: NOW,
  };
  assert(
    socketHealth(openedAndDied) === "zombie",
    "OPEN long ago with nothing ever received → zombie",
  );
}

// 14. refreshSession is true for exactly ONE health value. If a second one ever
//     starts heartbeating, the server's record stops meaning "a healthy socket
//     was observed" and starts meaning "the service worker was awake".
{
  const healths = [
    ["absent", { readyState: null, stateSince: 0, lastInboundAt: null, now: NOW }],
    [
      "connecting",
      { readyState: WS_CONNECTING, stateSince: NOW - 1000, lastInboundAt: null, now: NOW },
    ],
    [
      "stalled",
      {
        readyState: WS_CONNECTING,
        stateSince: NOW - CONNECT_STALL_MS - 1,
        lastInboundAt: null,
        now: NOW,
      },
    ],
    [
      "zombie",
      {
        readyState: WS_OPEN,
        stateSince: NOW - 10 * 60_000,
        lastInboundAt: NOW - SILENCE_STALL_MS - 1,
        now: NOW,
      },
    ],
    ["dead", { readyState: WS_CLOSED, stateSince: NOW - 1000, lastInboundAt: null, now: NOW }],
    [
      "healthy",
      { readyState: WS_OPEN, stateSince: NOW - 60_000, lastInboundAt: NOW - 1_000, now: NOW },
    ],
  ];
  const refreshing = healths
    .filter(([, snap]) => watchdogPlan(snap).refreshSession)
    .map(([name]) => name);
  assert(
    refreshing.length === 1 && refreshing[0] === "healthy",
    `only "healthy" heartbeats (got ${JSON.stringify(refreshing)})`,
  );
}

// --- Timing invariants ----------------------------------------------------

// 15. The alarm must fire more often than the server's freshness window, or the
//     heartbeat is not a heartbeat.
{
  assert(
    WATCHDOG_PERIOD_MIN * 60_000 < SESSION_FRESH_WINDOW_MS,
    `watchdog period (${WATCHDOG_PERIOD_MIN}min) must be under the ${SESSION_FRESH_WINDOW_MS}ms freshness window`,
  );
}

// 16. Silence must tolerate more than one dropped ping. Tearing down a working
//     socket on a single missed round-trip would be its own outage.
{
  assert(
    SILENCE_STALL_MS > 3 * PING_INTERVAL_MS,
    `SILENCE_STALL_MS (${SILENCE_STALL_MS}) must exceed three ping intervals`,
  );
}

// 17. A tick over a healthy socket never reconnects — the fix must not become a
//     reconnect storm on every alarm.
{
  const plan = watchdogPlan({
    readyState: WS_OPEN,
    stateSince: NOW - 3600_000,
    lastInboundAt: NOW - PING_INTERVAL_MS,
    now: NOW,
  });
  assert(
    plan.reopen === false && plan.closeFirst === false,
    "an hour-old socket still receiving pongs is left alone",
  );
}

// =========================================================================
// The watchdog wiring in background.js
// =========================================================================

const __dirname = dirname(fileURLToPath(import.meta.url));
const backgroundJs = readFileSync(join(__dirname, "..", "background.js"), "utf8");

// 18. The alarm listener must delegate to the health model. The regression
//     being pinned is literal: `if (!ws || ws.readyState === WebSocket.CLOSED)`
//     is the condition that let a bridge rot for thirty minutes.
{
  assert(
    backgroundJs.includes("watchdogPlan"),
    "background.js must decide from watchdogPlan, not from an inline readyState test",
  );
  assert(
    !/alarm\.name !== "xbrain_ws_watchdog"[\s\S]{0,200}readyState === WebSocket\.CLOSED/.test(
      backgroundJs,
    ),
    "the watchdog must not be back to acting only on a CLOSED socket",
  );
}

// 19. A healthy tick has to actually write the heartbeat, and it has to be the
//     upsert route — that endpoint is the only writer of last_seen_at.
{
  assert(
    backgroundJs.includes("refreshExternalSession"),
    "background.js must define the session heartbeat",
  );
  assert(
    backgroundJs.includes("/v1/me/external-sessions"),
    "the heartbeat must POST the external-sessions upsert — nothing else refreshes last_seen_at",
  );
  assert(
    /plan\.refreshSession[\s\S]{0,120}refreshExternalSession\(\)/.test(backgroundJs),
    "the heartbeat must be gated on the plan's refreshSession, not fired unconditionally",
  );
}

// 20. Inbound frames must be timestamped, or the zombie check has no input.
{
  assert(
    /ws\.onmessage[\s\S]{0,300}lastInboundAt = Date\.now\(\)/.test(backgroundJs),
    "every inbound frame must record lastInboundAt — the zombie check reads nothing else",
  );
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

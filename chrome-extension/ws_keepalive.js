// === xbrain WS keepalive + reconnect backoff ===
//
// Pure, side-effect-free module — consumed by background.js but kept testable
// without Chrome APIs (run_tests.mjs picks up tests/test_ws_keepalive.mjs).
//
// MV3 service workers are killed after ~30s of idle. PING_INTERVAL_MS < 30s
// keeps the SW alive; the chrome.alarms watchdog (in background.js) re-opens
// the WS if the SW was killed anyway (e.g. browser restart).

export const PING_INTERVAL_MS = 20_000; // < 30s MV3 SW idle timeout
export const WATCHDOG_PERIOD_MIN = 1; // chrome.alarms minimum stable: 1 min
export const MAX_BACKOFF_MS = 30_000;
export const MIN_BACKOFF_MS = 500;
export const MAX_ATTEMPT = 6;

// ---------------------------------------------------------------------------
// Watchdog health model
// ---------------------------------------------------------------------------
//
// WHY A SOCKET OBJECT IS NOT EVIDENCE OF A BRIDGE.
//
// The watchdog used to act on one condition — `!ws || readyState === CLOSED` —
// and a bridge went thirty minutes stale underneath it while the alarm fired
// thirty times. Two states slip through that test and both are common:
//
//   CONNECTING that never opens. A handshake to an unreachable host sits at
//   readyState 0 indefinitely. It is not CLOSED, so the old test found nothing
//   wrong, every minute, forever.
//
//   OPEN whose peer is gone. A laptop that slept, a NAT that dropped the
//   mapping, a proxy that closed one direction: the tab keeps a socket at
//   readyState 1 that will never deliver another byte. This is the worse one —
//   it looks perfect from here and is dead at the other end.
//
// Neither is detectable from `readyState` alone, so the model below adds the
// only two facts that separate them: when the current state was entered, and
// when something last arrived FROM the server. The second is why the bridge
// answers `ping` with `pong` — an idle socket is otherwise silent for hours and
// silence would be indistinguishable from death.

/** A handshake that has not opened in this long is not going to. */
export const CONNECT_STALL_MS = 30_000;

/**
 * An OPEN socket that has delivered nothing for this long is presumed dead.
 *
 * Three ping round-trips (60s) plus a 15s grace. Long enough that one dropped
 * pong on a slow network does not tear down a working connection; short enough
 * that the replacement is registered well inside the server's freshness window.
 */
export const SILENCE_STALL_MS = 75_000;

/**
 * How long the server treats a session row as evidence of a live bridge.
 *
 * Mirrored here as a bound, not as behaviour: it is what makes the once-a-minute
 * alarm a sufficient heartbeat. Widening it on the server to cover a missed
 * heartbeat would only make a dead bridge look alive for longer.
 */
export const SESSION_FRESH_WINDOW_MS = 90_000;

/** WebSocket.readyState, named — this module never sees the DOM constructor. */
export const WS_CONNECTING = 0;
export const WS_OPEN = 1;
export const WS_CLOSING = 2;
export const WS_CLOSED = 3;

/**
 * Classify the bridge socket from a snapshot the caller took.
 *
 * Pure: the caller reads the clock and the socket, this decides what they mean.
 *
 * @param {object} s
 * @param {number|null} s.readyState  null/undefined when there is no socket
 * @param {number} s.stateSince       ms timestamp the current state was entered
 * @param {number|null} s.lastInboundAt  ms timestamp of the last frame FROM the
 *   server; null when nothing has arrived yet on this socket
 * @param {number} s.now
 * @returns {"absent"|"connecting"|"stalled"|"healthy"|"zombie"|"dead"}
 */
export function socketHealth({ readyState, stateSince, lastInboundAt, now }) {
  if (readyState === null || readyState === undefined || readyState < 0) {
    return "absent";
  }
  if (readyState === WS_CLOSED || readyState === WS_CLOSING) return "dead";
  if (readyState === WS_CONNECTING) {
    const waiting = now - (stateSince || 0);
    return waiting > CONNECT_STALL_MS ? "stalled" : "connecting";
  }
  // OPEN. Nothing has arrived yet on a socket that just opened, so the open
  // time stands in until the first frame does — otherwise a socket that opens
  // and immediately dies would look healthy until something arrived, which is
  // exactly what never happens.
  const lastSign = lastInboundAt || stateSince || 0;
  return now - lastSign > SILENCE_STALL_MS ? "zombie" : "healthy";
}

/**
 * What the watchdog should do about that.
 *
 * `refreshSession` is deliberately true for ONE health value. The server's
 * record of "this user has a live bridge" is a timestamp somebody has to write,
 * and writing it on every alarm regardless of the socket would turn the record
 * into a statement about the service worker being awake — which is not the
 * question anyone is asking. A refresh means: a healthy socket was observed
 * just now. A stalled or zombie one gets replaced instead, and the register
 * handshake on the new socket writes the timestamp for real.
 *
 * @param {object} snapshot same shape as socketHealth's argument
 * @returns {{health: string, reopen: boolean, closeFirst: boolean, refreshSession: boolean}}
 */
export function watchdogPlan(snapshot) {
  const health = socketHealth(snapshot);
  switch (health) {
    case "healthy":
      return { health, reopen: false, closeFirst: false, refreshSession: true };
    case "connecting":
      // Still inside its grace window. Not proven live, so nothing is claimed.
      return { health, reopen: false, closeFirst: false, refreshSession: false };
    case "stalled":
    case "zombie":
      // The socket object still exists and still fails every "is it closed"
      // test, so it must be closed HERE or openBridgeWS's idempotence guard
      // would look at it, decide a connection is already in hand, and return.
      return { health, reopen: true, closeFirst: true, refreshSession: false };
    default: // "absent" | "dead"
      return { health, reopen: true, closeFirst: false, refreshSession: false };
  }
}

/**
 * Exponential backoff with ±20% jitter.
 *   attempt = 1 → ~2s   (2^1 * 1000 = 2000 base)
 *   attempt = 2 → ~4s
 *   attempt = 3 → ~8s
 *   attempt = 4 → ~16s
 *   attempt = 5 → ~30s  (capped)
 *   attempt = 6 → ~30s  (capped)
 *
 * Result is always clamped to [MIN_BACKOFF_MS, MAX_BACKOFF_MS].
 *
 * @param {number} attempt   1-based attempt index (caller increments BEFORE call)
 * @param {() => number} rand  defaults to Math.random; tests inject deterministic
 * @returns {number} ms delay
 */
export function computeBackoffMs(attempt, rand = Math.random) {
  const clamped = Math.min(Math.max(attempt | 0, 1), MAX_ATTEMPT);
  const base = Math.min(2 ** clamped * 1000, MAX_BACKOFF_MS);
  const jitterFactor = rand() * 0.4 - 0.2; // ∈ [-0.2, +0.2)
  const delay = base + base * jitterFactor;
  return Math.max(MIN_BACKOFF_MS, Math.min(delay, MAX_BACKOFF_MS));
}

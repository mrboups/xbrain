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

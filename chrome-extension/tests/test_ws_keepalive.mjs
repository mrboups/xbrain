// Tests for ws_keepalive.js — pure backoff/ping math, no Chrome APIs.

import {
  PING_INTERVAL_MS,
  MAX_BACKOFF_MS,
  MIN_BACKOFF_MS,
  MAX_ATTEMPT,
  computeBackoffMs,
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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

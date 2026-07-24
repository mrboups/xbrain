// Walks every test_*.mjs file in this directory, runs it under node's built-in test
// runner in a fresh process, and exits non-zero if any file failed. Mirrors the repo's
// chrome-extension/tests/run_tests.mjs convention. No deps.

import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(__dirname)
  .filter((f) => f.startsWith("test_") && f.endsWith(".mjs"))
  .sort();

if (files.length === 0) {
  console.error("FAIL: no test_*.mjs files found — a zero-test run is a failure, not a pass.");
  process.exit(1);
}

let failed = 0;
for (const f of files) {
  console.log(`\n--- ${f} ---`);
  // `node --test <file>` prints a TAP summary with the test count and exits non-zero on
  // any failing test.
  const r = spawnSync(process.execPath, ["--test", join(__dirname, f)], {
    stdio: "inherit",
  });
  if (r.status !== 0) {
    console.error(`FAIL: ${f} exited ${r.status}`);
    failed++;
  }
}

console.log(`\n=== ${files.length - failed}/${files.length} test files passed ===`);
process.exit(failed === 0 ? 0 : 1);

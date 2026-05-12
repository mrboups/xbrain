// Walks every test_*.mjs file in this directory, runs it in a fresh node
// process, and exits non-zero if any failed. No deps.

import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(__dirname)
  .filter((f) => f.startsWith("test_") && f.endsWith(".mjs"))
  .sort();

let failed = 0;
for (const f of files) {
  console.log(`\n--- ${f} ---`);
  const r = spawnSync(process.execPath, [join(__dirname, f)], {
    stdio: "inherit",
  });
  if (r.status !== 0) {
    console.error(`FAIL: ${f} exited ${r.status}`);
    failed++;
  }
}

console.log(`\n=== ${files.length - failed}/${files.length} test files passed ===`);
process.exit(failed === 0 ? 0 : 1);

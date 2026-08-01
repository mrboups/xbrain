/**
 * Drift gate for the shared chat core (Phase 27, Plan 27-01, D-27-04).
 *
 * packages/chat-core is the ONLY editable copy of the portable chat logic.
 * chrome-extension/chat_core/ and app-site/app/chat_core/ are GENERATED,
 * byte-identical copies produced by `make sync-chat-core`. This test is what
 * stops a second copy from quietly becoming a fork:
 *
 *   1. every source module exists in BOTH targets and matches byte-for-byte;
 *   2. neither target holds a .js the source no longer defines (no orphans);
 *   3. PORTABILITY — no source module names the browser-extension namespace,
 *      comments included. Comments are DELIBERATELY in scope: a gate that
 *      passes because prose was reworded to dodge it is worse than no gate, so
 *      the rule is "the token does not appear", not "the token is not called";
 *   4. api.js carries no origin literal — each surface injects its own baseUrl,
 *      so a shared file cannot repoint the PWA at the extension's backend.
 *
 * Pure node test — fs reads + Buffer.compare, no browser, no deps. Picked up by
 * run_tests.mjs (file name starts with test_). Mirrors the assert style of
 * test_theme.mjs.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const SOURCE_DIR = join(REPO_ROOT, "packages", "chat-core");
const TARGETS = [
  join("chrome-extension", "chat_core"),
  join("app-site", "app", "chat_core"),
];

let passed = 0;
let failed = 0;
function test(name, body) {
  try {
    body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

const jsFiles = (dir) =>
  existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith(".js")).sort() : [];

const SOURCES = jsFiles(SOURCE_DIR);

// ---- 0. The source of truth exists at all ----

test("packages/chat-core exists and holds the shared modules", () => {
  assert.ok(
    existsSync(SOURCE_DIR),
    "packages/chat-core is missing — it is the single source of truth for the chat logic",
  );
  assert.ok(
    SOURCES.length > 0,
    "packages/chat-core has no .js modules — nothing to share between surfaces",
  );
  // The shim contract and the API client are the two modules the whole
  // extraction hangs on; name them so a silent deletion is loud.
  for (const required of ["platform.js", "api.js"]) {
    assert.ok(
      SOURCES.includes(required),
      `packages/chat-core/${required} is missing — ${required === "platform.js" ? "the platform shim contract" : "the shared API client"} must live in the shared package`,
    );
  }
});

// ---- 1. Byte-identical copies in every target ----

for (const target of TARGETS) {
  const absDir = join(REPO_ROOT, target);

  for (const name of SOURCES) {
    test(`${target}/${name} is byte-identical to the source`, () => {
      const dst = join(absDir, name);
      assert.ok(
        existsSync(dst),
        `${target}/${name} is missing — run \`make sync-chat-core\``,
      );
      const srcBuf = readFileSync(join(SOURCE_DIR, name));
      const dstBuf = readFileSync(dst);
      assert.equal(
        Buffer.compare(srcBuf, dstBuf),
        0,
        `${target}/${name} has DRIFTED from packages/chat-core/${name} — ` +
          "the copy is generated; edit the source and re-run `make sync-chat-core`",
      );
    });
  }

  // ---- 2. No orphans ----

  test(`${target} holds no module absent from packages/chat-core`, () => {
    const orphans = jsFiles(absDir).filter((f) => !SOURCES.includes(f));
    assert.deepEqual(
      orphans,
      [],
      `${target} still ships ${JSON.stringify(orphans)} — a module deleted from ` +
        "the source must not survive in a copy, or the surface imports code that no longer exists",
    );
  });
}

// ---- 3. Portability gate (comments included, on purpose) ----

// Built by concatenation so this test file never itself contains the token it
// bans — otherwise a future "scan the tests too" sweep would trip on the gate.
const EXTENSION_NAMESPACE = "chrome" + ".";

for (const name of SOURCES) {
  test(`packages/chat-core/${name} names no browser-extension namespace`, () => {
    const src = readFileSync(join(SOURCE_DIR, name), "utf8");
    const hit = src.indexOf(EXTENSION_NAMESPACE);
    assert.equal(
      hit,
      -1,
      `packages/chat-core/${name} contains the browser-extension namespace at index ${hit} — ` +
        "chat-core runs in the PWA too; every capability must cross the platform shim " +
        "(comments count: a docstring hit is indistinguishable from a real dependency)",
    );
  });
}

// ---- 4. No origin literal in the API client (T-27-01-03) ----

test("api.js hardcodes no origin — baseUrl is injected by each surface", () => {
  const src = readFileSync(join(SOURCE_DIR, "api.js"), "utf8");
  assert.ok(
    !src.includes("://"),
    "packages/chat-core/api.js contains a URL scheme literal — each surface must inject its own baseUrl",
  );
  assert.ok(
    !src.includes("grooveos"),
    "packages/chat-core/api.js names a deployment host — a shared file must not pin one surface's backend",
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

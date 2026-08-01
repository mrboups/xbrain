/**
 * The platform shim contract, and the ONE capability the two surfaces do not
 * share (Phase 27, D-27-04).
 *
 * `currentPageUrl` is the extension's ability to read the tab the user is
 * looking at. A web page has no such thing, so the PWA's shim answers null and
 * the send-a-link flow asks the person to paste one instead.
 *
 * THE WEB BRANCH IS WHY THIS FILE EXISTS. Nobody exercises it by hand: the
 * extension developer always has a tab, so the null path is only ever taken on
 * the surface they are not looking at. Left unchecked, the plausible failure is
 * not a crash but a "send the current page" button on the web that silently
 * sends nothing, or sends this app's own URL.
 *
 * The web shim is imported and RUN here (it touches no browser global at import
 * time, by design). The extension's shim is read as text: importing it would
 * need the whole extension namespace stubbed, and what matters about it is that
 * it asks the browser for the active tab and refuses anything that is not
 * http(s).
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const CORE_DIR = join(REPO_ROOT, "packages", "chat-core");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

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

const pending = [];
function testAsync(name, body) {
  pending.push([name, body]);
}

const { PLATFORM_MEMBERS, assertPlatform } = await import(
  pathToFileURL(join(CORE_DIR, "platform.js")).href
);
const { webPlatform } = await import(
  pathToFileURL(join(APP_DIR, "platform_web.js")).href
);

/** A shim that satisfies every member, so a test can remove exactly one. */
function completePlatform() {
  return {
    storage: { get: async () => ({}), set: async () => {}, remove: async () => {} },
    openUrl: () => {},
    notify: async () => null,
    currentPageUrl: async () => null,
  };
}

// ---- 1. The contract names the capability -------------------------------

test("currentPageUrl is part of the platform contract, not an optional extra", () => {
  assert.ok(
    PLATFORM_MEMBERS.includes("currentPageUrl"),
    "a shim may not quietly omit it — the null case is the one that has to be handled",
  );
});

test("assertPlatform rejects a shim that omits currentPageUrl, and names it", () => {
  const partial = completePlatform();
  delete partial.currentPageUrl;
  assert.throws(
    () => assertPlatform(partial),
    /currentPageUrl/,
    "a half-implemented shim must fail at import, not silently no-op at first use",
  );
});

test("assertPlatform accepts a complete shim", () => {
  const p = completePlatform();
  assert.equal(assertPlatform(p), p, "it returns the same object, so callers can wrap");
});

// ---- 2. The web shim answers null, and says so on purpose ---------------

testAsync("the PWA's shim returns null rather than inventing a current page", async () => {
  const value = await webPlatform.currentPageUrl();
  assert.equal(
    value,
    null,
    "a web page can only see its own URL; returning it would send the wrong thing",
  );
});

testAsync("the PWA's shim is stable — a second read is still null", async () => {
  assert.equal(await webPlatform.currentPageUrl(), null);
  assert.equal(await webPlatform.currentPageUrl(), null);
});

test("importing the PWA's shim needs no browser at all", () => {
  // It was imported at the top of this file. If it touched localStorage,
  // Notification or window at module level, that import would already have
  // thrown - and the app would white-screen in any partitioned context.
  assert.ok(webPlatform, "platform_web.js imported without a browser present");
  assert.equal(typeof webPlatform.currentPageUrl, "function");
});

// ---- 3. The extension shim really reads the active tab ------------------

const chromeShim = readFileSync(
  join(REPO_ROOT, "chrome-extension", "platform_chrome.js"),
  "utf8",
);

test("the extension's shim resolves the ACTIVE tab of the CURRENT window", () => {
  assert.ok(
    /currentPageUrl/.test(chromeShim),
    "chrome-extension/platform_chrome.js must implement currentPageUrl",
  );
  assert.ok(
    /active:\s*true/.test(chromeShim) && /currentWindow:\s*true/.test(chromeShim),
    "it must ask for the active tab of the current window, not the first tab it finds",
  );
});

test("the extension's shim refuses a non-http(s) tab", () => {
  // An internal page, a PDF viewer or a revoked permission must produce null,
  // not a URL a teammate could never open.
  assert.ok(
    /\^https\?:/.test(chromeShim),
    "the tab URL must be scheme-checked before it is offered as a link to send",
  );
  assert.ok(
    /return null/.test(chromeShim),
    "an unreadable tab must degrade to null, the same value the web shim always returns",
  );
});

// ---- 4. Both shims are asserted at import -------------------------------

for (const [label, src] of [
  ["chrome-extension/platform_chrome.js", chromeShim],
  ["app-site/app/platform_web.js", readFileSync(join(APP_DIR, "platform_web.js"), "utf8")],
]) {
  test(`${label} asserts the contract at module load`, () => {
    assert.ok(
      /assertPlatform\(/.test(src),
      `${label} must call assertPlatform so a missing member fails loudly at import`,
    );
  });
}

for (const [name, body] of pending) {
  try {
    await body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

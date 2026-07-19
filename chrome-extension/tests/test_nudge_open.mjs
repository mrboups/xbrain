/**
 * Tests for chrome-extension/nudge_open.js (Phase 22 push-a-link, Plan 22-02).
 *
 * Proves the consent core on the RECEIVE side:
 *   (a) opt-in ON + a safe https url  → builds ONE notification whose message
 *       is the FULL literal url, whose title names the sender; persistPending
 *       is called with (returnedId, url); handleOpenUrl resolves to that id.
 *   (b) opt-in OFF (allowOpenLinkRequests:false) → no notification, returns null.
 *   (c) an unsafe scheme (javascript:/file:) with opt-in ON → no notification,
 *       returns null (defense-in-depth over the server check).
 *   (d) a wrong-type event ({type:"message"}) → no notification, returns null.
 *   (e) isSafeHttpUrl unit rows: http/https true; javascript/data/file/mailto/
 *       empty/whitespace/non-string false.
 *
 * Structural guarantee (D-22-02 / T-22-08): the module source contains NO
 * occurrence of the substring "tabs" — the receive handler literally cannot
 * open a browser tab. The tab-open is deferred to an explicit click (Plan 03).
 *
 * Pure node test — no chrome.* polyfill. All side effects go through injected
 * `deps`. Picked up by run_tests.mjs (file name starts with test_).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { isSafeHttpUrl, handleOpenUrl } from "../nudge_open.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

let passed = 0;
let failed = 0;
async function test(name, body) {
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

// A deps factory: records every notify + persistPending call. `settings`
// overrides let a case flip the opt-out.
function makeDeps(settings = { allowOpenLinkRequests: true }) {
  const calls = { notify: [], persist: [] };
  return {
    calls,
    getSettings: async () => settings,
    notify: async (opts) => {
      calls.notify.push(opts);
      return "notif-generated-id";
    },
    persistPending: async (id, url) => {
      calls.persist.push({ id, url });
    },
  };
}

// ---------- (a) opt-in ON + safe url → notify once with the real url ----------

await test("valid https event notifies once with the FULL url + sender, returns id", async () => {
  const deps = makeDeps();
  const url = "https://docs.example.com/page?x=1#frag";
  const data = {
    type: "open_url",
    url,
    from: { display_name: "Alice", sub: "gh|123" },
    team_id: "t-1",
    team_slug: "acme",
  };

  const id = await handleOpenUrl(data, deps);

  assert.equal(deps.calls.notify.length, 1, "notify must be called exactly once");
  const opts = deps.calls.notify[0];
  assert.equal(opts.message, url, "notification message must be the FULL literal url");
  assert.match(opts.title, /Alice/, "title must name the sender (display_name)");
  assert.equal(opts.type, "basic");
  assert.equal(opts.iconUrl, "icon128.png");
  assert.equal(deps.calls.persist.length, 1, "persistPending must be called once");
  assert.deepEqual(deps.calls.persist[0], { id: "notif-generated-id", url });
  assert.equal(id, "notif-generated-id", "handleOpenUrl returns the notification id");
});

await test("falls back to sender.sub, then to a generic label, when display_name is absent", async () => {
  const url = "http://a.example/b";

  const deps1 = makeDeps();
  await handleOpenUrl(
    { type: "open_url", url, from: { display_name: null, sub: "gh|777" } },
    deps1,
  );
  assert.match(deps1.calls.notify[0].title, /gh\|777/, "falls back to sub");

  const deps2 = makeDeps();
  await handleOpenUrl({ type: "open_url", url, from: null }, deps2);
  assert.match(deps2.calls.notify[0].title, /teammate/i, "generic fallback label");
});

// ---------- (b) opt-out OFF suppresses the notification entirely ----------

await test("opt-out OFF: no notification, returns null", async () => {
  const deps = makeDeps({ allowOpenLinkRequests: false });
  const id = await handleOpenUrl(
    { type: "open_url", url: "https://safe.example/x", from: { display_name: "Bob" } },
    deps,
  );
  assert.equal(deps.calls.notify.length, 0, "notify must NOT be called when opted out");
  assert.equal(deps.calls.persist.length, 0, "persistPending must NOT be called");
  assert.equal(id, null);
});

// ---------- (c) unsafe scheme dropped client-side even with opt-in ON ----------

await test("javascript:/file: urls are dropped: no notification, returns null", async () => {
  for (const bad of ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,<b>x"]) {
    const deps = makeDeps();
    const id = await handleOpenUrl(
      { type: "open_url", url: bad, from: { display_name: "Mallory" } },
      deps,
    );
    assert.equal(deps.calls.notify.length, 0, `notify must NOT fire for ${bad}`);
    assert.equal(id, null, `returns null for ${bad}`);
  }
});

// ---------- (d) wrong event type ignored ----------

await test("wrong event type ({type:'message'}) is ignored, returns null", async () => {
  const deps = makeDeps();
  const id = await handleOpenUrl(
    { type: "message", url: "https://safe.example/x" },
    deps,
  );
  assert.equal(deps.calls.notify.length, 0);
  assert.equal(id, null);

  // null/undefined data are also ignored.
  assert.equal(await handleOpenUrl(null, makeDeps()), null);
  assert.equal(await handleOpenUrl(undefined, makeDeps()), null);
});

// ---------- (e) isSafeHttpUrl unit rows ----------

await test("isSafeHttpUrl: http/https true; everything else false", () => {
  for (const good of [
    "https://x/y",
    "http://a.b?c#d",
    "https://example.com",
    "http://127.0.0.1:8080/path",
  ]) {
    assert.equal(isSafeHttpUrl(good), true, `${good} should be safe`);
  }
  for (const bad of [
    "javascript:alert(1)",
    "data:text/html,x",
    "file:///x",
    "mailto:a@b",
    "ftp://host/f",
    "",
    "   ",
    "not a url",
    null,
    undefined,
    42,
    {},
  ]) {
    assert.equal(isSafeHttpUrl(bad), false, `${JSON.stringify(bad)} should be unsafe`);
  }
});

// ---------- structural: the module cannot open a tab ----------

await test("nudge_open.js source contains NO 'tabs' capability (D-22-02)", () => {
  const src = readFileSync(join(__dirname, "..", "nudge_open.js"), "utf8");
  assert.equal(
    src.includes("tabs"),
    false,
    "nudge_open.js must not reference 'tabs' — auto-open is structurally impossible",
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

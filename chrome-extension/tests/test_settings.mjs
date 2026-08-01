/**
 * Tests for chrome-extension/settings.js (quick task 260512-spx).
 *
 * Verifies the schema invariants:
 *   1. Defaults: both toggles ON when storage is empty.
 *   2. Persisted booleans win over defaults.
 *   3. saveSettings merges patches (partial updates don't reset other keys).
 *   4. mergeSettings strips unknown keys + ignores non-boolean values.
 */

import assert from "node:assert/strict";
import {
  SETTINGS_KEY,
  DEFAULT_SETTINGS,
  loadSettings,
  saveSettings,
  mergeSettings,
} from "../settings.js";

function makeStorageArea() {
  const store = new Map();
  return {
    _store: store,
    async get(keys) {
      const out = {};
      const arr = Array.isArray(keys) ? keys : [keys];
      for (const k of arr) if (store.has(k)) out[k] = store.get(k);
      return out;
    },
    async set(obj) {
      for (const [k, v] of Object.entries(obj)) store.set(k, v);
    },
  };
}

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

await test("DEFAULT_SETTINGS has both toggles ON", () => {
  assert.equal(DEFAULT_SETTINGS.openInSidePanel, true);
  assert.equal(DEFAULT_SETTINGS.autoFillLibreChat, true);
});

await test("loadSettings returns defaults when storage empty", async () => {
  const s = await loadSettings(makeStorageArea());
  assert.deepEqual(s, {
    openInSidePanel: true,
    autoFillLibreChat: true,
    clipDefaultProject: null,
    clipDefaultTruthLevel: "EPHEMERAL",
    clipSkipOverlay: false,
    allowOpenLinkRequests: true,
    autoOpenLinkRequests: false,
  });
});

await test("autoOpenLinkRequests defaults OFF — auto-open is opt-in", async () => {
  // Letting a teammate open tabs in your browser without a click is a real
  // capability, so the default must stay the consent notification. A future edit
  // that flips this to true has to change this assertion deliberately.
  assert.equal(DEFAULT_SETTINGS.autoOpenLinkRequests, false);
  const s = await loadSettings(makeStorageArea());
  assert.equal(s.autoOpenLinkRequests, false);
});

// ─── Phase 22 push-a-link (Plan 22-02) — recipient opt-out, default ON ───────

await test("allowOpenLinkRequests defaults ON (D-22-04)", () => {
  assert.equal(DEFAULT_SETTINGS.allowOpenLinkRequests, true);
});

await test("mergeSettings keeps an explicit allowOpenLinkRequests:false", () => {
  const out = mergeSettings({ allowOpenLinkRequests: false });
  assert.equal(out.allowOpenLinkRequests, false);
});

await test("mergeSettings type-guards allowOpenLinkRequests → default true", () => {
  // A non-boolean must be ignored and fall back to the ON default.
  assert.equal(mergeSettings({ allowOpenLinkRequests: "nope" }).allowOpenLinkRequests, true);
  assert.equal(mergeSettings({ allowOpenLinkRequests: 0 }).allowOpenLinkRequests, true);
});

await test("a stored object omitting allowOpenLinkRequests yields true", async () => {
  const storage = makeStorageArea();
  await storage.set({ [SETTINGS_KEY]: { openInSidePanel: false } });
  const s = await loadSettings(storage);
  assert.equal(s.allowOpenLinkRequests, true);
});

await test("loadSettings honors persisted false values", async () => {
  const storage = makeStorageArea();
  await storage.set({
    [SETTINGS_KEY]: { openInSidePanel: false, autoFillLibreChat: true },
  });
  const s = await loadSettings(storage);
  assert.equal(s.openInSidePanel, false);
  assert.equal(s.autoFillLibreChat, true);
});

await test("saveSettings patches without losing other keys", async () => {
  const storage = makeStorageArea();
  await saveSettings(storage, { openInSidePanel: false });
  let s = await loadSettings(storage);
  assert.equal(s.openInSidePanel, false);
  assert.equal(s.autoFillLibreChat, true); // unchanged default

  await saveSettings(storage, { autoFillLibreChat: false });
  s = await loadSettings(storage);
  assert.equal(s.openInSidePanel, false); // still false from prior save
  assert.equal(s.autoFillLibreChat, false);
});

await test("mergeSettings strips unknown keys and wrong-typed values", () => {
  const out = mergeSettings({
    openInSidePanel: false,
    autoFillLibreChat: "yes please", // not a boolean → ignored
    unknownKey: true, // not in schema → stripped
    clipDefaultProject: 42, // wrong type for string|null → ignored
    clipDefaultTruthLevel: null, // null not allowed for required string → ignored
  });
  assert.equal(out.openInSidePanel, false);
  assert.equal(out.autoFillLibreChat, true); // ignored → default
  assert.equal(out.unknownKey, undefined);
  assert.equal(out.clipDefaultProject, null); // default
  assert.equal(out.clipDefaultTruthLevel, "EPHEMERAL"); // default
});

await test("mergeSettings accepts string + null for nullable keys", () => {
  const out = mergeSettings({
    clipDefaultProject: "fundraising",
    clipDefaultTruthLevel: "VALIDATED",
    clipSkipOverlay: true,
  });
  assert.equal(out.clipDefaultProject, "fundraising");
  assert.equal(out.clipDefaultTruthLevel, "VALIDATED");
  assert.equal(out.clipSkipOverlay, true);

  const out2 = mergeSettings({ clipDefaultProject: null });
  assert.equal(out2.clipDefaultProject, null); // null explicitly OK
});

await test("mergeSettings handles null/undefined input safely", () => {
  assert.deepEqual(mergeSettings(null), DEFAULT_SETTINGS);
  assert.deepEqual(mergeSettings(undefined), DEFAULT_SETTINGS);
  assert.deepEqual(mergeSettings({}), DEFAULT_SETTINGS);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

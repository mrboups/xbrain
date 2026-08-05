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
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  SETTINGS_KEY,
  DEFAULT_SETTINGS,
  loadSettings,
  saveSettings,
  mergeSettings,
  notificationsEnabled,
} from "../settings.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(__dirname, "..", rel), "utf8");

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
    clipDefaultTruthLevel: "WORKING",
    clipSkipOverlay: false,
    allowOpenLinkRequests: true,
    autoOpenLinkRequests: false,
    showNotifications: true,
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
  // WORKING since 2026-08-05: EPHEMERAL is excluded from every recall path, so the
  // old default meant a clipped page was invisible to the agent until a human
  // promoted it by hand — which nobody did.
  assert.equal(out.clipDefaultTruthLevel, "WORKING"); // default
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

// ─── The desktop-notification master switch ─────────────────────────────────
//
// A setting nothing asserts is a setting that silently stops working, and this
// one is easy to half-implement: mute the surface you can see, leave the service
// worker firing. The tests below pin the DEFAULT, the FAIL-OPEN behaviour, and —
// most importantly — that every call site in the extension consults it.

await test("showNotifications defaults ON — notifications worked before this switch", () => {
  // Shipping it OFF would take away a behaviour people already rely on. That is
  // a regression wearing a feature's clothes; flipping this needs a deliberate
  // edit here.
  assert.equal(DEFAULT_SETTINGS.showNotifications, true);
});

await test("notificationsEnabled reports the stored choice", async () => {
  const storage = makeStorageArea();
  assert.equal(await notificationsEnabled(storage), true, "empty storage -> the default");

  await saveSettings(storage, { showNotifications: false });
  assert.equal(await notificationsEnabled(storage), false);

  await saveSettings(storage, { showNotifications: true });
  assert.equal(await notificationsEnabled(storage), true);
});

await test("turning notifications off leaves the other settings alone", async () => {
  const storage = makeStorageArea();
  await saveSettings(storage, { autoFillLibreChat: false });
  await saveSettings(storage, { showNotifications: false });
  const s = await loadSettings(storage);
  assert.equal(s.showNotifications, false);
  assert.equal(s.autoFillLibreChat, false);
  assert.equal(s.allowOpenLinkRequests, true);
});

await test("notificationsEnabled FAILS OPEN when storage is unreadable", async () => {
  // We do not know what they chose, and the default is on. Muting on a storage
  // hiccup would drop a teammate's link request with nothing to explain it.
  const broken = {
    async get() {
      throw new Error("storage unavailable");
    },
  };
  assert.equal(await notificationsEnabled(broken), true);
});

await test("a non-boolean in storage falls back to ON, not to silence", async () => {
  assert.equal(mergeSettings({ showNotifications: "no" }).showNotifications, true);
  assert.equal(mergeSettings({ showNotifications: 0 }).showNotifications, true);
  assert.equal(mergeSettings({ showNotifications: null }).showNotifications, true);
});

// ---- The gate: EVERY call site consults the setting ------------------------

/** Comments out, so prose describing the rule is not mistaken for a call. */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

await test("every chrome.notifications.create in the extension is behind the setting", () => {
  // THE POINT OF THIS FILE. A toggle honoured in some places and not others is
  // worse than no toggle: the person switches it off, notifications keep
  // arriving, and they conclude the settings page is broken.
  //
  // Two shapes are accepted as "gated": a call inside a function that consults
  // notificationsEnabled, or a call routed through the platform shim's notify.
  const files = ["background.js", "platform_chrome.js", "popup.js"];
  const offenders = [];
  for (const name of files) {
    const src = stripComments(read(name));
    const creates = [...src.matchAll(/chrome\.notifications\.create\s*\(/g)];
    for (const m of creates) {
      // Look back a bounded distance for the guard. Every real call site keeps
      // it within a few lines; anything further away is not a guard, it is a
      // coincidence.
      const before = src.slice(Math.max(0, m.index - 600), m.index);
      if (!before.includes("notificationsEnabled(")) {
        offenders.push(`${name}@${m.index}`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `ungated chrome.notifications.create call site(s): ${offenders.join(", ")} — ` +
      "route it through chromePlatform.notify, or guard it with notificationsEnabled()",
  );
});

await test("the popup raises notifications through the shim, never a bare create", () => {
  const popup = stripComments(read("popup.js"));
  assert.ok(
    !popup.includes("chrome.notifications.create"),
    "popup.js must go through chromePlatform.notify — one call site, one gate",
  );
  assert.ok(
    popup.includes("chromePlatform.notify("),
    "popup.js must hand the shim's notify to handleOpenUrl",
  );
});

await test("the shim's notify returns null when muted, which the nudge path handles", () => {
  const shim = stripComments(read("platform_chrome.js"));
  assert.match(
    shim,
    /notificationsEnabled\(chrome\.storage\.sync\)\)\)\s*return null/,
    "platform_chrome.notify must answer null when muted — chat-core reads null as " +
      "'no notification shown' and falls back to in-page UI, so a muted teammate still SEES the request",
  );
});

await test("the service worker's own notifications are gated too", () => {
  const bg = stripComments(read("background.js"));
  assert.match(
    bg,
    /async function _notify\([\s\S]{0,300}?notificationsEnabled\(/,
    "background.js's _notify must consult the setting — clip results are notifications like any other",
  );
  // The "selection captured" fallback used to build its own create() call. It
  // must route through _notify, or it survives the toggle.
  const createsOutsideNotify = [...bg.matchAll(/chrome\.notifications\.create\s*\(/g)];
  assert.equal(
    createsOutsideNotify.length,
    1,
    "background.js must have exactly ONE create call site (inside _notify); " +
      `found ${createsOutsideNotify.length}`,
  );
});

await test("the bell and the options checkbox edit the SAME setting", () => {
  const popupJs = stripComments(read("popup.js"));
  const optionsJs = stripComments(read("options.js"));
  const popupHtml = read("popup.html");
  const optionsHtml = read("options.html");

  assert.ok(popupHtml.includes('id="btn-notifications"'), "the popup header needs the bell");
  assert.ok(
    optionsHtml.includes('id="opt-show-notifications"'),
    "the options page needs the checkbox, consistent with the other toggles",
  );
  for (const [name, src] of [["popup.js", popupJs], ["options.js", optionsJs]]) {
    assert.ok(
      /showNotifications/.test(src),
      `${name} must read and write showNotifications — a second key would let the two surfaces disagree`,
    );
  }
});

await test("the bell shows the STORED state on open, not an assumed one", () => {
  const popupJs = stripComments(read("popup.js"));
  const wire = /function wireNotificationToggle\(\)\s*\{[\s\S]*?\n\}/.exec(popupJs);
  assert.ok(wire, "popup.js must declare wireNotificationToggle()");
  assert.ok(
    wire[0].includes("loadSettings(") && wire[0].includes("data-state"),
    "the bell must paint from storage on open — the choice may have been made on " +
      "another machine (storage.sync) or on the options page a moment ago",
  );
  assert.ok(
    wire[0].includes("saveSettings("),
    "clicking the bell must persist, not just repaint",
  );
});

await test("popup.css draws a different bell for each state", () => {
  const css = read("popup.css").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(
    css,
    /#btn-notifications\[data-state="on"\]\s+\.xb-ico-bell\s*\{[^}]*display:\s*block/,
    "the ringing bell shows only when the setting is on",
  );
  assert.match(
    css,
    /#btn-notifications\s+\.xb-ico-bell-off\s*\{[^}]*display:\s*block/,
    "the struck bell is the default state's glyph",
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

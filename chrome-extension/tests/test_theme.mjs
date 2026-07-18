/**
 * Tests for chrome-extension/theme.js (Plan 20-01 — shadcn Neutral theme
 * runtime). theme.js is a pure, dependency-free module: no chrome.*, no DOM
 * globals. It only resolves the initial theme (stored choice wins over the OS
 * preference) and stamps `data-theme` on a root element passed in by the
 * caller. Persistence itself lives in popup.js (chrome.storage.local); here we
 * prove the precedence + a full persistence round-trip using plain stubs.
 *
 * Mirrors the assert style of test_settings.mjs.
 */

import assert from "node:assert/strict";
import {
  THEME_STORAGE_KEY,
  resolveInitialTheme,
  applyTheme,
} from "../theme.js";

// Tiny root element stub — records the last data-theme stamped on it.
function makeRootStub() {
  return {
    _a: {},
    setAttribute(k, v) {
      this._a[k] = v;
    },
    getAttribute(k) {
      return this._a[k];
    },
  };
}

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

// ---- resolveInitialTheme precedence ----

test("stored 'dark' wins over prefersDark=false", () => {
  assert.equal(resolveInitialTheme({ storedTheme: "dark", prefersDark: false }), "dark");
});

test("stored 'light' wins over prefersDark=true", () => {
  assert.equal(resolveInitialTheme({ storedTheme: "light", prefersDark: true }), "light");
});

test("no stored choice → prefersDark=true falls back to dark", () => {
  assert.equal(resolveInitialTheme({ storedTheme: null, prefersDark: true }), "dark");
});

test("no stored choice → prefersDark=false falls back to light", () => {
  assert.equal(resolveInitialTheme({ storedTheme: null, prefersDark: false }), "light");
});

test("invalid stored value is ignored → falls back to prefers", () => {
  assert.equal(resolveInitialTheme({ storedTheme: "garbage", prefersDark: true }), "dark");
  assert.equal(resolveInitialTheme({ storedTheme: "", prefersDark: false }), "light");
  assert.equal(resolveInitialTheme({ storedTheme: undefined, prefersDark: true }), "dark");
});

// ---- applyTheme ----

test("applyTheme stamps data-theme on root and returns the mode", () => {
  const root = makeRootStub();
  const ret = applyTheme(root, "dark");
  assert.equal(root.getAttribute("data-theme"), "dark");
  assert.equal(ret, "dark");

  const ret2 = applyTheme(root, "light");
  assert.equal(root.getAttribute("data-theme"), "light");
  assert.equal(ret2, "light");
});

// ---- THEME_STORAGE_KEY ----

test("THEME_STORAGE_KEY is a stable non-empty string", () => {
  assert.equal(typeof THEME_STORAGE_KEY, "string");
  assert.ok(THEME_STORAGE_KEY.length > 0);
  // Frozen contract: popup.js reads/writes this exact key in chrome.storage.local.
  assert.equal(THEME_STORAGE_KEY, "xbrain_theme_v1");
});

// ---- persistence round-trip (stored choice survives across popup opens) ----

test("persistence round-trip: toggle choice wins on the next open", () => {
  // A stand-in for chrome.storage.local keyed by THEME_STORAGE_KEY.
  const store = new Map();
  const root = makeRootStub();

  // First open on a dark OS with no stored choice → dark.
  const first = resolveInitialTheme({
    storedTheme: store.get(THEME_STORAGE_KEY) ?? null,
    prefersDark: true,
  });
  assert.equal(first, "dark");
  applyTheme(root, first);
  assert.equal(root.getAttribute("data-theme"), "dark");

  // User toggles to light — popup.js persists the choice.
  applyTheme(root, "light");
  store.set(THEME_STORAGE_KEY, "light");
  assert.equal(root.getAttribute("data-theme"), "light");

  // Next popup open, still a dark OS: the stored 'light' must win.
  const second = resolveInitialTheme({
    storedTheme: store.get(THEME_STORAGE_KEY) ?? null,
    prefersDark: true,
  });
  assert.equal(second, "light");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

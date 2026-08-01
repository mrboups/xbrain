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
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  THEME_STORAGE_KEY,
  resolveInitialTheme,
  applyTheme,
} from "../../packages/chat-core/theme.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const readRepo = (...p) => readFileSync(join(REPO_ROOT, ...p), "utf8");

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

// ===========================================================================
// The control itself: one segmented switch, three surfaces, one storage key.
//
// The theme used to be a checkbox in the middle of the options list — an item
// to read past rather than a display mode to flip. It is now the same .seg
// control on all three surfaces. What must not drift: the KEY (or two surfaces
// disagree about which theme is on) and the icon SIZING (a viewBox is not a
// size; without width/height these render at ~300x150 and destroy the row).
// ===========================================================================

/** Every surface that carries the switch: [markup, script, stylesheet]. */
const THEME_SURFACES = [
  ["chrome-extension/options.html", "chrome-extension/options.js", "chrome-extension/options.html"],
  ["app-site/app/index.html", "app-site/app/app.js", "app-site/app/app.css"],
];

for (const [markupPath, scriptPath, cssPath] of THEME_SURFACES) {
  test(`${markupPath}: the theme switch is a .seg with a light and a dark button`, () => {
    const markup = readRepo(...markupPath.split("/"));
    assert.ok(markup.includes('class="seg"'), "the switch must be the shared .seg control");
    for (const id of ["btn-theme-light", "btn-theme-dark"]) {
      assert.ok(markup.includes(`id="${id}"`), `missing #${id}`);
    }
    assert.ok(
      /<div[^>]*class="seg"[^>]*role="group"/.test(markup),
      "a two-button group needs role=group or it is two unrelated buttons to assistive tech",
    );
    // Icon-only, so each half needs a name of its own.
    for (const label of ["Light theme", "Dark theme"]) {
      assert.ok(markup.includes(`aria-label="${label}"`), `missing aria-label="${label}"`);
    }
  });

  test(`${scriptPath}: the switch persists under THEME_STORAGE_KEY`, () => {
    const src = readRepo(...scriptPath.split("/"));
    assert.ok(
      src.includes("THEME_STORAGE_KEY"),
      "a key of its own would let two surfaces disagree about which theme is on",
    );
    assert.ok(
      src.includes("applyTheme(") && src.includes("aria-pressed"),
      "the switch must both apply the theme and show which half is pressed",
    );
    assert.ok(
      /btn-theme-light/.test(src) && /btn-theme-dark/.test(src),
      "both halves must be wired, or one of them is decoration",
    );
  });

  test(`${cssPath}: the segment icons are given an explicit size`, () => {
    const css = readRepo(...cssPath.split("/")).replace(/\/\*[\s\S]*?\*\//g, "");
    const m = /\.seg\s+button\s+svg\s*\{([^}]*)\}/.exec(css);
    assert.ok(m, ".seg button svg must declare a size — a viewBox is not one");
    assert.match(m[1], /width:\s*15px/, "15px keeps the icon on the shadcn scale");
    assert.match(m[1], /height:\s*15px/);
  });

  test(`${cssPath}: the pressed segment is filled, and the group is radius 0`, () => {
    const css = readRepo(...cssPath.split("/")).replace(/\/\*[\s\S]*?\*\//g, "");
    const seg = /(?:^|[};])\s*\.seg\s*\{([^}]*)\}/m.exec(css);
    assert.ok(seg, "no .seg rule");
    const radius = /border-radius:\s*([^;]+)/.exec(seg[1]);
    assert.ok(radius, ".seg must declare a border-radius");
    const value = radius[1].trim();
    assert.ok(
      value === "0" || value === "0px" || value === "var(--radius)",
      `.seg radius is ${value} — the sharp corner is the Neutral signal`,
    );
    const pressed = /\.seg\s+button\[aria-pressed="true"\]\s*\{([^}]*)\}/.exec(css);
    assert.ok(pressed, "no rule for the pressed segment — nothing would show which is active");
    assert.match(pressed[1], /background:/, "the pressed segment must be FILLED, not merely tinted");
  });
}

test("the old dark-theme checkbox is gone, not left beside the switch", () => {
  // Two controls for one setting is how they end up disagreeing.
  const html = readRepo("chrome-extension", "options.html");
  const js = readRepo("chrome-extension", "options.js");
  assert.ok(!html.includes("opt-dark-theme"), "options.html still declares the checkbox");
  assert.ok(!js.includes("opt-dark-theme"), "options.js still binds the checkbox");
});

test("the switch sits with the page title, not inside the settings list", () => {
  const html = readRepo("chrome-extension", "options.html");
  const head = /<div class="page-head">([\s\S]*?)<\/div>\s*<\/div>/.exec(html);
  assert.ok(head, "options.html must have a .page-head row");
  assert.ok(head[0].includes("<h1>"), "the title belongs in that row");
  assert.ok(head[0].includes('class="seg"'), "and so does the switch");
  const card = /<div class="settings-card">([\s\S]*?)<\/div>\s*<!--/.exec(html);
  if (card) {
    assert.ok(
      !card[1].includes('class="seg"'),
      "the switch must not ALSO appear in the settings list",
    );
  }
  const css = html.replace(/\/\*[\s\S]*?\*\//g, "");
  const ph = /\.page-head\s*\{([^}]*)\}/.exec(css);
  assert.ok(ph, ".page-head needs a rule or the switch sits under the title");
  assert.match(ph[1], /justify-content:\s*space-between/, "title left, switch right");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

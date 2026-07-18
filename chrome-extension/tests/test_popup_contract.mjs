/**
 * Selector-contract + token guardrail for the extension chat popup (Plan 20-01).
 *
 * Pure node test — fs reads of popup.js / popup.html / popup.css + regex
 * assertions, no browser. It FAILS (exit 1) the moment a restyle breaks the
 * contract popup.js depends on. It guards four things:
 *
 *   1. ID contract    — every id popup.js binds still exists in popup.html.
 *   2. Class contract  — every class popup.js emits/toggles still has a rule
 *                        in popup.css.
 *   3. Token contract  — popup.css defines the shadcn Neutral tokens (exact
 *                        CONTEXT light hex), --radius:0px, Geist stacks, and
 *                        both the [data-theme="dark"] + prefers-color-scheme
 *                        dark blocks; and ships no external webfont fetch
 *                        (T-20-01-02 — CSP-safe fonts).
 *   4. English-only    — no accented Latin chars leaked into popup.html /
 *                        popup.js (guards against copying French from the
 *                        mockup — CLAUDE.md product-strings rule).
 *
 * Mirrors the assert style of test_settings.mjs.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(__dirname, "..", rel), "utf8");

const popupJs = read("popup.js");
const popupHtml = read("popup.html");
const popupCss = read("popup.css");

// ---------------------------------------------------------------------------
// Frozen contracts. These are the IDs and classes popup.js hard-depends on.
// A restyle may re-skin them but must NOT rename/remove them.
// ---------------------------------------------------------------------------

// Every id popup.js reads/writes (via $() / getElementById / #selectors).
const FROZEN_IDS = [
  "teamSelector",
  "btn-settings",
  "btn-open-librechat",
  "btn-add-to-memory",
  "presenceBadge",
  "presenceCount",
  "connection-card",
  "chat-body",
  "btn-signin-github",
  "btn-connect-xbrain",
  "connect-status",
  "btn-link-github",
  "chat-empty",
  "history-loader",
  "message-list",
  "chat-scroll",
  "composer-input",
  "btn-send",
  "btn-clip",
  "file-picker",
  "clip-overlay",
  "clip-project",
  "clip-preview-mode",
  "clip-preview-detail",
  "clip-status",
  "btn-clip-close",
  "btn-clip-cancel",
  "btn-clip-send",
  "clip-use-defaults",
];

// Every class popup.js emits on nodes it builds, or toggles at runtime, and
// which therefore needs a matching rule in popup.css.
// NOTE: Plan 03 extends this list with the restyled message-thread additions.
const FROZEN_CLASSES = [
  "xb-composer",
  "xb-upload-error",
  "xb-msg",
  "is-self",
  "is-user",
  "is-agent",
  "xb-msg-avatar",
  "xb-msg-meta",
  "xb-msg-author",
  "xb-msg-time",
  "xb-msg-provenance",
  "xb-msg-bubble",
  "xb-msg-caption",
  "xb-msg-thumb",
  "xb-msg-file-chip",
  "connect-btn",
  // Plan 20-03 — message-thread additions (mockup .who/.sources/.src/.savetag/.daysep).
  "xb-msg-text",
  "xb-msg-agent-label",
  "xb-msg-sources",
  "xb-msg-src",
  "xb-msg-chip",
  "xb-msg-savetag",
  "xb-msg-daysep",
];

// shadcn Neutral tokens — exact CONTEXT light-palette hex.
const TOKENS_LIGHT = {
  "--bg": "#FFFFFF",
  "--fg": "#0A0A0A",
  "--card": "#FFFFFF",
  "--muted": "#F5F5F5",
  "--muted-fg": "#737373",
  "--secondary": "#F5F5F5",
  "--primary": "#0A0A0A",
  "--primary-fg": "#FAFAFA",
  "--border": "#E5E5E5",
  "--input": "#E5E5E5",
  "--ring": "#A3A3A3",
  "--destructive": "#E5322D",
};

// ---------------------------------------------------------------------------
// Derive the set of ids popup.js actually references.
// ---------------------------------------------------------------------------

function referencedIds(js) {
  const ids = new Set();
  const patterns = [
    /getElementById\("([^"]+)"\)/g, // document.getElementById("id")
    /\$\("([^"]+)"\)/g, //             $("id") helper
    /["'`]#([A-Za-z][\w-]*)["'`\s]/g, // hardcoded "#id" selectors
  ];
  for (const re of patterns) {
    let m;
    while ((m = re.exec(js)) !== null) ids.add(m[1]);
  }
  return ids;
}

const REFERENCED = referencedIds(popupJs);

// ---------------------------------------------------------------------------
// Test harness (test_settings.mjs style).
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;
function test(name, body) {
  try {
    body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.message}`);
    failed++;
  }
}

// ---- 1. ID contract ----

for (const id of FROZEN_IDS) {
  test(`id contract: #${id} bound by popup.js AND present in popup.html`, () => {
    assert.ok(
      REFERENCED.has(id),
      `popup.js no longer references #${id} — frozen contract drifted`,
    );
    assert.ok(
      popupHtml.includes(`id="${id}"`),
      `popup.html is missing id="${id}" — popup.js selector would break`,
    );
  });
}

// ---- 2. Class contract ----

for (const cls of FROZEN_CLASSES) {
  test(`class contract: .${cls} has a rule in popup.css`, () => {
    assert.ok(
      popupCss.includes(`.${cls}`),
      `popup.css has no rule for .${cls} — popup.js emits/toggles it`,
    );
  });
}

// ---- 3. Token contract ----

for (const [tok, hex] of Object.entries(TOKENS_LIGHT)) {
  test(`token contract: ${tok}:${hex} defined in popup.css`, () => {
    assert.ok(
      popupCss.includes(`${tok}:${hex}`),
      `popup.css must define ${tok}:${hex} (exact CONTEXT light hex)`,
    );
  });
}

test("token contract: --radius:0px (sharp corners)", () => {
  assert.ok(popupCss.includes("--radius:0px"));
});

test("token contract: --sans names 'Geist' first", () => {
  assert.ok(popupCss.includes("--sans:'Geist'"));
});

test("token contract: --mono names 'Geist Mono' first", () => {
  assert.ok(popupCss.includes("--mono:'Geist Mono'"));
});

test("token contract: dark overrides via [data-theme=\"dark\"] + prefers block", () => {
  assert.ok(
    popupCss.includes('[data-theme="dark"]'),
    "missing :root[data-theme=\"dark\"] override",
  );
  assert.ok(
    popupCss.includes("prefers-color-scheme: dark"),
    "missing @media (prefers-color-scheme: dark) block",
  );
});

test("token contract: light override via [data-theme=\"light\"] (toggle wins dark OS)", () => {
  assert.ok(popupCss.includes('[data-theme="light"]'));
});

// ---- 3b. CSP-safe fonts (T-20-01-02) — no external webfont fetch ----

test("font safety: no @font-face / googleapis / gstatic (comments stripped)", () => {
  const cssNoComments = popupCss.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const bad of ["@font-face", "googleapis", "gstatic"]) {
    assert.ok(
      !cssNoComments.toLowerCase().includes(bad),
      `popup.css must not reference ${bad} — CSP-safe, no external font fetch`,
    );
  }
});

// ---- 3c. Truth-level chip spec (Plan 20-03) — monochrome, mockup-exact ----
//
// The mockup ships .chip.validated as a FILLED --primary badge and .chip.working
// as an OUTLINE badge. popup.js selects them via data-level, so the styling must
// hang off the attribute selectors.

test('chip spec: [data-level="validated"] is a filled --primary badge', () => {
  const m = popupCss.match(
    /\.xb-msg-chip\[data-level="validated"\][^{]*\{([^}]*)\}/,
  );
  assert.ok(m, 'popup.css must define .xb-msg-chip[data-level="validated"]');
  assert.ok(
    m[1].includes("var(--primary)") && m[1].includes("var(--primary-fg)"),
    "validated chip must be filled --primary / --primary-fg (mockup .chip.validated)",
  );
});

test('chip spec: [data-level="working"] is an outline badge', () => {
  const m = popupCss.match(
    /\.xb-msg-chip\[data-level="working"\][^{]*\{([^}]*)\}/,
  );
  assert.ok(m, 'popup.css must define .xb-msg-chip[data-level="working"]');
  assert.ok(
    m[1].includes("var(--border)") && m[1].includes("var(--muted-fg)"),
    "working chip must be an outline --border / --muted-fg badge (mockup .chip.working)",
  );
  assert.ok(
    !m[1].includes("background: var(--primary)"),
    "working chip must NOT be filled — outline only",
  );
});

// ---- 3d. XSS guard (T-20-03-01) ----
//
// Message content, agent stream deltas, and (future) source text are untrusted.
// They must reach the DOM via textContent only — never through innerHTML.

test("xss guard: no innerHTML assignment carries message/stream/source data", () => {
  const offenders = [];
  const re = /innerHTML\s*=\s*([^;]+);/g;
  let m;
  while ((m = re.exec(popupJs)) !== null) {
    const rhs = m[1];
    if (/msg\.|\.content|delta|source|src\.|label|textContent/.test(rhs)) {
      offenders.push(rhs.trim().slice(0, 80));
    }
  }
  assert.equal(
    offenders.length,
    0,
    `innerHTML must never carry untrusted data — found: ${JSON.stringify(offenders)}`,
  );
});

// ---- 3e. No fabricated provenance (T-20-03-02) ----
//
// Source rows and truth levels must come from the server. A hardcoded source
// array in popup.js would be invented provenance — a spoofing bug, not a stub.

test("no fabricated data: popup.js hardcodes no source array / truth level", () => {
  assert.ok(
    !/sources\s*=\s*\[\s*\{/.test(popupJs),
    "popup.js must not hardcode a sources array — render only server-sent metadata.sources",
  );
  assert.ok(
    !/data-level["'\s]*[:=]\s*["'](validated|working)["']/.test(popupJs) &&
      !/dataset\.level\s*=\s*["'](validated|working)["']/.test(popupJs),
    "popup.js must not hardcode a truth level — it comes from the source payload",
  );
});

// ---- 4. English-only guard ----

test("english-only: no accented Latin chars in popup.html + popup.js", () => {
  for (const [name, src] of [
    ["popup.html", popupHtml],
    ["popup.js", popupJs],
  ]) {
    const hits = src.match(/[À-ÿ]/g) || [];
    assert.equal(
      hits.length,
      0,
      `${name} has ${hits.length} accented char(s) ${JSON.stringify([
        ...new Set(hits),
      ])} — product strings must be English`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

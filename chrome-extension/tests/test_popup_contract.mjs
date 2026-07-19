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
  // Plan 23-04 — catch-me-up banner + ephemeral summary ids.
  "catchup-banner",
  "catchup-banner-text",
  "btn-catchup-run",
  "btn-catchup-dismiss",
  "catchup-summary",
  "btn-catchup-summary-close",
  // Plan 25-04 — team invite-code overlay ids.
  "btn-invite",
  "invite-panel",
  "btn-invite-close",
  "btn-invite-cancel",
  "btn-invite-mint",
  "invite-code-row",
  "invite-code-output",
  "btn-invite-copy",
  "invite-status",
  "invite-join-code",
  "btn-invite-join",
  "invite-join-status",
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

// ---- 1b. FULL referenced-id existence (Plan 22-03) ----
//
// FROZEN_IDS only guards the pre-existing hard-dependency ids. But EVERY id
// popup.js binds via getElementById / $() / a "#id" selector must also exist in
// popup.html — a missing one makes getElementById return null, and the very next
// `.addEventListener` throws at popup init, breaking the WHOLE popup (not just
// the new control). This asserts every id popup.js references resolves in
// popup.html, so a popup.js↔popup.html id mismatch (e.g. the Plan 22-03
// send-link affordance: #btn-send-link, #sendlink-panel, #sendlink-member,
// #sendlink-url, #btn-sendlink-submit, #sendlink-status, …) goes RED here
// instead of at runtime.

for (const id of [...REFERENCED].sort()) {
  test(`referenced id exists in popup.html: #${id}`, () => {
    assert.ok(
      popupHtml.includes(`id="${id}"`),
      `popup.js binds #${id} but popup.html has no id="${id}" — getElementById("${id}") would return null and break popup init`,
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

// ===========================================================================
// 5. Plan 20-04 — final mechanical gate: accessibility + token RESOLUTION +
//    CSP-safe fonts + radius-0 avatars.
//
// SKIP = FAIL. Every assertion below runs unconditionally against the file
// bytes; there is no environment in which one silently no-ops. The only
// conditional block in this file is the jsdom DOM smoke at the very bottom,
// which is an explicit ENHANCEMENT on top of the gate, not part of it.
// ===========================================================================

const CSS = popupCss.replace(/\/\*[\s\S]*?\*\//g, ""); // comments stripped
const HTML = popupHtml.replace(/<!--[\s\S]*?-->/g, "");

/** Body of the brace block opening at/after `from`. Handles nesting (@media). */
function braceBlock(src, from) {
  const open = src.indexOf("{", from);
  if (open === -1) return null;
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(open + 1, i);
  }
  return null;
}

/** Body of the first rule whose selector is exactly `sel`. */
function selectorBlock(src, sel) {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ +/g, "\\s+");
  const m = new RegExp(`(^|[};])\\s*${esc}\\s*\\{`, "m").exec(src);
  return m ? braceBlock(src, m.index) : null;
}

/** `--token: value` pairs declared directly in a block body. */
function decls(block) {
  const out = {};
  for (const chunk of block.split(";")) {
    const m = /^\s*(--[\w-]+)\s*:\s*(.+?)\s*$/.exec(chunk);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

// ---- 5a. Token RESOLUTION per theme block (exact CONTEXT hex) ----
//
// The existing section 3 proves a token string appears *somewhere*. This proves
// each of the four theme blocks resolves the whole palette to the right value,
// so a drifted dark override cannot hide behind a correct light one.

const TOKENS_DARK = {
  "--bg": "#0A0A0A",
  "--fg": "#FAFAFA",
  "--card": "#171717",
  "--card-fg": "#FAFAFA",
  "--muted": "#262626",
  "--muted-fg": "#A3A3A3",
  "--secondary": "#262626",
  "--primary": "#FAFAFA",
  "--primary-fg": "#171717",
  "--border": "rgba(255,255,255,.10)",
  "--input": "rgba(255,255,255,.15)",
  "--ring": "#787878",
  "--destructive": "#FB5A57",
};
const TOKENS_LIGHT_FULL = { ...TOKENS_LIGHT, "--card-fg": "#0A0A0A" };

const mediaDark = (() => {
  const m = /@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)/.exec(CSS);
  if (!m) return null;
  const body = braceBlock(CSS, m.index);
  return body === null ? null : selectorBlock(body, ":root");
})();

const THEME_BLOCKS = [
  [":root (base / light)", selectorBlock(CSS, ":root"), TOKENS_LIGHT_FULL],
  ['@media (prefers-color-scheme: dark) :root', mediaDark, TOKENS_DARK],
  [':root[data-theme="dark"]', selectorBlock(CSS, ':root[data-theme="dark"]'), TOKENS_DARK],
  [':root[data-theme="light"]', selectorBlock(CSS, ':root[data-theme="light"]'), TOKENS_LIGHT_FULL],
];

for (const [label, block, expected] of THEME_BLOCKS) {
  test(`token resolution: ${label} resolves the full CONTEXT palette`, () => {
    assert.ok(block, `popup.css has no ${label} block — theme would not resolve`);
    const got = decls(block);
    const bad = [];
    for (const [tok, want] of Object.entries(expected)) {
      const have = got[tok];
      if (have === undefined) bad.push(`${tok} MISSING (want ${want})`);
      else if (have.replace(/\s+/g, "") !== want.replace(/\s+/g, "")) {
        bad.push(`${tok}=${have} (want ${want})`);
      }
    }
    assert.equal(bad.length, 0, `${label} palette drift: ${bad.join("; ")}`);
  });
}

// ---- 5b. Accessibility: focus-visible on every interactive control ----
//
// Keyboard users must see where they are. Each control listed here is reachable
// by Tab in the popup; each needs a visible outline, not just a defined rule.

const FOCUSABLE = [
  [".seg button", "theme toggle"],
  [".xb-icon-btn", "header icon buttons"],
  [".xb-send-btn", "send button"],
  [".xb-clip-btn", "clip / attach button"],
  [".xb-msg-file-chip", "file chip"],
  [".xb-team-select", "team selector"],
];

for (const [sel, what] of FOCUSABLE) {
  test(`a11y: ${sel}:focus-visible draws a visible outline (${what})`, () => {
    const body = selectorBlock(CSS, `${sel}:focus-visible`);
    assert.ok(body, `popup.css has no ${sel}:focus-visible rule — ${what} has no keyboard focus state`);
    assert.ok(
      /outline\s*:/.test(body) && !/outline\s*:\s*none/.test(body),
      `${sel}:focus-visible must draw an outline (got: ${body.trim().replace(/\s+/g, " ")})`,
    );
    assert.ok(
      body.includes("var(--ring)"),
      `${sel}:focus-visible must use the --ring token`,
    );
  });
}

// ---- 5c. Accessibility: reduced motion actually suppresses motion ----

test("a11y: @media (prefers-reduced-motion: reduce) suppresses animation", () => {
  const m = /@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)/.exec(CSS);
  assert.ok(m, "popup.css has no @media (prefers-reduced-motion: reduce) block");
  const body = braceBlock(CSS, m.index);
  assert.ok(body && body.trim().length > 0, "reduced-motion block is empty — it suppresses nothing");
  assert.ok(
    /animation\s*:\s*none/.test(body),
    "reduced-motion block must set `animation: none` on the animated elements",
  );
});

// ---- 5d. CSP-safe fonts: zero webfont fetches across css + html ----

test("font safety: zero webfont fetches in popup.css + popup.html", () => {
  const PATTERNS = [
    [/@font-face/gi, "@font-face"],
    [/fonts\.googleapis/gi, "fonts.googleapis"],
    [/fonts\.gstatic/gi, "fonts.gstatic"],
    [/@import\s+url\(/gi, "@import url("],
    [/https?:\/\/[^\s"')]+\.(?:woff2?|ttf|otf|eot)/gi, "remote font file URL"],
  ];
  const hits = [];
  for (const [name, src] of [["popup.css", CSS], ["popup.html", HTML]]) {
    for (const [re, label] of PATTERNS) {
      const found = src.match(re) || [];
      if (found.length) hits.push(`${name}: ${label} x${found.length}`);
    }
  }
  assert.equal(hits.length, 0, `webfont fetch count must be 0 (CSP-safe) — found: ${hits.join("; ")}`);
});

test("font safety: Geist named first with a system fallback (no fetch needed)", () => {
  const root = selectorBlock(CSS, ":root");
  assert.ok(root, "no :root block");
  const d = decls(root);
  assert.ok(/^'Geist'/.test(d["--sans"] || ""), `--sans must name 'Geist' first (got ${d["--sans"]})`);
  assert.ok(
    /system-ui|-apple-system|sans-serif/.test(d["--sans"] || ""),
    "--sans needs a system fallback — Geist is not fetched, so it may be absent",
  );
  assert.ok(
    /^'Geist Mono'/.test(d["--mono"] || ""),
    `--mono must name 'Geist Mono' first (got ${d["--mono"]})`,
  );
  assert.ok(
    /ui-monospace|monospace/.test(d["--mono"] || ""),
    "--mono needs a system monospace fallback",
  );
});

// ---- 5e. Radius 0: avatars are square (the Neutral signal) ----

for (const sel of [".xb-msg-avatar", ".xb-group-avatar"]) {
  test(`radius 0: ${sel} is square (no border-radius: 50%)`, () => {
    const body = selectorBlock(CSS, sel);
    assert.ok(body, `popup.css has no ${sel} rule`);
    const m = /border-radius\s*:\s*([^;]+)/.exec(body);
    assert.ok(m, `${sel} must declare border-radius`);
    assert.ok(
      !/50%/.test(m[1]),
      `${sel} must not be a circle — border-radius: ${m[1].trim()} (Neutral avatars are square)`,
    );
    assert.ok(
      m[1].includes("var(--radius)"),
      `${sel} must use var(--radius) so radius 0 is token-driven (got ${m[1].trim()})`,
    );
  });
}

// ===========================================================================
// 6. Plan 23-04 — catch-me-up ordering (checker BLOCKER).
//
// In switchTeam(), refreshUnreadBanner() MUST run BEFORE the initial markRead():
// the banner count is captured against the STALE, pre-visit read cursor. If
// markRead() advanced the cursor first, unread-summary would always return 0 and
// the banner would never show. This asserts the textual order inside the
// switchTeam body and goes RED if the two calls are ever swapped.
// ===========================================================================

test("catch-me-up ordering: refreshUnreadBanner precedes the initial markRead in switchTeam", () => {
  const m = /async\s+function\s+switchTeam\s*\([^)]*\)\s*\{/.exec(popupJs);
  assert.ok(m, "popup.js has no switchTeam() function");
  const rawBody = braceBlock(popupJs, m.index);
  assert.ok(rawBody, "could not extract the switchTeam() body");
  // Strip comments first — a comment that MENTIONS markRead()/refreshUnreadBanner()
  // must not be mistaken for a call site (the ordering rationale is documented
  // inline right next to the calls).
  const body = rawBody
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
  const iBanner = body.indexOf("refreshUnreadBanner(");
  const iMark = body.indexOf("markRead(");
  assert.ok(iBanner !== -1, "switchTeam must call refreshUnreadBanner()");
  assert.ok(iMark !== -1, "switchTeam must call markRead() after the banner is captured");
  assert.ok(
    iBanner < iMark,
    "refreshUnreadBanner() must textually PRECEDE the initial markRead() in switchTeam — " +
      "otherwise mark-read bumps the read cursor to now() and the banner count is always 0",
  );
});

// ---------------------------------------------------------------------------
// ENHANCEMENT (not part of the gate): richer DOM smoke when jsdom is present.
//
// The gate above is the fs/regex contract and always runs. jsdom is not a repo
// dependency; when it is absent we say so plainly and the gate result stands.
// This block can only ADD failures, never mask them, and never skips a gate
// assertion.
// ---------------------------------------------------------------------------

let jsdomAvailable = false;
try {
  await import("jsdom");
  jsdomAvailable = true;
} catch {
  jsdomAvailable = false;
}

if (jsdomAvailable) {
  const { JSDOM } = await import("jsdom");
  const dom = new JSDOM(popupHtml);
  const doc = dom.window.document;
  const missing = FROZEN_IDS.filter((id) => !doc.getElementById(id));
  if (missing.length) {
    console.error(`  FAIL: dom smoke: ids absent from parsed DOM: ${missing.join(", ")}`);
    failed++;
  } else {
    console.log(`  PASS: dom smoke (jsdom): all ${FROZEN_IDS.length} frozen ids resolve in the parsed DOM`);
  }
} else {
  console.log(
    "  NOTE: jsdom not installed — optional DOM smoke unavailable. " +
      "This is an ENHANCEMENT, not a gate assertion; the fs/regex contract above ran in full.",
  );
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

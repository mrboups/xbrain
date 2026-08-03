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
  // Plan 26-05 — board action id.
  "btn-board",
  // The agent toggle, immediately left of send.
  "btn-agent",
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
  // The marker's two parts. The mark is what you see; the tip is what it
  // reveals — and without a rule for the tip it would render inline, always
  // visible, dumping a document extract into the middle of the thread.
  "xb-savetag-mark",
  "xb-savetag-tip",
  "xb-msg-daysep",
  // Telegram grouping — the name is per-run, the time is per-message. The
  // invisible spacer is the one that must never be dropped as "unused": without
  // a rule for it the reserved width is zero and the timestamp lands on top of
  // the last word.
  "is-run-follower",
  "xb-msg-timespace",
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

/**
 * The markup of the <div> containing `from`, close tag included.
 *
 * Nesting-aware, because "everything up to the next </div>" stops at the first
 * child and would report an empty container as correct.
 */
function divBlock(html, from) {
  if (from === -1) return null;
  const open = html.lastIndexOf("<div", from);
  if (open === -1) return null;
  const re = /<div\b|<\/div>/g;
  re.lastIndex = open;
  let depth = 0;
  let m;
  while ((m = re.exec(html)) !== null) {
    depth += m[0] === "</div>" ? -1 : 1;
    if (depth === 0) return html.slice(open, m.index + m[0].length);
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
  // The indexed-attachment marker is a real control now — it reveals the text
  // the brain holds — so Tab reaches it and Tab must be able to see where it is.
  [".xb-msg-savetag", "indexed-attachment marker"],
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
// 5f. The shell must work in BOTH entry points.
//
// popup.html is the manifest's `default_popup` AND its `side_panel`
// default_path. A side panel is a real full-height viewport; an extension popup
// has no height of its own — Chrome sizes the window to the document. A
// viewport-relative height therefore resolves against nothing in popup mode and
// the flex column collapses to a ~100px strip, which drops .xb-chat-empty
// (position:absolute; inset:0) straight on top of the composer. That shipped.
//
// These assertions are what stop the 100vh from coming back, and stop a second
// owner of the shell box from appearing.
// ===========================================================================

test("shell: html/body are sized in percent, never in viewport units", () => {
  const body = selectorBlock(CSS, "html, body");
  assert.ok(
    body,
    "popup.css must own the html/body box — without any height there, #app's percentage height has nothing to resolve against",
  );
  const d = /height\s*:\s*([^;]+)/.exec(body);
  assert.ok(d, "html/body must declare a height");
  assert.equal(
    d[1].trim(),
    "100%",
    `html/body height is ${d[1].trim()} — a vh unit collapses the popup, because the popup's viewport is sized BY this document`,
  );
});

test("shell: #app fills its context and still has a floor for popup mode", () => {
  const app = selectorBlock(CSS, "#app");
  assert.ok(app, "popup.css must declare #app");
  const height = /(?:^|;)\s*height\s*:\s*([^;]+)/.exec(app);
  assert.ok(height && height[1].trim() === "100%", `#app height must be 100% (got ${height && height[1]})`);
  const minH = /min-height\s*:\s*(\d+)px/.exec(app);
  assert.ok(
    minH,
    "#app needs a min-height, or popup mode has nothing to grow to and collapses",
  );
  const px = Number(minH[1]);
  assert.ok(
    px >= 480 && px <= 600,
    `#app min-height is ${px}px — it must be tall enough to be usable and at or under Chrome's 600px popup cap`,
  );
});

test("shell: no vh in the chain that SIZES the popup window", () => {
  // Scoped to the shell chain on purpose. A vh on html/body/#app is circular in
  // popup mode — the viewport is sized by this document — and that is the bug.
  // A vh elsewhere is fine and sometimes right: .xb-overlay-card caps a modal
  // at the window height, which is a perfectly well-defined number once the
  // window has one.
  const offenders = [];
  for (const sel of ["html, body", "#app"]) {
    const block = selectorBlock(CSS, sel);
    const hits = (block || "").match(/\d+vh/g) || [];
    if (hits.length) offenders.push(`${sel}: ${hits.join(", ")}`);
  }
  const inlineStyle = /<style>([\s\S]*?)<\/style>/.exec(popupHtml);
  assert.ok(inlineStyle, "popup.html has an inline <style> block");
  const inlineHits = inlineStyle[1].match(/\d+vh/g) || [];
  if (inlineHits.length) offenders.push(`popup.html inline: ${inlineHits.join(", ")}`);
  assert.deepEqual(
    offenders,
    [],
    `viewport height in the shell chain: ${offenders.join("; ")} — this is what collapsed popup mode`,
  );
});

test("shell: the html/body box has exactly ONE owner", () => {
  // It used to be declared inline in popup.html, AFTER the stylesheet link — so
  // the shell's height lived in one file and everything depending on it in
  // another, with the inline copy silently winning every edit.
  const inlineStyle = /<style>([\s\S]*?)<\/style>/.exec(popupHtml)[1];
  assert.ok(
    !/(^|[};])\s*html\s*,?\s*body\s*\{/.test(inlineStyle) && !/(^|[};])\s*body\s*\{/.test(inlineStyle),
    "popup.html's inline <style> declares html/body again — popup.css owns the shell box",
  );
});

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

// ===========================================================================
// 7. Plan 26-05 — the board action opens a SERVER-supplied, validated URL and
//    never leaks the board token.
//
// POST /v1/teams/{id}/boards returns `open_url` with a short-lived board token
// in its URL fragment — a bearer secret for that one board (T-26-32/33). These
// assertions pin the security-relevant shape of the openTeamBoard() handler so a
// future edit cannot (a) build the board URL client-side, (b) skip the scheme
// check, or (c) log the URL/token. They read popup.js as text (same mechanism as
// the rest of this file — no DOM harness). Each carries a WHY so a contributor
// who trips it learns the rule instead of deleting the test.
// ===========================================================================

// The openTeamBoard() body — the security-relevant surface for these checks.
const boardHandler = (() => {
  const m = /async\s+function\s+openTeamBoard\s*\([^)]*\)\s*\{/.exec(popupJs);
  return m ? braceBlock(popupJs, m.index) : null;
})();

// popup.js with comments stripped — so an explanatory comment mentioning a
// forbidden token is never a false positive.
const popupJsNoComments = popupJs
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/\/\/[^\n]*/g, "");

test("board action: openTeamBoard() opens the board with chrome.tabs.create", () => {
  assert.ok(boardHandler, "popup.js has no openTeamBoard() handler");
  assert.ok(
    boardHandler.includes("chrome.tabs.create"),
    "openTeamBoard() must open the board with chrome.tabs.create — the board is a tab, not a popup surface",
  );
});

test("board action: the opened URL is server-supplied, never built in the extension", () => {
  // The server owns the board URL (26-02). If popup.js contained a `#t=` fragment
  // or a `/?b=` board-id query, the extension would be minting board links the
  // server never authorised (T-26-33).
  assert.ok(
    !popupJsNoComments.includes("#t="),
    "popup.js must not contain a `#t=` fragment — the board URL (and its token) is built by the server, never the extension",
  );
  assert.ok(
    !popupJsNoComments.includes("/?b="),
    "popup.js must not contain a `/?b=` board-URL template — the extension only opens the open_url the server returns",
  );
});

test("board action: the URL is scheme-validated before it is opened", () => {
  assert.ok(boardHandler, "popup.js has no openTeamBoard() handler");
  assert.ok(
    boardHandler.includes("isSafeHttpUrl(data.open_url)"),
    "openTeamBoard() must validate data.open_url with isSafeHttpUrl before chrome.tabs.create — a non-http(s) scheme (javascript:/data:) must never reach the browser (T-26-31)",
  );
});

test("board action: the board URL and token are never logged from the handler", () => {
  // Scoped to the board handler: the fragment token is a bearer secret, so a
  // console line carrying open_url or xbt_token would persist it in the log
  // (T-26-32). (The pre-existing nudge handler elsewhere logs the static string
  // "open_url handling failed" — a description, not a value — so this is scoped
  // to openTeamBoard() where the board token actually lives.)
  assert.ok(boardHandler, "popup.js has no openTeamBoard() handler");
  assert.ok(
    !/console\.[a-z]+\([^)]*open_url/.test(boardHandler),
    "openTeamBoard() must never log open_url — the board URL fragment is a bearer secret (T-26-32)",
  );
  assert.ok(
    !/console\.[a-z]+\([^)]*xbt_token/.test(boardHandler),
    "openTeamBoard() must never log xbt_token — the caller's bearer token must never be logged (T-26-32)",
  );
});

// ===========================================================================
// 8. The composer does not explain itself.
//
// Typing "@agent" used to raise a line reading "Will summon @agent" under the
// pill. These assertions are about DELETION, not about hiding: a hint element
// that still gets built and then kept `hidden` is the same code with a flag on
// it, and the next edit turns it back on. The builder, the updater, the class
// and the string all have to be absent from the file.
//
// The DETECTION is untouched — the server decides what summons the agent. What
// is gone is the UI narrating the decision back at the person typing.
// ===========================================================================

test("composer: the mention hint is deleted, not hidden", () => {
  // Comments stripped: prose ABOUT the removed affordance is documentation, and
  // the rule here is that no CODE builds, updates, styles or prints it.
  const gone = [
    ["createMentionHint()", /createMentionHint/],
    ["updateMentionHint()", /updateMentionHint/],
    ["the .xb-mention-hint node", /xb-mention-hint/],
    ['the "Will summon" copy', /[Ww]ill summon/],
  ];
  for (const [what, re] of gone) {
    assert.ok(
      !re.test(popupJsNoComments),
      `popup.js still builds ${what} — the hint must be removed, not switched off`,
    );
  }
  assert.ok(
    !/xb-mention-hint/.test(popupCss),
    "popup.css still styles .xb-mention-hint — the rule outlived the element",
  );
  assert.ok(
    !/[Ww]ill summon/.test(popupHtml.replace(/<!--[\s\S]*?-->/g, "")),
    "popup.html still ships the hint's copy",
  );
});

// ===========================================================================
// 9. The agent toggle.
//
// It sits immediately LEFT of send, it shows whether it is armed, and — the
// property worth a gate — it is not a SECOND way to summon the agent. It writes
// the same @agent mention a person would type and lets the server's detector
// decide, so there is one authority. A parallel flag on the request would be a
// second one, and the two would disagree the first time either changed.
// ===========================================================================

test("agent toggle: #btn-agent sits immediately left of #btn-send in the pill", () => {
  // Read the buttons the composer pill actually contains, in order. Comparing
  // the LAST TWO is what pins "immediately left" — a later control dropped in
  // between the two would go red here rather than at a glance in the popup.
  const region = divBlock(HTML, HTML.indexOf('class="xb-composer-pill"'));
  assert.ok(region, "popup.html has no .xb-composer-pill");
  const ids = [...region.matchAll(/<button[^>]*id="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(ids.includes("btn-agent"), "the composer pill has no #btn-agent");
  assert.deepEqual(
    ids.slice(-2),
    ["btn-agent", "btn-send"],
    `the toggle must be the control immediately left of send (pill order: ${ids.join(" -> ")})`,
  );
});

test("agent toggle: it is a real toggle, with a state both readers can see", () => {
  const tag = /<button[^>]*id="btn-agent"[^>]*>/.exec(popupHtml);
  assert.ok(tag, "popup.html has no #btn-agent button");
  assert.match(tag[0], /type="button"/);
  assert.match(
    tag[0],
    /aria-pressed="false"/,
    "a toggle announces itself with aria-pressed — a plain button reads as an action",
  );
  assert.match(
    tag[0],
    /data-state="off"/,
    "the stylesheet keys the selected look off [data-state]; it must start defined",
  );
  assert.match(
    tag[0],
    /class="[^"]*\bxb-icon-btn\b/,
    "the toggle belongs to the .xb-icon-btn family (shadcn Neutral, radius 0, 15px svg)",
  );
});

test("agent toggle: popup.js writes BOTH state attributes, never one", () => {
  // A control that looks armed and reads unarmed is worse than one that does
  // neither, and it is exactly what writing only data-state produces.
  const m = /function\s+setAgentArmed\s*\([^)]*\)\s*\{/.exec(popupJs);
  assert.ok(m, "popup.js has no setAgentArmed()");
  const body = braceBlock(popupJs, m.index);
  assert.match(body, /dataset\.state\s*=/, "setAgentArmed must write [data-state]");
  assert.match(body, /aria-pressed/, "setAgentArmed must write [aria-pressed]");
});

test("agent toggle: the selected state is visible, not just announced", () => {
  const on = selectorBlock(CSS, '.xb-agent-btn[data-state="on"]');
  assert.ok(on, 'popup.css has no .xb-agent-btn[data-state="on"] rule');
  assert.ok(
    /background:\s*var\(--primary\)/.test(on),
    "armed must be unmistakable at a glance — the filled --primary the send button wears",
  );
});

test("agent toggle: ONE summon mechanism — the mention, never a parallel flag", () => {
  const m = /async\s+function\s+sendMessage\s*\([^)]*\)\s*\{/.exec(popupJs);
  assert.ok(m, "popup.js has no sendMessage()");
  const body = braceBlock(popupJs, m.index)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
  assert.ok(
    body.includes("withAgentMention("),
    "the toggle must compose the outgoing TEXT — that is what the server's detector reads",
  );
  for (const flag of ["summon", "to_agent", "toAgent", "is_agent", "mention_agent"]) {
    assert.ok(
      !body.includes(flag),
      `sendMessage sends a "${flag}" field — a second summon authority that can disagree with the mention`,
    );
  }
});

test("agent toggle: a successful send disarms it, and a failed one does not", () => {
  // Stated behaviour, pinned: this is a shared team chat, so an armed toggle
  // nobody noticed would send the next line meant for teammates to the agent.
  // Only the success path clears it, so a failed send keeps the draft AND the
  // intent that went with it.
  const m = /async\s+function\s+sendMessage\s*\([^)]*\)\s*\{/.exec(popupJs);
  const body = braceBlock(popupJs, m.index);
  const iTry = body.indexOf("try {");
  const iCatch = body.indexOf("} catch");
  const iReset = body.indexOf("setAgentArmed(false)");
  assert.ok(iReset !== -1, "sendMessage never disarms the toggle");
  assert.ok(
    iReset > iTry && iReset < iCatch,
    "the reset must sit on the success path — not in catch, and not in finally",
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

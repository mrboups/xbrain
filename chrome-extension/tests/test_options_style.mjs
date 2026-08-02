/**
 * The extension's Settings page runs on the SAME shadcn Neutral tokens as the
 * popup (chrome-extension/options.html + options.js).
 *
 * WHY THIS FILE EXISTS AT ALL. This page was the last surface on a palette of
 * its own: hardcoded dark, a blue accent, 8px corners, and no --primary /
 * --border / --ring defined anywhere. An earlier pass skipped it for a real
 * reason — porting the token NAMES onto rules written for the old variables
 * renders controls that exist and cannot be seen, and a white-on-white button
 * is invisible in a screenshot too. So the assertions below are not about
 * taste:
 *
 *   - every colour must resolve through a token, or the next theme change
 *     misses it and leaves one control behind;
 *   - a rule that FILLS with --primary must take its text from --primary-fg.
 *     --primary is near-black in light and near-white in dark; a literal white
 *     label is legible in exactly one of the two;
 *   - the token VALUES must equal popup.css's, or the two surfaces are two
 *     products;
 *   - and the page must honour the same stored theme the popup reads, in the
 *     same storage area, or they disagree about which theme is on.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(__dirname, "..", rel), "utf8");

const optionsHtml = read("options.html");
const optionsJs = read("options.js");
const popupCss = read("popup.css");

/** The stylesheet, comments out. */
const style = (() => {
  const m = /<style>([\s\S]*?)<\/style>/.exec(optionsHtml);
  assert.ok(m, "options.html must carry its stylesheet inline");
  return m[1].replace(/\/\*[\s\S]*?\*\//g, "");
})();
const POPUP_CSS = popupCss.replace(/\/\*[\s\S]*?\*\//g, "");

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

/** Custom-property declarations in a block, as a map. */
function tokens(block) {
  const out = {};
  for (const m of (block || "").matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    out[m[1]] = m[2].replace(/\s+/g, "").trim();
  }
  return out;
}

/** Every `selector { ... }` pair in a stylesheet, at-rules flattened away. */
function rules(src) {
  const out = [];
  const re = /([^{}]+)\{/g;
  let m;
  while ((m = re.exec(src))) {
    const selector = m[1].trim().replace(/\s+/g, " ");
    if (selector.startsWith("@")) continue; // the at-rule wrapper, not a rule
    const body = braceBlock(src, m.index);
    if (body === null) continue;
    if (/\{/.test(body)) continue; // a nested container, handled by its children
    out.push({ selector, body });
  }
  return out;
}

/** The shadcn keys both surfaces must agree on, value for value. */
const SHARED_TOKENS = [
  "--bg",
  "--fg",
  "--card",
  "--card-fg",
  "--muted",
  "--muted-fg",
  "--secondary",
  "--secondary-fg",
  "--primary",
  "--primary-fg",
  "--border",
  "--input",
  "--ring",
  "--destructive",
  "--radius",
];

// ---- 1. The palette is defined, and it is the popup's --------------------

test("options.html defines the shadcn Neutral tokens, radius 0", () => {
  const root = tokens(selectorBlock(style, ":root"));
  for (const key of SHARED_TOKENS) {
    assert.ok(root[key], `options.html defines no ${key} — a rule using it resolves to nothing, which is an invisible control`);
  }
  assert.equal(root["--radius"], "0px", "the sharp corner is the Neutral signal");
});

test("its light values are popup.css's, token for token", () => {
  const mine = tokens(selectorBlock(style, ":root"));
  const theirs = tokens(selectorBlock(POPUP_CSS, ":root"));
  const drift = SHARED_TOKENS.filter((k) => mine[k] !== theirs[k]).map(
    (k) => `${k}: options=${mine[k]} popup=${theirs[k]}`,
  );
  assert.equal(drift.length, 0, `the two surfaces drifted: ${drift.join("; ")}`);
});

test("its dark values are popup.css's too", () => {
  const mine = tokens(selectorBlock(style, ':root[data-theme="dark"]'));
  const theirs = tokens(selectorBlock(POPUP_CSS, ':root[data-theme="dark"]'));
  assert.ok(Object.keys(mine).length > 5, "options.html must carry a dark block");
  const drift = Object.keys(theirs)
    .filter((k) => SHARED_TOKENS.includes(k))
    .filter((k) => mine[k] !== theirs[k])
    .map((k) => `${k}: options=${mine[k]} popup=${theirs[k]}`);
  assert.equal(drift.length, 0, `the dark palettes drifted: ${drift.join("; ")}`);
});

test("all three theme blocks are present, so the toggle wins both ways", () => {
  assert.match(
    style,
    /@media\s*\(prefers-color-scheme:\s*dark\)/,
    "without the media block a dark OS gets the light page until somebody touches the switch",
  );
  assert.ok(
    selectorBlock(style, ':root[data-theme="dark"]'),
    "an explicit dark choice must out-specify the media query",
  );
  assert.ok(
    selectorBlock(style, ':root[data-theme="light"]'),
    "and an explicit LIGHT choice must beat a dark OS — without this block the toggle only works in one direction",
  );
});

test("the page is no longer pinned to dark", () => {
  const meta = /<meta[^>]+name="color-scheme"[^>]*>/.exec(optionsHtml);
  assert.ok(meta, "options.html must declare a color-scheme meta");
  assert.match(
    meta[0],
    /content="light dark"/,
    "pinned to dark, the browser renders form controls and scrollbars dark on a light page",
  );
  const root = selectorBlock(style, ":root");
  assert.match(root, /color-scheme:\s*light/, ":root is the light theme now");
  assert.match(
    selectorBlock(style, ':root[data-theme="dark"]'),
    /color-scheme:\s*dark/,
    "the dark block must flip the native controls too",
  );
});

// ---- 2. Nothing was left behind ------------------------------------------

test("the old --xb-* palette is gone from both files", () => {
  // Not merely unused: still DEFINED, it is a second palette waiting to be
  // referenced again by the next edit.
  for (const [name, src] of [["options.html", optionsHtml], ["options.js", optionsJs]]) {
    const hits = [...new Set(src.match(/--xb-[a-z0-9-]+/g) || [])];
    assert.deepEqual(hits, [], `${name} still names ${JSON.stringify(hits)}`);
  }
});

test("no rule carries a literal colour — every one resolves through a token", () => {
  const offenders = [];
  let examined = 0;
  for (const { selector, body } of rules(style)) {
    // The token blocks are where literals belong; everything else must not.
    if (/^:root/.test(selector)) continue;
    for (const m of body.matchAll(/(color|background|background-color|border|border-color|outline|box-shadow)\s*:\s*([^;]+)/g)) {
      examined++;
      const value = m[2];
      if (/#[0-9a-f]{3,8}\b|rgba?\(/i.test(value) || /\b(white|black)\b/i.test(value)) {
        offenders.push(`${selector} { ${m[1]}: ${value.trim()} }`);
      }
    }
  }
  // A parser that quietly matched nothing would report a clean page forever.
  assert.ok(examined > 50, `only ${examined} colour declarations were examined — this check has gone inert`);
  assert.deepEqual(
    offenders,
    [],
    `a literal colour here is a control the next theme change forgets:\n  ${offenders.join("\n  ")}`,
  );
});

test("options.js emits no literal colour either", () => {
  const inline = [...optionsJs.matchAll(/(?:color|background)\s*:\s*([^;"'`]+)/g)].map((m) => m[1]);
  const literals = inline.filter((v) => /#[0-9a-f]{3,8}\b|rgba?\(/i.test(v));
  assert.deepEqual(literals, [], `options.js hardcodes ${JSON.stringify(literals)}`);
  assert.ok(inline.length > 0, "this check is inert if options.js styles nothing inline");
});

// ---- 3. THE INVISIBLE-CONTROL CHECK --------------------------------------

test("anything FILLED with --primary takes its text from --primary-fg", () => {
  // The exact failure this port had to avoid: --primary is near-black in light
  // and near-white in dark, so a filled control with a fixed label colour is
  // legible in one theme and invisible in the other.
  const offenders = [];
  for (const { selector, body } of rules(style)) {
    if (/^:root/.test(selector)) continue;
    const fills = /background(?:-color)?\s*:\s*var\(--primary\)/.test(body);
    if (!fills) continue;
    if (!/color\s*:\s*var\(--primary-fg\)/.test(body)) offenders.push(selector);
  }
  assert.deepEqual(
    offenders,
    [],
    `filled with --primary and no --primary-fg label: ${offenders.join(", ")}`,
  );
});

test("the checked truth-level pill does not hide its own radio", () => {
  // It fills with --primary, and accent-color: var(--primary) would draw the
  // radio in the pill's own colour — a control that is there and cannot be seen.
  const checked = selectorBlock(style, '.opt-radio-row label:has(input:checked) input[type="radio"]');
  assert.ok(checked, "the radio inside a checked pill needs its own accent-color");
  assert.match(checked, /accent-color:\s*var\(--primary-fg\)/);
});

test("every interactive control says where the keyboard is", () => {
  // A monochrome page loses the browser's default blue ring, so each control
  // has to draw its own or focus becomes untrackable.
  for (const sel of [
    ".seg button:focus-visible",
    ".xb-btn-primary-options:focus-visible",
    ".team-invite-btn:focus-visible",
    ".team-leave-btn:focus-visible",
    ".org-block-btn:focus-visible",
    ".team-member-remove:focus-visible",
  ]) {
    const block = selectorBlock(style, sel);
    assert.ok(block, `options.html has no ${sel} rule`);
    assert.match(block, /outline:\s*2px solid var\(--ring\)/, `${sel} must use the ring token`);
  }
});

test("radius 0 everywhere except the one round thing that means something", () => {
  const offenders = [];
  for (const { selector, body } of rules(style)) {
    if (/^:root/.test(selector)) continue;
    for (const m of body.matchAll(/border-radius\s*:\s*([^;]+)/g)) {
      const value = m[1].trim();
      if (value === "var(--radius)") continue;
      // The presence dot is a dot: roundness is what makes it read as a status
      // light rather than a chip. popup.css draws its own the same way.
      if (selector === ".session-dot" && /999px/.test(value)) continue;
      offenders.push(`${selector} { border-radius: ${value} }`);
    }
  }
  assert.deepEqual(offenders, [], `radius must be token-driven: ${offenders.join("; ")}`);
});

// ---- 4. One theme, one key, one storage area -----------------------------

test("the page reads the theme the popup wrote, from the same area", () => {
  assert.match(
    optionsJs,
    /chrome\.storage\.local\.get\(\[THEME_STORAGE_KEY\]\)/,
    "the popup stores the theme in chrome.storage.local — reading sync here would let the two disagree",
  );
  assert.match(
    optionsJs,
    /chrome\.storage\.local\.set\(\{ \[THEME_STORAGE_KEY\]: mode \}\)/,
    "and it must write back to the same area",
  );
  assert.match(
    optionsJs,
    /applyTheme\(document\.documentElement, mode\)/,
    "data-theme must land on the root, which is what the three blocks key off",
  );
  assert.ok(
    optionsJs.includes('from "./chat_core/theme.js"'),
    "the resolve/apply pair is shared; a second copy would drift",
  );
});

test("english-only, and no emoji left to fight the palette", () => {
  const hits = optionsHtml.match(/[À-ÿ]/g) || [];
  assert.equal(hits.length, 0, `options.html has ${JSON.stringify([...new Set(hits)])}`);
  // An emoji renders in its own colour and is the one thing on the page a theme
  // cannot touch. The page title carried one; the refresh button carried one.
  for (const glyph of ["\u{1F9E0}", "\u{1F504}"]) {
    assert.ok(
      !optionsHtml.includes(glyph),
      `options.html still carries ${glyph} — it renders in its own colour whatever the theme`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

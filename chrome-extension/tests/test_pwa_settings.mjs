/**
 * The PWA's settings surface: a full-screen sheet, not a strip under the header
 * (app-site/app/index.html + app.css + app.js).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/.
 *
 * WHAT THIS GUARDS. A panel that covers half a phone screen is neither a page
 * nor a dialog: it hides the newest messages, it cannot hold a block that grows,
 * and it has no obvious way out. Turning it into a sheet introduces four things
 * that fail silently when they are wrong, and each has an assertion below:
 *
 *   - a sheet sized against the full viewport puts its OWN header off screen the
 *     moment the keyboard opens under it — the same bug the chat header had, one
 *     surface further in;
 *   - a sheet with no visible close control is dismissible only by Escape, which
 *     a touch device does not have;
 *   - focus that never enters the sheet, or never comes back, strands a keyboard
 *     user at the top of the document;
 *   - a sheet whose scroll chains to the page moves the thread behind it.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
const appJs = readFileSync(join(APP_DIR, "app.js"), "utf8");
const css = readFileSync(join(APP_DIR, "app.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);
const HTML = html.replace(/<!--[\s\S]*?-->/g, "");

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

/** Body of the brace block opening at/after `from`. Handles nesting. */
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

/** `prop: value` pairs declared directly in a block body. */
function props(block) {
  const out = {};
  for (const chunk of (block || "").split(";")) {
    const m = /^\s*([a-z-]+)\s*:\s*(.+?)\s*$/is.exec(chunk);
    if (m) out[m[1].trim()] = m[2].replace(/\s+/g, " ").trim();
  }
  return out;
}

/** The whole of a top-level function declaration in app.js. */
function functionBody(src, name) {
  const m = new RegExp(`function ${name}\\(\\)\\s*\\{`).exec(src);
  assert.ok(m, `app.js must declare ${name}()`);
  return braceBlock(src, m.index);
}

// ---- 1. It left the app column ------------------------------------------

test("the sheet is a sibling of the app column, not a row inside it", () => {
  // Inside #app it would still be squeezed between the header and the thread,
  // and it would inherit the column's max-width instead of taking the screen.
  const lastInApp = HTML.indexOf('id="account-identity"');
  const settingsAt = HTML.indexOf('id="settings-panel"');
  assert.ok(lastInApp !== -1 && settingsAt !== -1);
  assert.ok(
    settingsAt > lastInApp,
    "#settings-panel must sit outside #app, beside the other overlays",
  );
  assert.ok(
    !/class="xb-settings-panel"/.test(html),
    "the old strip class must be gone from the markup, not merely restyled",
  );
  assert.ok(
    !/\.xb-settings-panel\s*\{/.test(css),
    "app.css still carries the .xb-settings-panel rule — a rule for markup that no longer exists is dead weight nobody dares delete later",
  );
});

test("it announces itself as a dialog, with a name", () => {
  const open = /<div[^>]*id="settings-panel"[^>]*>/.exec(HTML);
  assert.ok(open, "index.html must declare #settings-panel");
  assert.match(open[0], /role="dialog"/);
  assert.match(open[0], /aria-modal="true"/);
  const labelled = /aria-labelledby="([^"]+)"/.exec(open[0]);
  assert.ok(labelled, "a dialog with no accessible name is announced as nothing");
  assert.ok(
    HTML.includes(`id="${labelled[1]}"`),
    `aria-labelledby points at #${labelled[1]}, which does not exist`,
  );
  assert.match(open[0], /\bhidden\b/, "it must start closed on every launch");
});

// ---- 2. Full screen, keyboard-aware, safe-area-aware ---------------------

test("the overlay covers the screen and outranks the other overlays", () => {
  const overlay = props(selectorBlock(css, ".xb-settings-overlay"));
  assert.equal(overlay.position, "fixed", "a full-screen sheet cannot be in flow");
  assert.equal(overlay.inset, "0");
  const other = props(selectorBlock(css, ".xb-overlay"));
  assert.ok(
    Number(overlay["z-index"]) > Number(other["z-index"]),
    `settings (${overlay["z-index"]}) must sit above the members/invite overlays (${other["z-index"]})`,
  );
});

test("the sheet is sized by the MEASURED viewport, like the shell", () => {
  const sheet = props(selectorBlock(css, ".xb-settings-sheet"));
  for (const key of ["height", "max-height"]) {
    assert.match(
      sheet[key] || "",
      /var\(--xb-viewport-height, 100dvh\)/,
      `.xb-settings-sheet ${key} is ${sheet[key]} — sized against the full viewport, this sheet's own header leaves the screen the moment the keyboard opens under the name field`,
    );
  }
  assert.equal(
    sheet["max-width"],
    "720px",
    "the same cap as the app column, so nothing jumps sideways on a wide window",
  );
  assert.equal(sheet.overflow, "hidden", "the body below is the only scroller");
});

test("top and bottom safe areas are respected", () => {
  const head = props(selectorBlock(css, ".xb-settings-sheet-head"));
  assert.match(
    head["padding-top"] || "",
    /env\(safe-area-inset-top\)/,
    "the sheet header would sit under the notch",
  );
  assert.equal(head["flex-shrink"], "0", "the header must not be squeezed by a long body");
  const body = props(selectorBlock(css, ".xb-settings-body"));
  assert.match(
    body["padding-bottom"] || "",
    /env\(safe-area-inset-bottom\)/,
    "sign out would land under the home indicator",
  );
});

test("the sheet scrolls itself, and the chat behind it does not move", () => {
  const body = props(selectorBlock(css, ".xb-settings-body"));
  assert.equal(body["overflow-y"], "auto");
  assert.equal(
    body["overscroll-behavior"],
    "contain",
    "without this, reaching the end of the settings list scrolls the thread behind it",
  );
  assert.equal(body["min-height"], "0", "a flex child needs this or it refuses to shrink and scroll");
});

// ---- 3. Getting out ------------------------------------------------------

test("there is a visible close control, and it has a name", () => {
  const btn = /<button[^>]*id="btn-settings-close"[\s\S]*?<\/button>/.exec(HTML);
  assert.ok(btn, "a sheet dismissible only by Escape is a trap on a touch device");
  assert.match(btn[0], /aria-label="[^"]+"/, "icon-only, so it needs an accessible name");
  assert.ok(btn[0].includes("<svg"), "it must carry a glyph, not a word");
  assert.ok(
    btn[0].includes('class="xb-icon-btn"'),
    "the shared icon-button class is what gives the SVG its 15px size",
  );
  const head = /<div[^>]*class="xb-settings-sheet-head"[\s\S]*?<\/div>/.exec(HTML);
  assert.ok(head && head[0].includes('id="btn-settings-close"'), "it belongs in the sheet header");
});

test("open and close move focus, and close hands it back to the opener", () => {
  const wire = functionBody(appJs, "wireSettingsPanel");
  assert.match(wire, /panel\.hidden = false/, "open must reveal the sheet");
  assert.match(wire, /panel\.hidden = true/, "close must hide it");
  assert.match(
    wire,
    /first\.focus\(\)/,
    "focus must move INTO the sheet, or a keyboard user is still out in the header",
  );
  assert.match(
    wire,
    /btn\.focus\(\)/,
    "and back to the control that opened it — otherwise closing drops the caret at the top of the document",
  );
  assert.ok(
    wire.includes('el("btn-settings-close")'),
    "the close button is what takes focus on open: focusing the NAME field would raise the keyboard before anybody asked to type",
  );
  assert.ok(
    !/profile-name/.test(wire),
    "focus on open must not land in a text field",
  );
});

test("Escape closes it, and so does the scrim beside the sheet", () => {
  const wire = functionBody(appJs, "wireSettingsPanel");
  assert.match(wire, /event\.key !== "Escape"/, "Escape must close the dialog");
  assert.match(
    wire,
    /event\.target === panel/,
    "a click on the overlay itself landed beside the sheet — that is 'outside'",
  );
  assert.ok(
    /aria-expanded", "true"/.test(wire) && /aria-expanded", "false"/.test(wire),
    "the header button must report the sheet's state in both directions",
  );
});

test("nothing in the shell scrolls the page — closing cannot jump the thread", () => {
  // The classic overlay bug: lock the body by scrolling it, then restore to 0
  // on close and lose the reader's place. This shell never scrolls at all.
  assert.ok(
    !/scrollTo|scrollTop/.test(appJs),
    "app.js must not touch the page scroll; the thread's position belongs to #chat-scroll",
  );
});

// ---- 4. What is in it, and in what order --------------------------------

test("the account block is first and sign out is last", () => {
  const sheet = /<div[^>]*class="xb-settings-body"[\s\S]*?\n      <\/div>/.exec(HTML);
  assert.ok(sheet, "the sheet must have a .xb-settings-body");
  const body = sheet[0];
  const order = ["settings-profile", "btn-theme-light", "settings-push-state", "btn-sign-out"];
  let previous = -1;
  for (const id of order) {
    const at = body.indexOf(`id="${id}"`);
    assert.ok(at !== -1, `#${id} must live in the settings sheet`);
    assert.ok(at > previous, `#${id} is out of order — the account comes first, sign out last`);
    previous = at;
  }
});

test("the theme switch is still a labelled row, not a bare pair of glyphs", () => {
  const head = /<div[^>]*class="xb-settings-head"[\s\S]*?<\/div>\s*<\/div>/.exec(HTML);
  assert.ok(head, "the display row must keep its .xb-settings-head container");
  assert.ok(head[0].includes('class="seg"'), "the switch belongs in that row");
  assert.match(
    head[0],
    /class="xb-settings-title">[^<]+</,
    "the row needs a label — two icons alone say nothing about what they change",
  );
  const p = props(selectorBlock(css, ".xb-settings-head"));
  assert.equal(p["justify-content"], "space-between", "label left, switch right");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

/**
 * The PWA's header, settings surface and composer must read as the SAME product
 * as the extension popup.
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/ and ../popup.css and
 * compares the two.
 *
 * WHY THIS IS A GATE AND NOT A REVIEW NOTE: every rule here is a rule somebody
 * already broke once. Inline SVGs carry a viewBox and no intrinsic size, so a
 * missing width/height renders them at ~300x150 and destroys the header — that
 * happened, and the 15px rule is the fix. A team rail whose CSS was copied and
 * then edited on one side looks fine in isolation and wrong side by side. And a
 * settings panel that ships an id nothing binds, or binds an id nothing ships,
 * is a control that silently does nothing.
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
const css = readFileSync(join(APP_DIR, "app.css"), "utf8");
const appJs = readFileSync(join(APP_DIR, "app.js"), "utf8");
const chatJs = readFileSync(join(APP_DIR, "chat.js"), "utf8");
const popupCss = readFileSync(join(__dirname, "..", "popup.css"), "utf8");

const CSS = css.replace(/\/\*[\s\S]*?\*\//g, "");
const POPUP_CSS = popupCss.replace(/\/\*[\s\S]*?\*\//g, "");
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

/** `prop: value` pairs declared directly in a block body. */
function props(block) {
  const out = {};
  for (const chunk of (block || "").split(";")) {
    const m = /^\s*([a-z-]+)\s*:\s*(.+?)\s*$/is.exec(chunk);
    if (m) out[m[1].trim()] = m[2].replace(/\s+/g, " ").trim();
  }
  return out;
}

// ---- 1. No brand: the teams ARE the identity of this screen ---------------

test("the header opens with the team rail, not a product name", () => {
  assert.ok(
    !/xb-brand/.test(html),
    "the brand block was removed from the header — the owner wants the team icons at the top-left with no title",
  );
  assert.ok(
    !/xb-brand/.test(css),
    "app.css still styles .xb-brand / .xb-brand-mark — a rule for markup that no longer exists is dead weight nobody dares delete later",
  );
  assert.ok(
    HTML.includes('id="team-rail"'),
    "index.html must declare the rail container",
  );
  const header = /<header[^>]*class="xb-header"[^>]*>([\s\S]*?)<\/header>/.exec(HTML);
  assert.ok(header, "index.html must have a .xb-header");
  assert.ok(
    header[1].indexOf('id="team-rail"') < header[1].indexOf('class="xb-header-right"'),
    "the rail must come before the actions — left group, right group, nothing between",
  );
});

test("the <select> team picker is gone, not merely hidden", () => {
  // A hidden select that something still writes to is a second source of truth
  // for the active team; that is precisely the drift D-27-04 exists to stop.
  assert.ok(
    !/id="team-selector"/.test(html),
    "index.html still declares #team-selector",
  );
  for (const [name, src] of [["app.js", appJs], ["chat.js", chatJs]]) {
    assert.ok(
      !src.includes("team-selector"),
      `${name} still binds #team-selector — the rail is the only team control now`,
    );
  }
});

// ---- 2. The rail is styled identically on both surfaces -------------------

/**
 * Properties that MUST match popup.css exactly. Layout-only: the properties
 * left out are the ones that legitimately differ (the popup is a fixed 320px
 * panel with a max-width on the rail; a phone is not).
 */
const RAIL_MATCH = {
  ".xb-team-icon": [
    "width",
    "height",
    "border-radius",
    "border",
    "background",
    "color",
    "font-size",
    "font-weight",
    "padding",
  ],
  ".xb-team-badge": [
    "top",
    "right",
    "min-width",
    "height",
    "padding",
    "border-radius",
    "background",
    "color",
    "font-size",
    "font-weight",
    "line-height",
  ],
  ".xb-icon-btn": ["width", "height", "background", "border", "border-radius", "padding", "color"],
};

for (const [sel, keys] of Object.entries(RAIL_MATCH)) {
  test(`${sel} matches chrome-extension/popup.css value for value`, () => {
    const mine = selectorBlock(CSS, sel);
    const theirs = selectorBlock(POPUP_CSS, sel);
    assert.ok(mine, `app.css has no ${sel} rule`);
    assert.ok(theirs, `popup.css has no ${sel} rule — the reference is gone`);
    const a = props(mine);
    const b = props(theirs);
    const drift = [];
    for (const key of keys) {
      if (a[key] !== b[key]) drift.push(`${key}: app.css=${a[key]} popup.css=${b[key]}`);
    }
    assert.equal(drift.length, 0, `${sel} drifted: ${drift.join("; ")}`);
  });
}

test("the active team square is the filled one, on both surfaces", () => {
  for (const [name, src] of [["app.css", CSS], ["popup.css", POPUP_CSS]]) {
    const block = selectorBlock(src, '.xb-team-icon[aria-selected="true"]');
    assert.ok(block, `${name} must style the selected square`);
    assert.ok(
      block.includes("var(--primary)") && block.includes("var(--primary-fg)"),
      `${name}: the active team must fill with --primary`,
    );
  }
});

test("a square being dragged is visibly picked up", () => {
  // chat-core toggles .is-dragging; without a rule the drag has no feedback at
  // all and reads as a broken control.
  assert.ok(selectorBlock(CSS, ".xb-team-icon.is-dragging"), "app.css has no .is-dragging rule");
  assert.ok(
    selectorBlock(POPUP_CSS, ".xb-team-icon.is-dragging"),
    "popup.css has no .is-dragging rule",
  );
});

// ---- 3. The icon-sizing trap ---------------------------------------------

test("every inline SVG is given an explicit size", () => {
  // THE TRAP: a viewBox is not a size. Without width/height these render at the
  // UA default (~300x150) and blow the header apart. This already happened once.
  for (const sel of [".xb-icon-btn svg", ".seg button svg"]) {
    const block = selectorBlock(CSS, sel);
    assert.ok(block, `app.css has no ${sel} rule — its icons would render enormous`);
    const p = props(block);
    assert.equal(p.width, "15px", `${sel} width must be 15px (the shadcn scale)`);
    assert.equal(p.height, "15px", `${sel} height must be 15px`);
  }
});

test("the header ships icons, not text labels", () => {
  const header = /<header[^>]*class="xb-header"[^>]*>([\s\S]*?)<\/header>/.exec(HTML)[1];
  for (const id of ["btn-people", "btn-invite", "btn-enable-push", "btn-settings"]) {
    const btn = new RegExp(`<button[^>]*id="${id}"[\\s\\S]*?</button>`).exec(header);
    assert.ok(btn, `the header must carry #${id}`);
    assert.ok(
      btn[0].includes('class="xb-icon-btn"'),
      `#${id} must be an .xb-icon-btn`,
    );
    assert.ok(btn[0].includes("<svg"), `#${id} must carry an inline SVG`);
    // Text between the tags would sit next to the icon and wrap the header.
    const text = btn[0].replace(/<[^>]*>/g, "").trim();
    assert.equal(text, "", `#${id} still carries the text "${text}" — icon only`);
    assert.ok(
      /aria-label="/.test(btn[0]),
      `#${id} is icon-only, so it needs an aria-label or it has no accessible name`,
    );
  }
});

// ---- 4. The bell tells the truth -----------------------------------------

test("both bell glyphs ship, and CSS picks one off data-state", () => {
  const btn = /<button[^>]*id="btn-enable-push"[\s\S]*?<\/button>/.exec(HTML)[0];
  assert.ok(btn.includes("xb-ico-bell"), "the ringing bell must be in the markup");
  assert.ok(btn.includes("xb-ico-bell-off"), "the struck bell must be in the markup");
  // Shipping both and toggling with CSS is what lets push.js paint the state
  // without ever assigning markup.
  assert.ok(
    /#btn-enable-push\[data-state="on"\]\s+\.xb-ico-bell\s*\{[^}]*display:\s*block/.test(CSS),
    "app.css must show the ringing bell only when data-state is on",
  );
  assert.ok(
    /#btn-enable-push\[data-state="on"\]\s+\.xb-ico-bell-off\s*\{[^}]*display:\s*none/.test(CSS),
    "app.css must hide the struck bell when data-state is on",
  );
  assert.ok(
    /#btn-enable-push\s+\.xb-ico-bell\s*\{[^}]*display:\s*none/.test(CSS),
    "the default (not on) must show the struck bell",
  );
});

test("the bell is still the only push control, and nothing else may prompt", () => {
  // Moving the element must not have moved the logic: index.html carries no
  // push call, and app.js only wires and reads.
  for (const [name, src] of [["index.html", html], ["app.js", appJs], ["chat.js", chatJs]]) {
    for (const api of ["requestPermission", "pushManager.subscribe"]) {
      assert.ok(!src.includes(api), `${name} must never reach ${api} (D-27-05)`);
    }
  }
  assert.ok(
    appJs.includes("wirePushButton(api,") && appJs.includes("refreshPushButton(api,"),
    "app.js must wire the bell through push.js rather than handling the click itself",
  );
});

// ---- 5. The settings surface ---------------------------------------------

test("settings holds the display mode and the push state", () => {
  const panel = /<div[^>]*id="settings-panel"[\s\S]*?\n    <\/div>/.exec(HTML);
  assert.ok(panel, "index.html must declare #settings-panel");
  for (const id of ["btn-theme-light", "btn-theme-dark", "settings-push-state"]) {
    assert.ok(panel[0].includes(`id="${id}"`), `#${id} belongs inside the settings panel`);
  }
  // Settings is for preferences and for the account. The overlays are header
  // ACTIONS and must not have crept in here as "just a link".
  for (const marker of ["board", "invite", "people", "catchup", "add-to-memory"]) {
    assert.ok(
      !panel[0].toLowerCase().includes(marker),
      `the settings panel references "${marker}" — that is a header action, not a setting`,
    );
  }
});

test("sign out is the LAST row of settings, and is not in the header at all", () => {
  // It is a rare, deliberate act. In the header it wore a glyph that reads as
  // "exit" only once you already know, one tap away from the controls people
  // use constantly.
  const header = /<header[^>]*class="xb-header"[^>]*>([\s\S]*?)<\/header>/.exec(HTML)[1];
  assert.ok(
    !header.includes('id="btn-sign-out"'),
    "sign out must have left the header — it competed with what people actually use",
  );
  const panel = /<div[^>]*id="settings-panel"[\s\S]*?\n    <\/div>/.exec(HTML)[0];
  const btn = /<button[^>]*id="btn-sign-out"[\s\S]*?<\/button>/.exec(panel);
  assert.ok(btn, "#btn-sign-out belongs at the end of the settings panel");
  // Last: everything else in the panel opens before it in the markup.
  const rest = panel.slice(panel.indexOf(btn[0]) + btn[0].length);
  assert.ok(
    !/<(button|input|select|a)\b/.test(rest),
    `sign out must be the last control in the panel; found more after it: ${rest.trim().slice(0, 60)}`,
  );
  assert.equal(
    btn[0].replace(/<[^>]*>/g, "").trim(),
    "Sign out",
    "a labelled row, not an icon — an icon here would be the same mistake in a new place",
  );
  assert.ok(
    !btn[0].includes("<svg"),
    "no glyph: the row IS the label",
  );
  assert.ok(
    btn[0].includes('class="xb-settings-signout"'),
    "it needs its own class, or it inherits the look of a preference",
  );
  // Full width, and destructive-styled so it never reads as one more setting.
  const p = props(selectorBlock(CSS, ".xb-settings-signout"));
  assert.equal(p.width, "100%", "a full-width row, not a button floating in the panel");
  assert.ok(
    (p.color || "").includes("var(--destructive)") ||
      (p.border || "").includes("var(--destructive)"),
    `the row must be destructive-styled (got color=${p.color} border=${p.border})`,
  );
  assert.equal(p["border-radius"], "var(--radius)", "radius must stay token-driven (0)");
  assert.ok(
    selectorBlock(CSS, ".xb-settings-signout:focus-visible"),
    "keyboard users must see where they are",
  );
  // The id is load-bearing: app.js binds the click and toggles its visibility.
  assert.ok(
    appJs.includes('el("btn-sign-out")'),
    "app.js still owns the click; moving the element must not have moved the wiring",
  );
});

test("the theme switch is pinned to the top-right of the panel, not listed as a row", () => {
  const head = /<div[^>]*class="xb-settings-head"[\s\S]*?<\/div>\s*<\/div>/.exec(HTML);
  assert.ok(head, "the panel must have a .xb-settings-head");
  assert.ok(head[0].includes('class="seg"'), "the .seg toggle belongs in the panel header");
  const p = props(selectorBlock(CSS, ".xb-settings-head"));
  assert.equal(p["justify-content"], "space-between", "title left, switch right");
});

test("the theme toggle is radius 0 and fills the pressed segment", () => {
  const seg = props(selectorBlock(CSS, ".seg"));
  assert.equal(seg["border-radius"], "var(--radius)", "radius must be token-driven (0)");
  const pressed = selectorBlock(CSS, '.seg button[aria-pressed="true"]');
  assert.ok(pressed, "app.css must style the pressed segment");
  assert.ok(
    pressed.includes("var(--primary)") && pressed.includes("var(--primary-fg)"),
    "the pressed segment fills with --primary, like everywhere else in the palette",
  );
});

test("the theme is stored under the key the extension already uses", () => {
  assert.ok(
    appJs.includes("THEME_STORAGE_KEY"),
    "app.js must persist under chat-core's THEME_STORAGE_KEY, not a key of its own",
  );
  assert.ok(
    appJs.includes('from "./chat_core/theme.js"'),
    "the theme helpers are shared; a second resolveInitialTheme would drift",
  );
});

// ---- 6. The composer is the extension's, glyph for glyph ------------------

test("the composer is a pill: + , a textarea, then ›", () => {
  const composer = /<div[^>]*id="composer"[\s\S]*?\n    <\/div>/.exec(HTML);
  assert.ok(composer, "index.html must declare #composer");
  const body = composer[0];
  assert.ok(body.includes('class="xb-composer-pill"'), "the controls live inside one pill");

  const clip = /<button[^>]*id="btn-clip"[\s\S]*?<\/button>/.exec(body);
  assert.ok(clip, "the pill must carry the attach button");
  assert.equal(
    clip[0].replace(/<[^>]*>/g, "").trim(),
    "+",
    'attach is a bare "+" — a paperclip emoji renders in its own colour and fights the monochrome palette',
  );

  const send = /<button[^>]*id="btn-send"[\s\S]*?<\/button>/.exec(body);
  assert.ok(send, "the pill must carry the send button");
  const sendGlyph = send[0].replace(/<[^>]*>/g, "").trim();
  assert.ok(
    sendGlyph === "\u203a" || sendGlyph === "&rsaquo;",
    `send is a bare "\u203a", got ${JSON.stringify(sendGlyph)} — not the word "Send", not an arrow emoji`,
  );

  assert.ok(
    /<textarea[^>]*id="composer-input"/.test(body),
    "the field is a textarea, not an input — an input cannot grow with the text",
  );
  assert.ok(/rows="1"/.test(body), 'the textarea starts at rows="1" so the pill is one line at rest');
});

test("the placeholder is the owner's wording, unchanged", () => {
  // Short on purpose: the old sentence ran past the pill's width on a phone and
  // the half a reader saw was an instruction about the agent, on a box whose
  // first job is talking to people.
  const want = "Talk to the team, or @agent";
  assert.ok(html.includes(want), `index.html must use the placeholder "${want}"`);
  assert.ok(
    readFileSync(join(__dirname, "..", "popup.html"), "utf8").includes(want),
    "the extension must use the same one — this test is comparing two products",
  );
  const gone = "Use @agent to interact with your team agent.";
  for (const [name, src] of [
    ["index.html", html],
    ["popup.html", readFileSync(join(__dirname, "..", "popup.html"), "utf8")],
  ]) {
    assert.ok(!src.includes(gone), `${name} still carries the old placeholder`);
  }
});

test("the textarea grows to 8 rows and only then shows a scrollbar", () => {
  const p = props(selectorBlock(CSS, "#composer-input"));
  assert.equal(p["min-height"], "calc(1.5em + 16px)", "one row at rest");
  assert.equal(p["max-height"], "calc(1.5em * 8 + 16px)", "grows to 8 rows, then scrolls");
  assert.equal(
    p["overflow-y"],
    "hidden",
    "no scrollbar at rest — the pill must read as a single clean line",
  );
  assert.ok(
    selectorBlock(CSS, "#composer-input.is-overflowing"),
    "app.css must define the overflowing state the resize logic toggles",
  );
  assert.ok(
    chatJs.includes("is-overflowing"),
    "chat.js must toggle .is-overflowing, or the ceiling traps text with no way to scroll to it",
  );
});

test("the pill's two ends read as equally inset — and both products agree", () => {
  // "Collé à droite", as reported on an iPhone. The two ends of the pill put
  // their first mark in different places: the send button is a filled block
  // whose EDGE is its ink, so its inset is its padding outright, while the "+"
  // is a thin glyph centred in a 32px box and carries ~10px of that box before
  // any ink. Equal numbers therefore do not read as equal — and these were not
  // even equal: 6px on the left against 4px on the right put the heavier, higher
  // contrast end nearest the border. Whatever the numbers become, the end
  // carrying the filled block is the one that needs the room.
  const seen = {};
  for (const [name, src] of [
    ["app.css", CSS],
    ["popup.css", POPUP_CSS],
  ]) {
    const pill = props(selectorBlock(src, ".xb-composer-pill"));
    const parts = (pill.padding || "").split(/\s+/).filter(Boolean);
    assert.ok(parts.length >= 2, `${name}: .xb-composer-pill declares no usable padding`);
    // CSS shorthand: 2 values are vertical/horizontal, 3 add a bottom, 4 are
    // clockwise from the top.
    const right = parts.length === 1 ? parts[0] : parts[1];
    const left = parts.length === 4 ? parts[3] : right;
    const px = (v) => Number.parseInt(v, 10);
    assert.ok(
      Number.isFinite(px(right)) && Number.isFinite(px(left)),
      `${name}: padding is "${pill.padding}" — this reads pixels`,
    );
    assert.ok(
      px(right) >= px(left),
      `${name}: padding is "${pill.padding}", ${px(left)}px left against ${px(right)}px right — the ghost glyph already carries its own air and the filled block carries none, so the smaller number on the send side is exactly what makes the pill look shoved against its right edge`,
    );
    assert.ok(
      px(right) >= 8,
      `${name}: ${px(right)}px between the send button and the pill border reads as touching it`,
    );
    seen[name] = pill.padding;
  }
  assert.equal(
    seen["app.css"],
    seen["popup.css"],
    "one pill, two products: a padding that drifts between them is the same bug fixed once",
  );
});

test("balancing the pill did not shrink a touch target", () => {
  // The buttons are the things a thumb has to hit; the pill's padding must not
  // be bought out of their size. 32px is what they have always been — smaller
  // is a regression, and the pill still stands a row taller than they are.
  for (const [name, src] of [
    ["app.css", CSS],
    ["popup.css", POPUP_CSS],
  ]) {
    for (const sel of [".xb-clip-btn", ".xb-send-btn", ".xb-agent-btn"]) {
      const btn = props(selectorBlock(src, sel));
      assert.ok(
        Number.parseInt(btn.width, 10) >= 32 && Number.parseInt(btn.height, 10) >= 32,
        `${name} ${sel}: ${btn.width} x ${btn.height}`,
      );
    }
  }
});

test("Enter sends and Shift+Enter inserts a newline", () => {
  assert.ok(
    /event\.shiftKey\)\s*return/.test(chatJs),
    "chat.js must let SHIFT+ENTER fall through to the textarea's own newline",
  );
  assert.ok(
    /event\.key !== "Enter"/.test(chatJs) && /preventDefault\(\);\s*\n\s*sendMessage\(\)/.test(chatJs),
    "a bare Enter must send instead of inserting a newline",
  );
});

test("the attach button actually uploads — no dead control", () => {
  // A button that looks identical to the extension's and does nothing is worse
  // than not shipping it.
  assert.ok(chatJs.includes("uploadMedia("), "chat.js must call the shared upload");
  assert.ok(
    chatJs.includes('el("file-picker")') && chatJs.includes("picker.click()"),
    'the "+" must open the file picker',
  );
  assert.ok(
    HTML.includes('id="file-picker"'),
    "index.html must declare the hidden file input the button opens",
  );
  assert.ok(
    chatJs.includes("MAX_MEDIA_BYTES"),
    "the size ceiling comes from chat-core, so both surfaces refuse the same files",
  );
});

test("both surfaces send their upload through ONE multipart call site", () => {
  const popupJs = readFileSync(join(__dirname, "..", "popup.js"), "utf8");
  for (const [name, src] of [["chat.js", chatJs], ["popup.js", popupJs]]) {
    assert.ok(
      !src.includes("new FormData("),
      `${name} builds its own multipart body — api.uploadMedia is the one place that does`,
    );
    assert.ok(src.includes("uploadMedia("), `${name} must call api.uploadMedia`);
  }
});

// ---- 7. The ids each module binds exist in the markup ---------------------

test("every id app.js and chat.js bind is declared in index.html", () => {
  const ids = new Set();
  for (const src of [appJs, chatJs]) {
    for (const m of src.matchAll(/\bel\(\s*["']([a-z0-9-]+)["']\s*\)/g)) ids.add(m[1]);
  }
  assert.ok(ids.size >= 15, `expected a substantial binding set, found ${ids.size}`);
  for (const id of ids) {
    assert.ok(
      html.includes(`id="${id}"`),
      `#${id} is bound but not declared — the binding silently does nothing`,
    );
  }
});

// ---- 8. English only ------------------------------------------------------

test("english-only: no accented Latin chars in the new surfaces", () => {
  for (const [name, src] of [
    ["index.html", html],
    ["app.css", css],
    ["app.js", appJs],
    ["chat.js", chatJs],
  ]) {
    const hits = src.match(/[À-ÿ]/g) || [];
    assert.equal(
      hits.length,
      0,
      `${name} has ${hits.length} accented char(s) ${JSON.stringify([...new Set(hits)])}`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

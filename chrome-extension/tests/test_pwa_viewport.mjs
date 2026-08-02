/**
 * The on-screen keyboard must not take the header off the screen
 * (app-site/app/viewport.js + the shell rules in app.css).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/ and ../popup.css.
 *
 * WHY A LAYOUT FIX NEEDS A TEST AT ALL. Nothing in this repo can open a phone,
 * so the assertions below stand in for the three things that were each,
 * separately, enough to break it:
 *
 *   - the viewport meta with no `interactive-widget` directive (Chrome draws
 *     the keyboard OVER the page and the browser scrolls to reveal the field);
 *   - a shell height of 100dvh, which no keyboard ever updates;
 *   - no visualViewport listener at all, which is the only signal iOS gives.
 *
 * And one that is invisible in a browser too: a leaked listener pair. Both run
 * on every frame of a keyboard animation, and a second binding writes the same
 * variable twice for the rest of the session. The detach is asserted here
 * because nothing else can see it.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
const appJs = readFileSync(join(APP_DIR, "app.js"), "utf8");
const viewportJs = readFileSync(join(APP_DIR, "viewport.js"), "utf8");
const css = readFileSync(join(APP_DIR, "app.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);
const popupCss = readFileSync(join(__dirname, "..", "popup.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

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
const pending = [];
function testAsync(name, body) {
  pending.push([name, body]);
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

// ---- 1. The meta directive (Chrome / Android) ----------------------------

test("the viewport meta tells Chrome to RESIZE for the keyboard, not overlay it", () => {
  const meta = /<meta[^>]+name="viewport"[^>]*>/.exec(html);
  assert.ok(meta, "index.html must declare a viewport meta");
  assert.match(
    meta[0],
    /interactive-widget=resizes-content/,
    "without this directive Chrome draws the keyboard over the viewport, the composer ends up underneath it, and the browser scrolls the page to reveal it — taking the header out through the top",
  );
  assert.match(
    meta[0],
    /viewport-fit=cover/,
    "viewport-fit=cover is what makes env(safe-area-inset-*) resolve to anything — the keyboard fix must not cost the home-indicator clearance",
  );
  assert.match(meta[0], /width=device-width/);
});

// ---- 2. The shell (app.css) ---------------------------------------------

test("the shell's height is the MEASURED viewport, with 100dvh only as a fallback", () => {
  const body = props(selectorBlock(css, "body"));
  assert.equal(
    body.height,
    "var(--xb-viewport-height, 100dvh)",
    `body height is ${body.height} — dvh tracks browser chrome and is never updated by the keyboard, so it can only be the fallback`,
  );
  assert.equal(body.overflow, "hidden", "the page itself must not scroll");
});

test("the app column cannot scroll either — only the message list does", () => {
  const app = props(selectorBlock(css, "#app"));
  assert.equal(app.overflow, "hidden", "#app must not become a second scroller");
  const scroll = props(selectorBlock(css, "#chat-scroll"));
  assert.equal(scroll["overflow-y"], "auto", "the thread is the one scroller");
  assert.equal(
    scroll["overscroll-behavior"],
    "contain",
    "reaching the top of the thread must not hand the gesture to the page",
  );
});

test("the header is still pinned: it may not be squeezed or scrolled away", () => {
  const header = props(selectorBlock(css, ".xb-header"));
  assert.equal(
    header["flex-shrink"],
    "0",
    "a shrinkable header is a header the composer can squeeze to nothing",
  );
});

test("the composer keeps its safe-area inset, and drops it only while the keys are up", () => {
  const composer = props(selectorBlock(css, ".xb-composer"));
  assert.match(
    composer["padding-bottom"] || "",
    /env\(safe-area-inset-bottom\)/,
    "a fixed pixel value here puts the send button under the home indicator",
  );
  const open = props(selectorBlock(css, ':root[data-keyboard="open"] .xb-composer'));
  assert.ok(
    open && open["padding-bottom"],
    "with the keyboard covering the home indicator, that inset is a strip of dead space between the pill and the keys",
  );
  assert.ok(
    !/env\(/.test(open["padding-bottom"]),
    "the keyboard-open override must not re-add the inset it exists to drop",
  );
});

test("an overlay card is sized against the same measured viewport", () => {
  const card = props(selectorBlock(css, ".xb-overlay-card"));
  assert.match(
    card["max-height"] || "",
    /var\(--xb-viewport-height, 100dvh\)/,
    "a card sized against the full viewport has its own header off screen once the keyboard opens",
  );
});

// ---- 3. The extension's popup is a different surface and stays untouched --

test("the popup/side-panel shell is not dragged into the PWA's viewport math", () => {
  const app = props(selectorBlock(popupCss, "#app"));
  assert.equal(
    app["min-height"],
    "560px",
    "an extension popup has no viewport of its own; the 560px floor is what stops the flex column collapsing to a strip",
  );
  assert.equal(app.height, "100%", "the side panel IS a real viewport — height:100% fills it");
  assert.ok(
    !popupCss.includes("--xb-viewport-height"),
    "the popup must not be resized to a visual viewport it does not have — the floor above is the whole point",
  );
});

// ---- 4. The module itself, driven against a fake window ------------------

const viewport = await import(pathToFileURL(join(APP_DIR, "viewport.js")).href);

/** A root element stub: records custom properties and attributes. */
function makeRoot() {
  const attrs = {};
  const styles = {};
  return {
    attrs,
    styles,
    style: {
      setProperty: (k, v) => {
        styles[k] = v;
      },
      removeProperty: (k) => {
        delete styles[k];
      },
    },
    setAttribute: (k, v) => {
      attrs[k] = v;
    },
    removeAttribute: (k) => {
      delete attrs[k];
    },
  };
}

/**
 * A window stub with a visual viewport.
 *
 * @param {{height: number, innerHeight?: number, scale?: number, scrollY?: number}} opts
 */
function makeView(opts = {}) {
  const listeners = {};
  const scrolls = [];
  const view = {
    innerHeight: opts.innerHeight ?? 800,
    scrollY: opts.scrollY ?? 0,
    scrollTo: (x, y) => {
      scrolls.push([x, y]);
      view.scrollY = y;
    },
    visualViewport: {
      height: opts.height ?? 800,
      scale: opts.scale ?? 1,
      addEventListener: (type, fn) => {
        (listeners[type] = listeners[type] || []).push(fn);
      },
      removeEventListener: (type, fn) => {
        const list = listeners[type] || [];
        const i = list.indexOf(fn);
        if (i !== -1) list.splice(i, 1);
      },
    },
  };
  view.listeners = listeners;
  view.scrolls = scrolls;
  view.fire = (type) => {
    for (const fn of listeners[type] || []) fn();
  };
  view.count = (type) => (listeners[type] || []).length;
  return view;
}

test("binding writes the measured height and listens on BOTH resize and scroll", () => {
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });

  assert.equal(
    root.styles["--xb-viewport-height"],
    "800px",
    "the height must be written immediately — waiting for the first event leaves the first paint on the fallback",
  );
  assert.equal(view.count("resize"), 1, "Android's keyboard fires resize");
  assert.equal(
    view.count("scroll"),
    1,
    "iOS slides the visual viewport instead of resizing the layout one: the event that arrives is scroll, and a resize-only listener sees nothing on the platform that needs this most",
  );
});

test("the keyboard opening is followed on the SCROLL event alone (the iOS path)", () => {
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });

  view.visualViewport.height = 420;
  view.fire("scroll");
  assert.equal(root.styles["--xb-viewport-height"], "420px");
  assert.equal(
    root.attrs["data-keyboard"],
    "open",
    "380px of the viewport went away — that is a keyboard, and the composer's safe-area inset must collapse",
  );

  view.visualViewport.height = 800;
  view.fire("scroll");
  assert.equal(root.styles["--xb-viewport-height"], "800px");
  assert.equal(
    root.attrs["data-keyboard"],
    undefined,
    "the keyboard closed — the home indicator is exposed again and the inset comes back",
  );
});

test("a small shrink is the address bar, not a keyboard", () => {
  const view = makeView({ height: 740, innerHeight: 800 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });
  assert.equal(root.styles["--xb-viewport-height"], "740px", "the height still follows");
  assert.equal(
    root.attrs["data-keyboard"],
    undefined,
    "60px is a toolbar; collapsing the composer's inset for it would put the send button under the home indicator",
  );
});

test("a page the browser scrolled to reveal the field is scrolled back", () => {
  // This IS the reported bug: iOS scrolls the layout viewport to bring the
  // focused composer above the keyboard, and the header leaves through the top.
  const view = makeView({ height: 420, innerHeight: 800, scrollY: 180 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });
  assert.deepEqual(view.scrolls, [[0, 0]], "the shell now fits, so the page must sit at the top");
});

test("an unscrolled page is left alone", () => {
  const view = makeView({ height: 800, innerHeight: 800, scrollY: 0 });
  viewport.bindViewport({ view, root: makeRoot() });
  assert.deepEqual(view.scrolls, [], "a scrollTo on every frame would fight the reader");
});

test("a pinch-zoomed viewport is not mistaken for a keyboard", () => {
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });

  view.visualViewport.scale = 2.4;
  view.visualViewport.height = 330;
  view.fire("scroll");
  assert.equal(
    root.styles["--xb-viewport-height"],
    "800px",
    "zooming must not shrink the app under the reader's fingers — the last honest height stays",
  );
  assert.equal(root.attrs["data-keyboard"], undefined);
});

test("detaching removes BOTH listeners and returns the shell to the fallback", () => {
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  const detach = viewport.bindViewport({ view, root });

  view.visualViewport.height = 400;
  view.fire("resize");
  assert.equal(root.styles["--xb-viewport-height"], "400px");

  detach();
  assert.equal(view.count("resize"), 0, "a leaked listener runs on every keyboard frame, forever");
  assert.equal(view.count("scroll"), 0);
  assert.equal(
    root.styles["--xb-viewport-height"],
    undefined,
    "a detached binding must not leave the shell frozen at whatever height the keyboard left behind",
  );
  assert.equal(root.attrs["data-keyboard"], undefined);

  // And it is genuinely detached: a later event writes nothing.
  view.visualViewport.height = 200;
  view.fire("resize");
  assert.equal(root.styles["--xb-viewport-height"], undefined);
});

test("no VisualViewport: nothing is written, nothing throws, teardown still works", () => {
  const root = makeRoot();
  const view = { innerHeight: 800, scrollY: 0 };
  const detach = viewport.bindViewport({ view, root });
  assert.equal(typeof detach, "function", "the caller's teardown must stay unconditional");
  assert.equal(
    root.styles["--xb-viewport-height"],
    undefined,
    "the stylesheet's 100dvh fallback is what runs here — writing a guess would override it",
  );
  detach();
});

test("bindViewport survives being called with nothing at all", () => {
  // node has no window and no document; the module must not reach for either.
  const detach = viewport.bindViewport();
  assert.equal(typeof detach, "function");
  detach();
});

// ---- 5. The shell wires it exactly once ---------------------------------

test("app.js binds the viewport on boot, and re-binding cannot stack listeners", () => {
  assert.ok(appJs.includes('from "./viewport.js"'), "app.js must import the binding");
  assert.equal(
    (appJs.match(/bindViewport\(/g) || []).length,
    1,
    "one call site, or two bindings write the same variable on every keyboard frame",
  );
  assert.match(
    appJs,
    /if \(releaseViewport\) releaseViewport\(\);/,
    "a re-bind must take the previous one down first — the leak is invisible until it is not",
  );
  assert.match(appJs, /wireViewport\(\);/, "boot() must actually call it");
});

test("viewport.js is precached, or the shell is broken offline", () => {
  const sw = readFileSync(join(APP_DIR, "sw.js"), "utf8");
  assert.ok(sw.includes('"/app/viewport.js"'), "sw.js must precache the new module");
});

test("viewport.js cannot raise the notification prompt (D-27-05)", () => {
  for (const api of ["requestPermission", "pushManager.subscribe"]) {
    assert.ok(!viewportJs.includes(api), `viewport.js must never reach ${api}`);
  }
});

test("english-only: no accented Latin chars in viewport.js", () => {
  const hits = viewportJs.match(/[À-ÿ]/g) || [];
  assert.equal(hits.length, 0, `viewport.js has ${JSON.stringify([...new Set(hits)])}`);
});

// ---- Run the async probes, then report ----------------------------------

for (const [name, body] of pending) {
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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

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

test("the shell is POSITIONED against the visible window, not only sized to it", () => {
  // The black band between the composer and the keys. A column of the right
  // height painted from the top of the layout viewport ends `offsetTop` pixels
  // short of the bottom of the visible window, and what shows in the gap is bare
  // canvas. Sizing alone cannot fix it — the box has to move.
  const body = props(selectorBlock(css, "body"));
  assert.equal(
    body.transform,
    "translateY(var(--xb-viewport-offset-top, 0px))",
    `body transform is ${body.transform} — the shell must follow the visual viewport's offset, with 0px (flush) as the fallback`,
  );
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

test("double-tap zooms nothing, and pinch still zooms everything", () => {
  const body = props(selectorBlock(css, "body"));
  assert.equal(
    body["touch-action"],
    "manipulation",
    "without it a double-tap on a message zooms the whole app — and `manipulation` is the value that drops that gesture while KEEPING pinch-zoom",
  );
  const meta = /<meta[^>]+name="viewport"[^>]*>/.exec(html);
  assert.ok(
    !/user-scalable\s*=\s*(no|0)/.test(meta[0]),
    "user-scalable=no is the mistake this invites: iOS has ignored it since iOS 10, so it fixes nothing there, and on the browsers that honour it somebody who needs to magnify the text no longer can",
  );
  assert.ok(
    !/maximum-scale/.test(meta[0]),
    "maximum-scale is the same accessibility regression wearing a different name",
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
 * WHAT IT CANNOT BE EVIDENCE FOR. A stub has no keyboard. It answers whatever
 * numbers a test hands it, so it proves what this module DOES with a reported
 * geometry and never that iOS reports that geometry. The assumptions are named
 * where they are used.
 *
 * @param {{height: number, innerHeight?: number, scale?: number, scrollY?: number,
 *   offsetTop?: number}} opts
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
      offsetTop: opts.offsetTop ?? 0,
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

test("the visible window's OFFSET is measured, not assumed to be zero", () => {
  // ASSUMPTION, unverifiable here: that iOS reports a non-zero offsetTop when it
  // slides the visual viewport over a page that cannot scroll. What this proves
  // is only that a reported offset reaches the stylesheet — if the device
  // reports 0, the variable is 0px and the shell is where it always was.
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });
  assert.equal(
    root.styles["--xb-viewport-offset-top"],
    "0px",
    "flush is a measurement too — leaving the variable unset would make the stylesheet's fallback the only value it ever has",
  );

  view.visualViewport.height = 420;
  view.visualViewport.offsetTop = 44;
  view.fire("scroll");
  assert.equal(
    root.styles["--xb-viewport-offset-top"],
    "44px",
    "the window moved down 44px inside the layout viewport; a shell that ignores that ends 44px short of the bottom of the screen — the band between the composer and the keys",
  );

  view.visualViewport.offsetTop = 0;
  view.fire("scroll");
  assert.equal(root.styles["--xb-viewport-offset-top"], "0px", "and it comes back");
});

test("scrollY and offsetTop are corrected as the separate quantities they are", () => {
  // The page is exactly at the top AND the window has moved: scrollTo(0, 0) has
  // nothing to do here, which is why reading only scrollY missed this for a
  // whole release.
  const view = makeView({ height: 420, innerHeight: 800, scrollY: 0, offsetTop: 60 });
  const root = makeRoot();
  viewport.bindViewport({ view, root });
  assert.deepEqual(view.scrolls, [], "there is nothing to scroll back");
  assert.equal(
    root.styles["--xb-viewport-offset-top"],
    "60px",
    "and the offset is still 60 — scrollY answers a different question",
  );
});

test("detaching also gives back the offset, not just the height", () => {
  const view = makeView({ height: 420, innerHeight: 800, offsetTop: 44 });
  const root = makeRoot();
  const detach = viewport.bindViewport({ view, root });
  assert.equal(root.styles["--xb-viewport-offset-top"], "44px");
  detach();
  assert.equal(
    root.styles["--xb-viewport-offset-top"],
    undefined,
    "a shell left translated by an offset nothing is measuring any more has its header off screen for good",
  );
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

// ---- 4b. The subscription (what the thread listens to) ------------------

/** Run `body` with a subscriber attached, and always take it down again. */
function withSubscriber(handler, body) {
  const off = viewport.onViewportChange(handler);
  try {
    body();
  } finally {
    off();
  }
}

test("a change is announced once, with the height it replaced", () => {
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  const seen = [];
  withSubscriber(
    (change) => seen.push(change),
    () => {
      viewport.bindViewport({ view, root });
      assert.equal(seen.length, 1, "the first measurement is a change too");
      assert.equal(
        seen[0].previousHeight,
        800,
        "nothing has moved yet — a first event reporting a drop from zero would have every subscriber react to a change that did not happen",
      );

      view.visualViewport.height = 464;
      view.fire("resize");
      assert.equal(seen.length, 2);
      assert.equal(seen[1].height, 464);
      assert.equal(
        seen[1].previousHeight,
        800,
        "336px is what the message list just lost, and the only way back to where the reader was",
      );

      // iOS fires scroll continuously. The same numbers are not news.
      view.fire("scroll");
      view.fire("scroll");
      assert.equal(
        seen.length,
        2,
        "re-announcing an unchanged viewport makes every subscriber re-do its work on every frame of a keyboard animation",
      );
    },
  );
});

test("the keyboard flag rides along, at a real iPhone's numbers", () => {
  // ASSUMPTION, unverifiable here: that window.innerHeight is the LAYOUT
  // viewport on iOS and does not shrink for the keyboard. If it did shrink with
  // visualViewport.height, `covered` would be ~0, the flag would never fire and
  // the composer would keep an inset it should drop. The margin is what makes
  // this safe rather than lucky: 336 against a 120 threshold.
  const view = makeView({ height: 844, innerHeight: 844 });
  const root = makeRoot();
  const seen = [];
  withSubscriber(
    (change) => seen.push(change),
    () => {
      viewport.bindViewport({ view, root });
      assert.equal(seen[0].keyboard, false);

      view.visualViewport.height = 508; // 844 - (keyboard 292 + accessory 44)
      view.fire("resize");
      assert.equal(seen[1].keyboard, true, "336px of viewport is a keyboard by any threshold");
      assert.equal(root.attrs["data-keyboard"], "open");
    },
  );
});

test("an offset that moves on its own is announced even when the height holds", () => {
  const view = makeView({ height: 508, innerHeight: 844 });
  const root = makeRoot();
  const seen = [];
  withSubscriber(
    (change) => seen.push(change),
    () => {
      viewport.bindViewport({ view, root });
      view.visualViewport.offsetTop = 36;
      view.fire("scroll");
      assert.equal(seen.length, 2, "the window moved; the shell has to move with it");
      assert.equal(seen[1].offsetTop, 36);
      assert.equal(
        seen[1].previousHeight,
        seen[1].height,
        "nothing was taken from the list, so nothing about the reader's position changed",
      );
    },
  );
});

test("unsubscribing is honoured, and a thrown subscriber cannot stop the measurement", () => {
  const view = makeView({ height: 800, innerHeight: 800 });
  const root = makeRoot();
  let calls = 0;
  const off = viewport.onViewportChange(() => {
    calls++;
    throw new Error("a subscriber's bug");
  });
  // The module reports the throw. Expected here, and unreadable in the runner's
  // output, so it is swallowed for exactly this block.
  const warn = console.warn;
  console.warn = () => {};
  try {
    viewport.bindViewport({ view, root });
    view.visualViewport.height = 500;
    view.fire("resize");
  } finally {
    console.warn = warn;
  }
  assert.equal(calls, 2, "bound + one change");
  assert.equal(
    root.styles["--xb-viewport-height"],
    "500px",
    "the height IS the layout: a throwing listener must not freeze the shell at whatever the keyboard left behind",
  );

  off();
  view.visualViewport.height = 640;
  view.fire("resize");
  assert.equal(calls, 2, "an unsubscribed handler is not called again");
  assert.equal(root.styles["--xb-viewport-height"], "640px");
});

test("onViewportChange survives a caller who hands it nothing", () => {
  assert.equal(typeof viewport.onViewportChange(), "function");
  assert.equal(typeof viewport.onViewportChange(null)(), "undefined");
});

test("viewport.js never reaches into the chat's DOM", () => {
  // The wiring is one-directional on purpose: this module measures, and what to
  // do about a measurement is a question about where the reader is in the
  // conversation. A getElementById here would put both facts in the wrong file.
  for (const reach of ["getElementById", "querySelector", "chat-scroll", "message-list"]) {
    assert.ok(
      !viewportJs.includes(reach),
      `viewport.js names ${reach} — it measures, it does not decide what anything else should do about the measurement`,
    );
  }
});

// ---- 4c. What the thread does about it ----------------------------------
//
// The decision lives in chat-core (createViewportAnchor) precisely so it can be
// driven here rather than described. chat.js's own part is three lines of
// wiring, and those are asserted as text further down — a stub cannot press a
// key, so what is proven below is the arithmetic, not the phone.

const render = await import(
  pathToFileURL(join(APP_DIR, "chat_core", "render.js")).href
);

/**
 * A scroller, and the ONE fact that makes this bug what it is: when the shell
 * loses height the scroller absorbs it, so clientHeight drops while scrollTop
 * and scrollHeight stay exactly as they were.
 */
function makeScroller({ scrollHeight = 2000, clientHeight = 800, scrollTop = 0 }) {
  return {
    scrollHeight,
    clientHeight,
    scrollTop,
    /** The keyboard opening, as the scroller experiences it. */
    absorb(px) {
      this.clientHeight -= px;
    },
  };
}

test("the keyboard opening re-anchors a thread that was AT the bottom", () => {
  const scroller = makeScroller({ scrollHeight: 2000, clientHeight: 800, scrollTop: 1200 });
  let forced = 0;
  const anchor = render.createViewportAnchor({
    getScrollEl: () => scroller,
    scrollToBottom: (opts) => {
      if (opts && opts.force) forced++;
      scroller.scrollTop = scroller.scrollHeight;
    },
  });

  scroller.absorb(336); // the shell shrank first; the handler runs after
  const anchored = anchor({ height: 508, previousHeight: 844 });

  assert.equal(
    anchored,
    true,
    "the reader was sitting exactly at the end — after the shrink the raw gap reads 336px, and an implementation that measures it without subtracting what the viewport lost decides they are far from the bottom and leaves the message they are replying to behind the composer",
  );
  assert.equal(forced, 1, "the unforced path would re-ask against the invalidated geometry");
});

test("...and does NOT re-anchor somebody who had scrolled up to read", () => {
  // 500px up: reading history. Tapping the field must not throw them back to
  // the present — that is a worse bug than the one being fixed.
  const scroller = makeScroller({ scrollHeight: 2000, clientHeight: 800, scrollTop: 700 });
  let calls = 0;
  const anchor = render.createViewportAnchor({
    getScrollEl: () => scroller,
    scrollToBottom: () => calls++,
  });

  scroller.absorb(336);
  assert.equal(anchor({ height: 508, previousHeight: 844 }), false);
  assert.equal(calls, 0, "their position is theirs");
  assert.equal(scroller.scrollTop, 700, "and it is untouched");
});

test("a nudge of a few lines still counts as following the conversation", () => {
  const scroller = makeScroller({ scrollHeight: 2000, clientHeight: 800, scrollTop: 1140 });
  let calls = 0;
  const anchor = render.createViewportAnchor({
    getScrollEl: () => scroller,
    scrollToBottom: () => calls++,
  });
  scroller.absorb(336);
  assert.equal(
    anchor({ height: 508, previousHeight: 844 }),
    true,
    "60px off the end is the same tolerance auto-scroll already uses; two different answers to one question is how the thread and the unread badge start disagreeing",
  );
  assert.equal(calls, 1);
});

test("the keyboard CLOSING gives the room back without moving a reader", () => {
  // Growing absorbs nothing, so the gap is read as it stands. Somebody who was
  // 500px up while typing is still 500px up when the keys go away.
  const scroller = makeScroller({ scrollHeight: 2000, clientHeight: 508, scrollTop: 992 });
  let calls = 0;
  const anchor = render.createViewportAnchor({
    getScrollEl: () => scroller,
    scrollToBottom: () => calls++,
  });
  scroller.clientHeight = 844;
  assert.equal(anchor({ height: 844, previousHeight: 508 }), false);
  assert.equal(calls, 0);
});

test("the near-bottom threshold is one number, and it is where it says it is", () => {
  // Three behaviours read this: auto-scroll on a new message, the keyboard
  // re-anchor above, and the jump-to-latest control. They must agree — a button
  // offering to take somebody to a bottom the app thinks it is already at does
  // nothing when pressed.
  const at = (gap) => ({ scrollHeight: 1000, clientHeight: 500, scrollTop: 500 - gap });
  assert.equal(render.isNearBottom(at(0)), true, "exactly at the end");
  assert.equal(render.isNearBottom(at(119)), true, "a nudge is still following");
  assert.equal(render.isNearBottom(at(120)), false, "and 120 is where reading history starts");
  assert.equal(render.NEAR_BOTTOM_PX, 120, "the number is exported, so nobody has to restate it");
  assert.equal(render.isNearBottom(null), false, "no scroller is not 'at the bottom'");
});

test("an anchor with no thread to anchor decides nothing", () => {
  const anchor = render.createViewportAnchor({
    getScrollEl: () => null,
    scrollToBottom: () => {
      throw new Error("must not be called");
    },
  });
  assert.equal(anchor({ height: 508, previousHeight: 844 }), false);
  assert.equal(render.createViewportAnchor()({ height: 1, previousHeight: 2 }), false);
});

test("chat.js wires the two halves together, and declares neither", () => {
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  assert.match(
    chat,
    /import \{ onViewportChange \} from "\.\/viewport\.js"/,
    "the thread must subscribe to the measurement rather than measure for itself",
  );
  assert.match(chat, /createViewportAnchor/, "and use chat-core's decision");
  assert.equal(
    (chat.match(/onViewportChange\(/g) || []).length,
    1,
    "one subscription: a second would scroll the thread twice on every frame of a keyboard animation",
  );
  assert.ok(
    !/function\s+(isNearBottom|createViewportAnchor)\s*\(/.test(chat),
    "a second definition of these is a second answer to where the reader is",
  );
  assert.ok(
    !/\b120\b/.test(chat.replace(/\/\*[\s\S]*?\*\//g, "")),
    "the near-bottom threshold belongs to chat-core; a copy here drifts silently",
  );
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

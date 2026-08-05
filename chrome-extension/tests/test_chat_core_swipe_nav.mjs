/**
 * Contract for packages/chat-core/swipe_nav.js — swipe left/right to switch team.
 *
 * WHAT MAKES THIS GESTURE DANGEROUS is not that it might fail to fire. It is
 * that it fires when nobody asked: a team switch tears down a websocket
 * subscription, reloads a thread and moves a read cursor, so a swipe recognised
 * out of a scroll is a visible, expensive interruption in the middle of reading.
 * Almost every assertion below is therefore a REFUSAL, and each one was checked
 * by loosening the implementation and watching it go red.
 *
 * What is locked:
 *   1. a clearly horizontal, long-enough drag fires once, in the right direction;
 *   2. a vertical or diagonal drag NEVER fires, and a gesture given to the
 *      scroller cannot be claimed back later in the same touch;
 *   3. a touch starting in the composer, on the rail, or in anything that
 *      scrolls sideways is not a swipe;
 *   4. mouse and wheel are not bound at all — the desktop is untouched;
 *   5. every listener is passive, so this module cannot block a scroll;
 *   6. the module holds no order and performs no switch: it reports a direction.
 *
 * Pure node test — a hand-rolled event target, no jsdom, no dependency.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  createSwipeNavigator,
  DEFAULT_MIN_DISTANCE_PX,
  DEFAULT_DOMINANCE,
  DEFAULT_SLOP_PX,
} from "../../packages/chat-core/swipe_nav.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const SOURCE = readFileSync(
  join(REPO_ROOT, "packages", "chat-core", "swipe_nav.js"),
  "utf8",
);
/** The source with its prose removed — see the event-name scan for why. */
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

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

// ---- A minimal element tree ------------------------------------------------

/**
 * Just enough element: parentNode, a class for `matches`, the two geometry
 * numbers the horizontal-overflow test reads, and a listener registry that
 * RECORDS ITS OPTIONS — the passive assertion is the whole reason this stub
 * cannot be a bare EventTarget.
 */
function makeNode({ selector = "", scrollWidth = 0, clientWidth = 0 } = {}) {
  const node = {
    selector,
    scrollWidth,
    clientWidth,
    parentNode: null,
    bound: [],
    matches(sel) {
      return sel === selector;
    },
    addEventListener(type, fn, options) {
      node.bound.push({ type, fn, options });
    },
    removeEventListener(type, fn) {
      const i = node.bound.findIndex((b) => b.type === type && b.fn === fn);
      if (i !== -1) node.bound.splice(i, 1);
    },
    fire(type, event) {
      for (const b of node.bound.filter((x) => x.type === type)) b.fn(event);
    },
  };
  return node;
}

const point = (x, y) => ({ clientX: x, clientY: y });
const startEvent = (target, x, y, extra = []) => ({
  target,
  touches: [point(x, y), ...extra],
});
const moveEvent = (x, y, extra = []) => ({ touches: [point(x, y), ...extra] });
const endEvent = (x, y) => ({ changedTouches: [point(x, y)] });

/** A navigator over a surface with one ordinary child to touch. */
function harness(overrides = {}) {
  const surface = makeNode({ selector: "#chat-scroll" });
  const child = makeNode({ selector: ".xb-msg-bubble" });
  child.parentNode = surface;
  const fired = [];
  const nav = createSwipeNavigator({
    surfaceEl: surface,
    onSwipe: (d) => fired.push(d),
    blockSelectors: ["#composer", "#team-rail"],
    ...overrides,
  });
  nav.attach();
  return { surface, child, fired, nav };
}

/** Drive one whole touch: down at (x0,y0), a sample at (x1,y1), up there. */
function swipe(h, x0, y0, x1, y1, target) {
  h.surface.fire("touchstart", startEvent(target || h.child, x0, y0));
  h.surface.fire("touchmove", moveEvent(x1, y1));
  h.surface.fire("touchend", endEvent(x1, y1));
}

// ---- 1. The gesture fires, once, the right way -----------------------------

test("a long leftward drag asks for the NEXT team", () => {
  const h = harness();
  swipe(h, 300, 400, 300 - DEFAULT_MIN_DISTANCE_PX - 20, 402);
  assert.deepEqual(h.fired, ["next"]);
});

test("a long rightward drag asks for the PREVIOUS team", () => {
  const h = harness();
  swipe(h, 100, 400, 100 + DEFAULT_MIN_DISTANCE_PX + 20, 398);
  assert.deepEqual(h.fired, ["previous"]);
});

test("one touch fires exactly one switch, however many samples it produced", () => {
  // A finger produces a touchmove per frame. Firing per sample would switch team
  // dozens of times in one gesture.
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400));
  for (let x = 295; x >= 180; x -= 5) h.surface.fire("touchmove", moveEvent(x, 401));
  h.surface.fire("touchend", endEvent(180, 401));
  assert.deepEqual(h.fired, ["next"]);
});

test("the module reports a DIRECTION and nothing else — it owns no order", () => {
  // The contract that keeps the rail the single source of order: this callback
  // is handed a string, never a team, an index or a list.
  const seen = [];
  const surface = makeNode({ selector: "#chat-scroll" });
  const child = makeNode();
  child.parentNode = surface;
  const nav = createSwipeNavigator({
    surfaceEl: surface,
    onSwipe: (...args) => seen.push(args),
  });
  nav.attach();
  surface.fire("touchstart", startEvent(child, 300, 400));
  surface.fire("touchmove", moveEvent(200, 400));
  surface.fire("touchend", endEvent(200, 400));
  assert.equal(seen.length, 1);
  assert.equal(seen[0].length, 1, "onSwipe takes one argument");
  assert.equal(typeof seen[0][0], "string");
  assert.ok(["next", "previous"].includes(seen[0][0]));
});

// ---- 2. It must not fight the scroll ---------------------------------------

test("a vertical drag never fires, however far it travels sideways in the end", () => {
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400));
  // Decided vertical on the first meaningful sample...
  h.surface.fire("touchmove", moveEvent(302, 340));
  // ...and no later sideways drift can take it back.
  h.surface.fire("touchmove", moveEvent(100, 320));
  h.surface.fire("touchend", endEvent(100, 320));
  assert.deepEqual(h.fired, [], "a gesture given to the scroller stays given");
});

test("a diagonal drag is the scroller's — dominance, not just direction", () => {
  const h = harness();
  // 100px across, 90px down: more horizontal than vertical, and still a scroll.
  swipe(h, 300, 400, 200, 490);
  assert.deepEqual(h.fired, []);
  // The same 100px across with a shallow slope IS a swipe.
  const flat = harness();
  swipe(flat, 300, 400, 200, 420);
  assert.deepEqual(flat.fired, ["next"]);
});

test("a short horizontal drag is not a swipe", () => {
  const h = harness();
  swipe(h, 300, 400, 300 - (DEFAULT_MIN_DISTANCE_PX - 5), 400);
  assert.deepEqual(h.fired, [], "below the minimum distance nothing happens");
});

test("a tap fires nothing at all", () => {
  const h = harness();
  swipe(h, 300, 400, 301, 400);
  assert.deepEqual(h.fired, []);
});

test("noise under the slop decides nothing, and the gesture can still be a swipe", () => {
  // The failure this prevents: a 2px wobble downward on contact settling the
  // gesture as vertical, so a deliberate swipe right after it does nothing.
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400));
  h.surface.fire("touchmove", moveEvent(300, 400 + DEFAULT_SLOP_PX - 2));
  h.surface.fire("touchmove", moveEvent(180, 404));
  h.surface.fire("touchend", endEvent(180, 404));
  assert.deepEqual(h.fired, ["next"]);
});

test("a gesture claimed sideways but released as a scroll does not fire", () => {
  // Re-checked at the end rather than trusted from the moment it was claimed:
  // a finger that starts across and then runs down the screen meant to scroll.
  //
  // The travel here is deliberately PAST the minimum distance (140px across),
  // so the distance check cannot be what refuses it — otherwise this would pass
  // with the end-of-gesture dominance test deleted, and say nothing about it.
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400));
  h.surface.fire("touchmove", moveEvent(260, 402)); // claimed: 40 across, 2 down
  h.surface.fire("touchend", endEvent(160, 700)); // released: 140 across, 300 down
  assert.deepEqual(h.fired, []);

  // The same distance across with the finger ending level IS a swipe, so the
  // assertion above is about the slope and not about the distance.
  const level = harness();
  level.surface.fire("touchstart", startEvent(level.child, 300, 400));
  level.surface.fire("touchmove", moveEvent(260, 402));
  level.surface.fire("touchend", endEvent(160, 410));
  assert.deepEqual(level.fired, ["next"]);
});

test("a second finger abandons the gesture — a pinch is not a swipe", () => {
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400, [point(320, 400)]));
  h.surface.fire("touchmove", moveEvent(180, 400));
  h.surface.fire("touchend", endEvent(180, 400));
  assert.deepEqual(h.fired, []);

  // And a finger that JOINS mid-gesture abandons it too.
  const joined = harness();
  joined.surface.fire("touchstart", startEvent(joined.child, 300, 400));
  joined.surface.fire("touchmove", moveEvent(280, 402));
  joined.surface.fire("touchmove", moveEvent(180, 402, [point(400, 400)]));
  joined.surface.fire("touchend", endEvent(180, 402));
  assert.deepEqual(joined.fired, []);
});

test("touchcancel drops the gesture, so the OS taking over fires nothing", () => {
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400));
  h.surface.fire("touchmove", moveEvent(180, 401));
  h.surface.fire("touchcancel", {});
  h.surface.fire("touchend", endEvent(180, 401));
  assert.deepEqual(h.fired, []);
});

test("a touchend with no touch behind it is ignored rather than thrown on", () => {
  const h = harness();
  h.surface.fire("touchstart", startEvent(h.child, 300, 400));
  h.surface.fire("touchmove", moveEvent(180, 401));
  h.surface.fire("touchend", { changedTouches: [] });
  assert.deepEqual(h.fired, []);
});

// ---- 3. Where a swipe may not start ----------------------------------------

test("a touch that starts in the composer is typing, not a swipe", () => {
  const h = harness();
  const composer = makeNode({ selector: "#composer" });
  composer.parentNode = h.surface;
  const input = makeNode({ selector: "#composer-input" });
  input.parentNode = composer;
  // Started on the input INSIDE the composer: the walk has to go up.
  swipe(h, 300, 400, 180, 400, input);
  assert.deepEqual(h.fired, []);
});

test("a touch that starts on the team rail belongs to the rail", () => {
  const h = harness();
  const rail = makeNode({ selector: "#team-rail" });
  rail.parentNode = h.surface;
  swipe(h, 300, 400, 180, 400, rail);
  assert.deepEqual(h.fired, [], "the rail already owns tap and drag");
});

test("a code block that scrolls sideways keeps its own gesture", () => {
  // The case that matters on a phone: swiping a long line of code is how the
  // rest of it is read. Measured, not matched by class, so a wide table is
  // covered by the same rule.
  const h = harness();
  const pre = makeNode({ selector: ".xb-md-pre", scrollWidth: 900, clientWidth: 320 });
  pre.parentNode = h.surface;
  const code = makeNode({ selector: ".xb-md-codeblock" });
  code.parentNode = pre;
  swipe(h, 300, 400, 180, 400, code);
  assert.deepEqual(h.fired, []);

  // A code block whose content FITS does not overflow, so it does not block —
  // otherwise the assertion above would be passing because every block blocks.
  const fits = harness();
  const narrow = makeNode({ selector: ".xb-md-pre", scrollWidth: 300, clientWidth: 320 });
  narrow.parentNode = fits.surface;
  swipe(fits, 300, 400, 180, 400, narrow);
  assert.deepEqual(fits.fired, ["next"]);
});

test("the surface's own scrolling does not disqualify everything inside it", () => {
  // The walk stops BEFORE the surface. A scroller that reported horizontal
  // overflow for any reason would otherwise turn the whole feature off, silently.
  const surface = makeNode({ selector: "#chat-scroll", scrollWidth: 900, clientWidth: 320 });
  const child = makeNode({ selector: ".xb-msg-bubble" });
  child.parentNode = surface;
  const fired = [];
  createSwipeNavigator({ surfaceEl: surface, onSwipe: (d) => fired.push(d) }).attach();
  surface.fire("touchstart", startEvent(child, 300, 400));
  surface.fire("touchmove", moveEvent(180, 400));
  surface.fire("touchend", endEvent(180, 400));
  assert.deepEqual(fired, ["next"]);
});

test("a blocked start is not reconsidered when the finger leaves the region", () => {
  const h = harness();
  const rail = makeNode({ selector: "#team-rail" });
  rail.parentNode = h.surface;
  h.surface.fire("touchstart", startEvent(rail, 300, 400));
  h.surface.fire("touchmove", moveEvent(180, 400));
  h.surface.fire("touchmove", moveEvent(100, 400));
  h.surface.fire("touchend", endEvent(100, 400));
  assert.deepEqual(h.fired, []);
});

// ---- 4. Desktop is untouched ------------------------------------------------

test("only touch events are bound — no mouse, no wheel, no pointer", () => {
  const h = harness();
  assert.deepEqual(
    h.surface.bound.map((b) => b.type).sort(),
    ["touchcancel", "touchend", "touchmove", "touchstart"],
  );
  for (const banned of ["mousedown", "mousemove", "mouseup", "wheel", "pointerdown", "dragstart"]) {
    assert.ok(
      !h.surface.bound.some((b) => b.type === banned),
      `swipe_nav must not bind ${banned} — a trackpad flick would switch team`,
    );
  }
});

test("the CODE names no mouse, wheel or pointer API at all", () => {
  // A source scan as well as a behavioural one: the risk is somebody "adding
  // desktop support" later, and the behavioural test only sees what attach binds.
  //
  // Comments stripped first, unlike the chat-core portability gate. That gate
  // bans a token whose mere presence is a dependency; this one bans an event
  // BINDING, and the docstring has to be free to say which events were rejected
  // and why. A rule the explanation of itself would break is a rule that gets
  // deleted along with the explanation.
  for (const banned of ["mousedown", "mousemove", "mouseup", "wheel", "pointerdown", "pointermove"]) {
    assert.ok(
      !CODE.includes(banned),
      `swipe_nav.js names ${banned} — this is a touch gesture, and the desktop surfaces share this file`,
    );
  }
  // ...and the scan is not vacuous: the events it DOES bind are in there.
  for (const bound of ["touchstart", "touchmove", "touchend", "touchcancel"]) {
    assert.ok(CODE.includes(bound), `the scan cannot see the code — ${bound} is missing from it`);
  }
});

// ---- 5. It cannot block a scroll -------------------------------------------

test("every listener is registered PASSIVE", () => {
  const h = harness();
  for (const b of h.surface.bound) {
    assert.ok(b.options && b.options.passive === true, `${b.type} is not passive`);
  }
});

test("the code calls preventDefault nowhere — a passive listener that did would warn", () => {
  assert.ok(
    !CODE.includes("preventDefault"),
    "a passive listener cannot preventDefault; calling it logs a console error and does nothing",
  );
});

// ---- 6. Lifecycle -----------------------------------------------------------

test("detach removes every listener, and a swipe afterwards fires nothing", () => {
  const h = harness();
  h.nav.detach();
  assert.deepEqual(h.surface.bound, []);
  swipe(h, 300, 400, 180, 400);
  assert.deepEqual(h.fired, []);
});

test("attaching twice binds the same four functions, so a re-boot cannot double-fire", () => {
  // addEventListener de-duplicates an identical (type, listener, capture) triple,
  // which is what makes chat.js's re-boot safe. The stub records rather than
  // de-duplicates, so what is asserted is the property that matters: the SAME
  // function object is handed over both times.
  const h = harness();
  const first = h.surface.bound.map((b) => b.fn);
  h.nav.attach();
  const second = h.surface.bound.slice(4).map((b) => b.fn);
  assert.deepEqual(second, first, "a second attach must reuse the same handlers");
});

test("createSwipeNavigator refuses to build without a surface", () => {
  assert.throws(() => createSwipeNavigator({}), /opts\.surfaceEl/);
  assert.throws(() => createSwipeNavigator({ surfaceEl: {} }), /opts\.surfaceEl/);
});

test("a navigator with no onSwipe is inert rather than a crash on the first swipe", () => {
  const surface = makeNode({ selector: "#chat-scroll" });
  const child = makeNode();
  child.parentNode = surface;
  const nav = createSwipeNavigator({ surfaceEl: surface });
  nav.attach();
  surface.fire("touchstart", startEvent(child, 300, 400));
  surface.fire("touchmove", moveEvent(180, 400));
  surface.fire("touchend", endEvent(180, 400));
});

test("the thresholds are exported, so a surface can read them rather than guess", () => {
  assert.ok(DEFAULT_MIN_DISTANCE_PX > DEFAULT_SLOP_PX, "the swipe bar is above the noise floor");
  assert.ok(DEFAULT_DOMINANCE > 1, "1.0 would claim every diagonal flick");
});

// ---- 7. Portability ---------------------------------------------------------

test("swipe_nav.js reaches for no browser global and no extension API", () => {
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const token of ["chrome.", "browser.", "window.", "document.", "localStorage", "fetch("]) {
    assert.ok(!code.includes(token), `swipe_nav.js references ${token}`);
  }
});

test("english-only: no accented Latin chars in swipe_nav.js", () => {
  const hits = SOURCE.match(/[À-ÿ]/g) || [];
  assert.equal(hits.length, 0, `found ${JSON.stringify([...new Set(hits)])}`);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

/**
 * Horizontal swipe over the message area, for switching teams on a phone.
 *
 * WHAT THIS FILE IS NOT. It does not know what a team is, it holds no order and
 * it performs no switch. It recognises one gesture and calls back with a
 * direction; `team_rail.js` owns which team is next and what switching means.
 * That split is the point — the rail sits directly above the thread, so a swipe
 * that computed its own order would disagree with the squares the reader can
 * see, and there is no way to notice that from either side alone.
 *
 * TOUCH ONLY. No mouse handler, no wheel handler, no pointer events. A trackpad
 * emits horizontal wheel deltas whenever a laptop user brushes it sideways, and
 * a mouse drag is how text gets selected; binding either would make the desktop
 * surfaces change team by accident. `touchstart`/`touchmove`/`touchend` are the
 * only three events this module knows about, and a device without them simply
 * never fires the callback.
 *
 * IT MUST NOT FIGHT THE SCROLL, which is the failure that makes gestures like
 * this hated. Two mechanisms, both structural:
 *
 *   1. every listener is PASSIVE. This module cannot call preventDefault even
 *      by accident, so the browser's own vertical scrolling is never blocked,
 *      never janky, and never waiting on this code to decide something;
 *   2. the gesture is decided ONCE per touch. The first movement that clears
 *      `slopPx` settles it: if that movement was more vertical than horizontal,
 *      the touch is abandoned for good and no amount of sideways drift later in
 *      the same gesture can claim it back.
 *
 * WHERE A SWIPE MAY NOT START. A touch that begins in the composer, on the rail
 * itself, or inside anything that scrolls sideways (a fenced code block is the
 * one that matters) is not a team switch — it is typing, tapping a square, or
 * reading a long line. Those are refused at `touchstart` and never reconsidered.
 *
 * Used by:
 *   - app-site/app/chat.js (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_core_swipe_nav.mjs
 */

/**
 * How far a finger must travel before the gesture is a swipe at all.
 *
 * Below this it is a tap, or the hand shifting while reading. Deliberately
 * generous: a team switch tears down a socket subscription and reloads a thread,
 * so the cost of firing one nobody asked for is much higher than the cost of
 * making somebody swipe again.
 */
export const DEFAULT_MIN_DISTANCE_PX = 64;

/**
 * How much more horizontal than vertical the movement has to be.
 *
 * 1.0 would mean "anything past 45 degrees", which on a thread you are scrolling
 * through is most diagonal flicks. 1.6 asks for a movement that is clearly
 * sideways and leaves the diagonal band to the scroller, where it belongs.
 */
export const DEFAULT_DOMINANCE = 1.6;

/**
 * Movement below this decides nothing — it is the noise a finger makes on
 * contact. The first sample past it is what settles horizontal versus vertical.
 */
export const DEFAULT_SLOP_PX = 10;

/**
 * Watch `surfaceEl` for a horizontal swipe.
 *
 * @param {{
 *   surfaceEl: EventTarget & {contains?: Function},
 *   onSwipe: (direction: "next"|"previous") => void,
 *   blockSelectors?: Array<string>,
 *   minDistancePx?: number,
 *   dominance?: number,
 *   slopPx?: number
 * }} opts
 *   surfaceEl       — the scroller the gesture is read over
 *   onSwipe         — "next" for a swipe LEFT (the content moves left, so the
 *                     reader is going right along the rail), "previous" for the
 *                     other way. The same direction the platform uses everywhere.
 *   blockSelectors  — CSS selectors a touch must not start inside. The surface
 *                     names its own, because a composer's id is not this file's
 *                     business.
 * @returns {{attach: () => void, detach: () => void}}
 */
export function createSwipeNavigator(opts) {
  const cfg = opts || {};
  const surfaceEl = cfg.surfaceEl;
  if (!surfaceEl || typeof surfaceEl.addEventListener !== "function") {
    throw new TypeError("createSwipeNavigator requires opts.surfaceEl");
  }
  const onSwipe = typeof cfg.onSwipe === "function" ? cfg.onSwipe : () => {};
  const blockSelectors = Array.isArray(cfg.blockSelectors) ? cfg.blockSelectors : [];
  const minDistance = Number(cfg.minDistancePx) || DEFAULT_MIN_DISTANCE_PX;
  const dominance = Number(cfg.dominance) || DEFAULT_DOMINANCE;
  const slop = Number(cfg.slopPx) || DEFAULT_SLOP_PX;

  /**
   * The touch in progress.
   *
   * `claimed` is a three-state on purpose: null means "not decided yet", true
   * means this is a swipe, false means this gesture has been given to the
   * scroller and is not coming back.
   */
  let start = null;

  /**
   * May a touch that landed on `node` become a swipe?
   *
   * Walks up to (but not including) the surface, so the surface's own vertical
   * scrolling never disqualifies everything inside it.
   */
  function startsInBlockedRegion(node) {
    let el = node;
    while (el && el !== surfaceEl) {
      if (typeof el.matches === "function") {
        for (const selector of blockSelectors) {
          if (el.matches(selector)) return true;
        }
      }
      // ANYTHING THAT SCROLLS SIDEWAYS keeps its own gesture. A fenced code
      // block with a long line is the case that matters: swiping it is how the
      // rest of the line is read, and stealing that would make code unreadable
      // on the surface where it is hardest to read already. Measured rather than
      // matched by class, so a table or an overflowing image is covered too.
      if (typeof el.scrollWidth === "number" && typeof el.clientWidth === "number") {
        if (el.scrollWidth > el.clientWidth) return true;
      }
      el = el.parentNode || null;
    }
    return false;
  }

  function onTouchStart(event) {
    const touches = event.touches || [];
    // A second finger is a pinch or a zoom, never a team switch. Abandoned
    // rather than ignored, so lifting back to one finger cannot resume it.
    if (touches.length !== 1) {
      start = null;
      return;
    }
    const touch = touches[0];
    if (startsInBlockedRegion(event.target)) {
      start = null;
      return;
    }
    start = { x: touch.clientX, y: touch.clientY, claimed: null };
  }

  function onTouchMove(event) {
    if (!start) return;
    const touches = event.touches || [];
    if (touches.length !== 1) {
      start = null; // a finger joined mid-gesture
      return;
    }
    const dx = touches[0].clientX - start.x;
    const dy = touches[0].clientY - start.y;
    // ONLY WHILE UNDECIDED. This condition is the "cannot be claimed back" rule
    // in its entirety: once `claimed` holds a boolean, no later sample can
    // rewrite it, so a gesture handed to the scroller stays handed over however
    // sideways the finger drifts afterwards. There is deliberately no second
    // early-return for the decided-vertical case — it would be an unfalsifiable
    // branch, since this test already refuses to touch anything.
    if (start.claimed === null) {
      if (Math.abs(dx) < slop && Math.abs(dy) < slop) return; // still noise
      start.claimed = Math.abs(dx) > Math.abs(dy) * dominance;
    }
  }

  function onTouchEnd(event) {
    const pending = start;
    start = null;
    if (!pending || pending.claimed !== true) return;
    const touch = (event.changedTouches && event.changedTouches[0]) || null;
    if (!touch) return;
    const dx = touch.clientX - pending.x;
    const dy = touch.clientY - pending.y;
    // Re-checked at the END, not trusted from the moment it was claimed: a
    // gesture can start sideways and finish as a scroll, and the finger's
    // resting place is the only honest account of what was meant.
    if (Math.abs(dx) < minDistance) return;
    if (Math.abs(dx) <= Math.abs(dy) * dominance) return;
    onSwipe(dx < 0 ? "next" : "previous");
  }

  function onTouchCancel() {
    start = null;
  }

  // Passive, every one of them: this module must never be able to block a scroll.
  const PASSIVE = { passive: true };
  const BOUND = [
    ["touchstart", onTouchStart],
    ["touchmove", onTouchMove],
    ["touchend", onTouchEnd],
    ["touchcancel", onTouchCancel],
  ];

  return {
    attach() {
      for (const [type, fn] of BOUND) surfaceEl.addEventListener(type, fn, PASSIVE);
    },
    detach() {
      for (const [type, fn] of BOUND) surfaceEl.removeEventListener(type, fn, PASSIVE);
      start = null;
    },
  };
}

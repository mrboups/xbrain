/**
 * The on-screen keyboard, and the header it used to take away with it.
 *
 * THE BUG THIS FILE EXISTS FOR. Focus the composer on a phone and the header
 * scrolled off the top while the pill rode up the screen. Three separate
 * causes, all real:
 *
 *   1. the viewport meta carried no `interactive-widget` directive, so Chrome
 *      on Android OVERLAYS the keyboard on the viewport instead of resizing the
 *      content under it. That is fixed in index.html, not here.
 *   2. the shell was `height: 100dvh`. `dvh` tracks the browser's own chrome —
 *      the collapsing address bar — and is NOT updated by the virtual keyboard.
 *      The layout therefore keeps its full height, the focused field ends up
 *      under the keyboard, and the browser does the only thing left to it: it
 *      scrolls the PAGE to reveal the field, which takes the header out through
 *      the top.
 *   3. nothing listened to visualViewport. iOS Safari ignores the meta
 *      directive outright, so the VisualViewport API is the only way that
 *      surface can see the keyboard at all.
 *
 * So the meta answers Chrome and this module answers iOS — and Chrome gets a
 * second, more precise source of truth for free: the shell's height stops being
 * a guess about browser chrome and becomes a measurement of the part of the
 * page a person can actually see.
 *
 * WHY BOTH `resize` AND `scroll`. On Android the keyboard fires `resize`. On
 * iOS it slides the visual viewport over an unchanged layout viewport and the
 * event that arrives is `scroll` — a listener on `resize` alone sees nothing at
 * all on the platform that needs this most.
 *
 * WHY IT HANDS BACK A DETACH. The binding is per session, not per team, and a
 * leaked pair of listeners is invisible until it is not: both run on every
 * viewport change, of which a keyboard animation produces dozens.
 *
 * NOTHING here may ask for notification access or touch a push subscription
 * (D-27-05) — push.js owns the single click-gated call site.
 */

/**
 * The height app.css lays the shell out against.
 *
 * Unset is a supported state, not a broken one: the stylesheet's own
 * `var(--xb-viewport-height, 100dvh)` fallback is what a browser without the
 * VisualViewport API runs on.
 */
const HEIGHT_VAR = "--xb-viewport-height";

/**
 * How far the visible window sits BELOW the top of the layout viewport.
 *
 * app.css translates the shell by it, which is the difference between a column
 * that is the right size and one that is also in the right place. Unset means
 * zero, which is the answer on every platform that keeps the two viewports
 * flush — the variable earns its keep on exactly one, and that one is the phone.
 */
const OFFSET_TOP_VAR = "--xb-viewport-offset-top";

/**
 * Stamped on the root for as long as a keyboard covers part of the viewport.
 *
 * It buys one thing: the composer's bottom safe-area inset collapses while the
 * keyboard is up. That inset exists to clear the home indicator, and the
 * keyboard is already covering the home indicator — kept, it would be a strip
 * of dead space between the pill and the keys.
 */
const KEYBOARD_ATTR = "data-keyboard";

/**
 * Below this, a shrunken visual viewport is the address bar collapsing, a
 * toolbar appearing or a rounding difference — not a keyboard. A threshold is
 * the only honest way to tell: no browser reports "the keyboard is open".
 */
const KEYBOARD_MIN_PX = 120;

/** A pinch of zoom either side of 1 is measurement noise, not a gesture. */
const SCALE_EPSILON = 0.01;

/**
 * Everyone who wants to be told the visible window changed.
 *
 * WHY A SUBSCRIPTION AND NOT A CALL. This module measures; it does not know what
 * anything else should do about the measurement. The thread has to re-anchor
 * when the keyboard takes 300px away — but whether it may is a question about
 * where the reader is in the conversation, which is the chat's business and not
 * a viewport's. Reaching into the thread's own element from here would put the
 * two facts in the wrong module and make this file un-testable without a chat.
 * A test asserts that no element id of another surface appears below, comments
 * included: a gate that passes because prose was reworded is worse than none.
 *
 * Module-level rather than per-binding: a document has exactly one visual
 * viewport, and a subscriber is wired once for the session while the binding
 * itself can be taken down and put back by a re-boot.
 */
const listeners = new Set();

/**
 * Be told when the measured viewport changes.
 *
 * @param {(change: {height: number, previousHeight: number, offsetTop: number,
 *   keyboard: boolean}) => void} handler
 *   height         — what the shell is now laid out against
 *   previousHeight — what it was laid out against a moment ago. The difference
 *                    is what the message list just lost, which is the only way a
 *                    reader's position before the change can still be recovered.
 *   offsetTop      — how far the visible window sits below the layout viewport
 *   keyboard       — whether that loss is big enough to be a keyboard
 * @returns {() => void} unsubscribe. Always a function.
 */
export function onViewportChange(handler) {
  if (typeof handler !== "function") return () => {};
  listeners.add(handler);
  return () => listeners.delete(handler);
}

/** Tell the subscribers, and survive one of them throwing. */
function notify(change) {
  for (const handler of [...listeners]) {
    try {
      handler(change);
    } catch (e) {
      // The height IS the layout. A subscriber that throws must not take the
      // measurement down with it and freeze the shell at whatever the keyboard
      // left behind.
      console.warn("[xbrain] viewport subscriber failed:", e);
    }
  }
}

/**
 * Drive the shell's height from the visual viewport.
 *
 * @param {{view?: Object, root?: Object}} [refs]
 *   view — the window to measure. Injected so this is testable without a DOM.
 *   root — the element the height variable is written to (documentElement).
 * @returns {() => void} detach. Always a function, including on the platforms
 *   where there was nothing to attach, so a caller's teardown stays
 *   unconditional.
 */
export function bindViewport(refs = {}) {
  const view =
    refs.view || (typeof window === "undefined" ? null : window);
  const root =
    refs.root ||
    (typeof document === "undefined" ? null : document.documentElement);
  if (!view || !root) return () => {};

  const viewport = view.visualViewport;
  // No VisualViewport — an older iOS, or an embedded webview. The stylesheet's
  // 100dvh fallback runs, and on Chrome the meta directive still resizes the
  // content. Writing nothing is the correct outcome, not a degraded one.
  if (!viewport) return () => {};

  // What was last WRITTEN, so a change can be told from the dozens of events
  // that repeat it: iOS fires scroll continuously, and re-announcing the same
  // height would have every subscriber re-doing its work on every frame.
  let lastHeight = null;
  let lastOffsetTop = null;

  const measure = () => {
    // A pinch-zoomed visual viewport is a window onto the page, not a keyboard
    // measurement. Resizing the shell to it would shrink the app under the
    // reader's fingers and fight the gesture; the last honest height stays.
    const scale = typeof viewport.scale === "number" ? viewport.scale : 1;
    if (Math.abs(scale - 1) > SCALE_EPSILON) return;

    const height = Math.round(viewport.height || 0);
    if (height <= 0) return;
    root.style.setProperty(HEIGHT_VAR, `${height}px`);

    // WHERE the visible window is, which is a different question from how tall
    // it is — and the one that was never asked. The visual viewport is not
    // always flush with the top of the layout viewport: when the keyboard opens
    // over a document that CANNOT scroll (this one: body is overflow:hidden and
    // exactly viewport-tall) iOS still has to bring the focused field into view,
    // and the only move left to it is to slide the visible window down inside
    // the layout viewport. The shell then paints from the top of the LAYOUT
    // viewport, its last `offsetTop` pixels fall below the visible window, and
    // what is left in their place, between the composer and the keys, is a band
    // of bare canvas.
    const offsetTop = Math.max(0, Math.round(viewport.offsetTop || 0));
    root.style.setProperty(OFFSET_TOP_VAR, `${offsetTop}px`);

    const covered = Math.round((view.innerHeight || height) - height);
    if (covered >= KEYBOARD_MIN_PX) root.setAttribute(KEYBOARD_ATTR, "open");
    else root.removeAttribute(KEYBOARD_ATTR);

    // The shell is now exactly as tall as what can be seen, so the page has
    // nothing left to scroll. If it is scrolled anyway, that IS the bug this
    // file exists for: iOS scrolled the layout viewport to reveal the focused
    // field and took the header with it. Put it back.
    //
    // Not the same correction as the offset above, and neither replaces the
    // other: scrollY is the document's position inside the layout viewport,
    // offsetTop is the visible window's position inside that same layout
    // viewport. A page pinned at scrollY 0 can still be looked at through a
    // window that has moved.
    if (view.scrollY > 0 && typeof view.scrollTo === "function") {
      view.scrollTo(0, 0);
    }

    if (height === lastHeight && offsetTop === lastOffsetTop) return;
    // First measurement: nothing has moved yet, so the change is zero-sized.
    // A subscriber deriving "how much did the list just lose" from it must get
    // 0 here rather than the whole height.
    const previousHeight = lastHeight === null ? height : lastHeight;
    lastHeight = height;
    lastOffsetTop = offsetTop;
    // AFTER the style is written and the page is back at the top: a subscriber
    // that re-measures its own scroller must see the layout this change
    // produced, not the one it replaced.
    notify({
      height,
      previousHeight,
      offsetTop,
      keyboard: covered >= KEYBOARD_MIN_PX,
    });
  };

  measure();
  viewport.addEventListener("resize", measure);
  viewport.addEventListener("scroll", measure);

  return () => {
    viewport.removeEventListener("resize", measure);
    viewport.removeEventListener("scroll", measure);
    // Back to the stylesheet's fallback rather than to a stale pixel count: a
    // detached binding must not leave the shell frozen at whatever height the
    // keyboard happened to leave behind — nor translated down by an offset
    // nothing is measuring any more, which would strand the header off screen.
    root.style.removeProperty(HEIGHT_VAR);
    root.style.removeProperty(OFFSET_TOP_VAR);
    root.removeAttribute(KEYBOARD_ATTR);
  };
}

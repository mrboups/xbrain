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
 * WHY IT KEEPS ASKING AFTER THE LAST EVENT. The events stop before the keyboard
 * does. iOS reports the viewport several times while the keys slide in and the
 * final one of those readings is from the middle of the animation — no event
 * announces the end. A handler that trusts the last event it received lays the
 * shell out against a viewport that has since moved, and the difference shows as
 * a strip of page background between the composer and the keys. See
 * SETTLE_MAX_FRAMES: the fix is to keep reading until the geometry stops
 * changing, never to add a constant that cancels the error on one device.
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
 * How long the settle may keep re-reading the viewport after an event.
 *
 * THE BUG THESE EXIST FOR. With the keyboard open there was a band of page
 * background between the bottom of the composer and the keyboard's accessory
 * bar — and scrolling made it correct itself. That self-correction is the
 * diagnosis: the geometry this module settles on after a stray event is right,
 * so the one it took when the keyboard opened was not. iOS animates the keys in
 * over roughly a quarter of a second, reports the viewport as it goes, and stops
 * reporting before the animation ends. The last event of the sequence carries a
 * frame from the middle of the slide, and nothing arrives to correct it.
 *
 * `visualViewport.height` is a live reading, not a snapshot handed to the event,
 * so a frame loop can see the final geometry whether or not anything announces
 * it. It ends when two consecutive frames agree — a viewport that has not moved
 * for a frame has finished moving — which is a measurement of the animation
 * rather than an assumption about how long it lasts.
 *
 * These two numbers bound the WORK, not the measurement: a viewport that never
 * settles (a page in constant motion, a device mid-rotation) must not leave a
 * loop running for the session. Neither may ever become a pixel constant added
 * to a height — an offset that happens to cancel the error on one phone is how
 * this reappears on the next. 40 frames is ~660ms at 60Hz and ~330ms at 120Hz,
 * both past the end of an iOS keyboard animation; the deadline is what holds
 * when the frames are slow rather than merely many.
 */
const SETTLE_MAX_FRAMES = 40;
const SETTLE_MAX_MS = 700;

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

  // The frame clock, taken from the injected window so the settle can be driven
  // by a test rather than described by one. Absent — an environment with a
  // visual viewport but no rAF — costs the settle and nothing else: the module
  // goes on measuring exactly as it did before, which is the behaviour that had
  // the band in it, not a new failure.
  const requestFrame =
    typeof view.requestAnimationFrame === "function"
      ? (fn) => view.requestAnimationFrame(fn)
      : null;
  const cancelFrame =
    typeof view.cancelAnimationFrame === "function"
      ? (id) => view.cancelAnimationFrame(id)
      : null;

  // What was last WRITTEN, so a change can be told from the dozens of events
  // that repeat it: iOS fires scroll continuously, and re-announcing the same
  // height would have every subscriber re-doing its work on every frame.
  let lastHeight = null;
  let lastOffsetTop = null;

  // The settle's own state. `settleLast` is the reading the previous frame took
  // — the other half of the agreement that ends the loop — and is deliberately
  // NOT `lastHeight`: one answers "has the animation stopped", the other "is
  // this news to a subscriber".
  let frame = null;
  let framesLeft = 0;
  let deadline = 0;
  let settleLast = null;
  let released = false;

  /**
   * The geometry as the browser reports it right now, or null when this module
   * must keep its hands off the layout.
   *
   * A pinch-zoomed visual viewport is a window onto the page, not a keyboard
   * measurement. Resizing the shell to it would shrink the app under the
   * reader's fingers and fight the gesture; the last honest height stays.
   */
  const read = () => {
    const scale = typeof viewport.scale === "number" ? viewport.scale : 1;
    if (Math.abs(scale - 1) > SCALE_EPSILON) return null;
    const height = Math.round(viewport.height || 0);
    if (height <= 0) return null;
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
    return { height, offsetTop };
  };

  /** Lay the shell out against a reading, and tell whoever is listening. */
  const apply = ({ height, offsetTop }) => {
    root.style.setProperty(HEIGHT_VAR, `${height}px`);
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
    //
    // Checked on every settle frame too, not only on an event: the scroll iOS
    // performs to reveal the focused field is not always announced by one.
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

  /** One frame of the settle: ask again, and stop once the answer holds. */
  const step = () => {
    frame = null;
    if (released) return;
    const reading = read();
    // A gesture started mid-settle. Stand down exactly as an event would, and
    // do not keep a loop running that would fight it sixty times a second.
    if (!reading) {
      settleLast = null;
      return;
    }
    const settled =
      settleLast !== null &&
      settleLast.height === reading.height &&
      settleLast.offsetTop === reading.offsetTop;
    settleLast = reading;
    apply(reading);
    if (settled) return;
    if (--framesLeft <= 0) return;
    if (Date.now() >= deadline) return;
    frame = requestFrame(step);
  };

  /** Start the settle, or push its budget back out for a fresh event. */
  const armSettle = () => {
    if (!requestFrame || released) return;
    framesLeft = SETTLE_MAX_FRAMES;
    deadline = Date.now() + SETTLE_MAX_MS;
    // A new event invalidates the pair being compared: the reading it carried
    // may be from the middle of an animation, and letting it stand as half of an
    // agreement would end the settle on precisely the value that is wrong.
    settleLast = null;
    // Re-arming an in-flight loop rather than queueing a second one — two loops
    // would read the same viewport twice a frame for the rest of the animation.
    if (frame === null) frame = requestFrame(step);
  };

  const measure = () => {
    const reading = read();
    if (!reading) return;
    // Applied now rather than at the end of the settle: the shell has to follow
    // the keyboard while it moves, or the composer spends the whole animation
    // behind it. The settle exists to correct the LAST of these readings — the
    // one no event ever arrives to correct.
    apply(reading);
    armSettle();
  };

  measure();
  viewport.addEventListener("resize", measure);
  viewport.addEventListener("scroll", measure);

  return () => {
    // Before the listeners come off: a frame already queued would otherwise run
    // after teardown and write a height back onto a shell that was just handed
    // to the stylesheet's fallback.
    released = true;
    if (frame !== null && cancelFrame) cancelFrame(frame);
    frame = null;
    settleLast = null;
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

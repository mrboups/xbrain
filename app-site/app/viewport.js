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

  const measure = () => {
    // A pinch-zoomed visual viewport is a window onto the page, not a keyboard
    // measurement. Resizing the shell to it would shrink the app under the
    // reader's fingers and fight the gesture; the last honest height stays.
    const scale = typeof viewport.scale === "number" ? viewport.scale : 1;
    if (Math.abs(scale - 1) > SCALE_EPSILON) return;

    const height = Math.round(viewport.height || 0);
    if (height <= 0) return;
    root.style.setProperty(HEIGHT_VAR, `${height}px`);

    const covered = Math.round((view.innerHeight || height) - height);
    if (covered >= KEYBOARD_MIN_PX) root.setAttribute(KEYBOARD_ATTR, "open");
    else root.removeAttribute(KEYBOARD_ATTR);

    // The shell is now exactly as tall as what can be seen, so the page has
    // nothing left to scroll. If it is scrolled anyway, that IS the bug this
    // file exists for: iOS scrolled the layout viewport to reveal the focused
    // field and took the header with it. Put it back.
    if (view.scrollY > 0 && typeof view.scrollTo === "function") {
      view.scrollTo(0, 0);
    }
  };

  measure();
  viewport.addEventListener("resize", measure);
  viewport.addEventListener("scroll", measure);

  return () => {
    viewport.removeEventListener("resize", measure);
    viewport.removeEventListener("scroll", measure);
    // Back to the stylesheet's fallback rather than to a stale pixel count: a
    // detached binding must not leave the shell frozen at whatever height the
    // keyboard happened to leave behind.
    root.style.removeProperty(HEIGHT_VAR);
    root.removeAttribute(KEYBOARD_ATTR);
  };
}

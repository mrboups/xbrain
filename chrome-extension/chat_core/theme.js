/**
 * xbrain — theme.js (Plan 20-01, shadcn "Neutral" theme runtime).
 *
 * Pure, dependency-free ES module: NO browser-extension API, NO DOM globals.
 * The caller injects the storage value and the root element, exactly like
 * settings.js takes its storage area as an argument. That keeps this module
 * node-testable.
 *
 * Responsibilities:
 *   - resolveInitialTheme: decide 'light' vs 'dark' on boot. A previously
 *     stored choice wins over the OS preference; an absent/invalid stored
 *     value falls back to prefers-color-scheme.
 *   - applyTheme: stamp `data-theme` on the root element. The stylesheet defines
 *     the token overrides for :root[data-theme="dark"] / :root[data-theme="light"],
 *     which out-specify the prefers-color-scheme media block so the toggle wins
 *     in BOTH directions.
 *
 * Persistence is done by the surface, through its platform storage shim, under
 * THEME_STORAGE_KEY.
 *
 * Used by:
 *   - chrome-extension/popup.js + options.js (boot + toggle wiring)
 *   - app-site/app/ — the PWA (same key, web storage shim)
 *   - chrome-extension/tests/test_theme.mjs
 */

/** Storage key for the persisted theme choice (platform storage shim). */
export const THEME_STORAGE_KEY = "xbrain_theme_v1";

/** The only two valid theme modes. */
const VALID_MODES = ["light", "dark"];

/**
 * Resolve the theme to apply on boot.
 *
 * Security (T-20-01-01): only an exact 'dark' or 'light' stored value is
 * honored; any other value (null, "", "garbage", numbers, objects) is ignored
 * and we fall back to the OS preference. No untrusted string ever reaches the
 * DOM beyond the fixed data-theme attribute set by applyTheme.
 *
 * @param {{storedTheme: unknown, prefersDark: boolean}} input
 * @returns {"light"|"dark"}
 */
export function resolveInitialTheme({ storedTheme, prefersDark } = {}) {
  if (VALID_MODES.includes(storedTheme)) {
    return storedTheme;
  }
  return prefersDark ? "dark" : "light";
}

/**
 * Stamp the theme onto a root element (document.documentElement in the popup).
 * Pure w.r.t. globals — the caller passes the element in.
 *
 * @param {{setAttribute: (k: string, v: string) => void}} rootEl
 * @param {"light"|"dark"} mode
 * @returns {"light"|"dark"} the mode applied (for chaining/persistence)
 */
export function applyTheme(rootEl, mode) {
  rootEl.setAttribute("data-theme", mode);
  return mode;
}

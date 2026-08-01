/**
 * The platform shim contract (Phase 27, D-27-04).
 *
 * chat-core NEVER touches a browser-extension API, localStorage, window.open or
 * Notification directly — it goes through one of these objects, which each
 * surface supplies:
 *   chrome-extension/platform_chrome  — extension storage / tab / notification APIs
 *   app-site/app/platform_web         — localStorage / window.open / SW showNotification
 * (module paths written without their .js suffix on purpose: the portability
 * gate greps these files for a literal browser-namespace token, comments
 * included, and a filename would be indistinguishable from a real dependency)
 *
 * This module holds the CONTRACT only: the member list and a validator. It has
 * no implementation and no imports, so it stays byte-identical on both surfaces.
 *
 * @typedef {Object} PlatformStorage
 * @property {(keys: string[]) => Promise<Object>} get
 * @property {(obj: Object) => Promise<void>} set
 * @property {(keys: string[]) => Promise<void>} remove
 *
 * @typedef {Object} Platform
 * @property {PlatformStorage} storage
 * @property {(url: string) => void} openUrl
 * @property {(opts: {title: string, message: string, iconUrl?: string}) => Promise<string|null>} notify
 * @property {() => Promise<string|null>} currentPageUrl
 */

/**
 * Every top-level member a platform implementation must provide.
 *
 * `currentPageUrl` is the one capability the two surfaces genuinely do not
 * share. An extension can read the page the user is looking at, so "send this
 * to a teammate" needs no typing; a web page cannot see anything but itself, so
 * the honest answer there is null and the person pastes a link. It is a member
 * of the contract rather than an optional extra precisely so the null case has
 * to be handled: a surface that quietly skipped it would ship a "send the
 * current page" button that sends nothing.
 */
export const PLATFORM_MEMBERS = ["storage", "openUrl", "notify", "currentPageUrl"];

/** Every function `platform.storage` must provide. */
export const PLATFORM_STORAGE_MEMBERS = ["get", "set", "remove"];

/**
 * Throw a TypeError naming the FIRST missing member of a platform shim.
 *
 * Called at module load by each surface's implementation (T-27-01-04): a
 * partially-implemented platform must fail loudly at import time rather than
 * silently no-op a notification or a token read at first use.
 *
 * @param {unknown} p the candidate platform implementation
 * @returns {Platform} the same object, so callers can `export const x = assertPlatform({...})`
 * @throws {TypeError} naming the missing path, e.g. "platform.storage.remove is not a function"
 */
export function assertPlatform(p) {
  if (!p || typeof p !== "object") {
    throw new TypeError("platform is not an object");
  }
  for (const member of PLATFORM_MEMBERS) {
    if (p[member] === undefined || p[member] === null) {
      throw new TypeError(`platform.${member} is missing`);
    }
  }
  if (typeof p.storage !== "object") {
    throw new TypeError("platform.storage is not an object");
  }
  for (const fn of PLATFORM_STORAGE_MEMBERS) {
    if (typeof p.storage[fn] !== "function") {
      throw new TypeError(`platform.storage.${fn} is not a function`);
    }
  }
  if (typeof p.openUrl !== "function") {
    throw new TypeError("platform.openUrl is not a function");
  }
  if (typeof p.notify !== "function") {
    throw new TypeError("platform.notify is not a function");
  }
  if (typeof p.currentPageUrl !== "function") {
    throw new TypeError("platform.currentPageUrl is not a function");
  }
  return p;
}

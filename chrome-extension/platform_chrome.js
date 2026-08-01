/**
 * The Chrome extension's implementation of the chat-core platform shim
 * (Phase 27, D-27-04).
 *
 * chat-core is surface-agnostic: it never touches an extension API directly.
 * Every capability it needs — persistent storage, opening a URL, raising a
 * notification — crosses into this file, which is the only place in the
 * extension half of the chat that knows it is running inside an extension.
 * app-site/app/platform_web.js is the PWA's counterpart (localStorage,
 * window.open, ServiceWorkerRegistration.showNotification).
 *
 * The contract is asserted at module load (see the bottom of this file), so a
 * half-implemented shim fails loudly at import instead of silently no-opping a
 * token read or a notification at first use (T-27-01-04).
 */

import { assertPlatform } from "./chat_core/platform.js";
import { isSafeHttpUrl } from "./chat_core/nudge_open.js";
import { notificationsEnabled } from "./settings.js";

/** @type {import("./chat_core/platform.js").Platform} */
export const chromePlatform = {
  // The extension's LOCAL storage area — the same one that holds xbt_token,
  // the theme choice and the team order. Deliberately not the sync area:
  // credentials must not ride a profile sync.
  storage: {
    get: (keys) => chrome.storage.local.get(keys),
    set: (obj) => chrome.storage.local.set(obj),
    remove: (keys) => chrome.storage.local.remove(keys),
  },

  /**
   * Open a URL in a new tab — but only after re-validating the scheme.
   *
   * The check is repeated AT THE POINT OF ACTION (the Phase-22 rule, mirrored
   * from popup.js's openDirect): a URL is never trusted just because it passed
   * a check upstream, so a javascript:/data: URL cannot reach the browser even
   * if some caller forgot to validate (T-27-01-02).
   *
   * @param {string} url
   */
  openUrl: (url) => {
    if (!isSafeHttpUrl(url)) return;
    chrome.tabs.create({ url });
  },

  /**
   * Raise an OS notification, promise-wrapped so chat-core can await an id.
   *
   * Gated on the user's showNotifications setting. Returning null when it is off
   * is not a silent failure — it is the contract chat-core already handles:
   * `handleOpenUrl` falls back to in-page UI when no notification id comes back,
   * so a muted teammate still SEES the link request, just not as a popup.
   *
   * @param {{title: string, message: string, iconUrl?: string, type?: string, priority?: number}} opts
   * @returns {Promise<string|null>} the notification id, or null when muted
   */
  notify: async (opts) => {
    if (!(await notificationsEnabled(chrome.storage.sync))) return null;
    return new Promise((resolve) =>
      chrome.notifications.create(
        { type: "basic", iconUrl: "icon128.png", ...opts },
        resolve,
      ),
    );
  },
};

// Fail at import, not at first use.
assertPlatform(chromePlatform);

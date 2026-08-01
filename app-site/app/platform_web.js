/**
 * The PWA's implementation of the chat-core platform shim (Phase 27, D-27-04).
 *
 * The browser twin of chrome-extension/platform_chrome.js. chat-core never
 * touches storage, a URL opener or a notification directly — every capability
 * crosses into this file, which is the only place in the web half of the chat
 * that knows it is running in a plain page.
 *
 * The contract is asserted at module load (bottom of this file), so a
 * half-implemented shim fails loudly at import instead of silently no-opping a
 * token read at first use.
 */

import { assertPlatform } from "./chat_core/platform.js";
import { isSafeHttpUrl } from "./chat_core/nudge_open.js";

/**
 * localStorage throws — it is not merely empty — in a storage-partitioned
 * context, in Safari private mode when the quota is exhausted, and whenever the
 * user has blocked site data. An unguarded read would take down the whole boot
 * path, so every access is wrapped and a failure degrades to "no value stored":
 * the app then shows its sign-in card instead of a white screen.
 */
function safeLocalStorage() {
  try {
    return window.localStorage || null;
  } catch (e) {
    return null;
  }
}

/** @type {import("./chat_core/platform.js").Platform} */
export const webPlatform = {
  /**
   * localStorage is synchronous, but the shim contract is async so both
   * surfaces look identical to chat-core.
   *
   * This shim is deliberately KEY-AGNOSTIC: callers pass the key names in. The
   * canonical app-site key names live in auth.js, which is the module that owns
   * what "signed in" means (D-27-02).
   */
  storage: {
    async get(keys) {
      const out = {};
      const store = safeLocalStorage();
      if (!store) return out;
      const list = Array.isArray(keys) ? keys : [keys];
      for (const key of list) {
        try {
          const value = store.getItem(key);
          if (value !== null) out[key] = value;
        } catch (e) {
          // Skip this key; a partial read beats an exception mid-boot.
        }
      }
      return out;
    },

    async set(obj) {
      const store = safeLocalStorage();
      if (!store) return;
      for (const [key, value] of Object.entries(obj || {})) {
        try {
          store.setItem(key, value);
        } catch (e) {
          // Quota exceeded or storage blocked — nothing useful to do here, and
          // throwing would abort a sign-in that otherwise succeeded.
        }
      }
    },

    async remove(keys) {
      const store = safeLocalStorage();
      if (!store) return;
      const list = Array.isArray(keys) ? keys : [keys];
      for (const key of list) {
        try {
          store.removeItem(key);
        } catch (e) {
          // Same reasoning as set().
        }
      }
    },
  },

  /**
   * Open a URL in a new tab — but only after re-validating the scheme.
   *
   * The check is repeated AT THE POINT OF ACTION (the Phase-22 rule, mirrored
   * from the extension's shim): a URL is never trusted just because it passed a
   * check upstream, so a javascript:/data: URL cannot reach the browser even if
   * some caller forgot to validate. `noopener,noreferrer` stops the opened page
   * from reaching back through window.opener.
   *
   * @param {string} url
   */
  openUrl(url) {
    if (!isSafeHttpUrl(url)) return;
    window.open(url, "_blank", "noopener,noreferrer");
  },

  /**
   * In-page notification fallback ONLY.
   *
   * Real push notifications are raised by the service worker (sw.js) from a
   * push event. This path exists for events that arrive while the tab is open —
   * an `open_url` nudge landing on a focused tab, for instance.
   *
   * It NEVER asks the browser for notification access. D-27-05 puts that prompt
   * behind an explicit click, and this function is reachable without one; plan
   * 27-07 owns the single click-gated call site. If access has not already been
   * granted this returns null and the caller falls back to in-page UI.
   *
   * @param {{title: string, message: string, iconUrl?: string}} opts
   * @returns {Promise<string|null>} a notification tag when shown, else null
   */
  async notify({ title, message, iconUrl } = {}) {
    if (typeof Notification === "undefined") return null;
    if (Notification.permission !== "granted") return null;

    const tag = `xb-${Date.now()}`;
    const options = {
      body: message,
      tag,
      icon: iconUrl || "/app/icons/icon-192.png",
    };

    try {
      // Prefer the service worker's registration: on Android a page-constructed
      // Notification is not allowed, and the SW path is the only one that works
      // in both places.
      const registration =
        navigator.serviceWorker && navigator.serviceWorker.getRegistration
          ? await navigator.serviceWorker.getRegistration("/app/")
          : null;
      if (registration) {
        await registration.showNotification(title, options);
        return tag;
      }
      new Notification(title, options);
      return tag;
    } catch (e) {
      return null;
    }
  },
};

// Fail at import, not at first use.
assertPlatform(webPlatform);

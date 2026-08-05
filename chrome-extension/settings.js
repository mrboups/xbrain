/**
 * xbrain extension settings — pure helpers backed by chrome.storage.sync.
 *
 * Pure module: chrome.storage is injected via deps so node tests can run
 * without polyfilling chrome.*. Settings sync across devices via the user's
 * Chrome profile (chrome.storage.sync).
 *
 * Settings shape (all booleans, default ON):
 *   openInSidePanel        — clicking the toolbar icon opens the side panel
 *                            (Chrome 114+) instead of a floating popup.
 *   autoFillLibreChat      — content script auto-fills the API key field on
 *                            chat.grooveos.app for the "Claude Pro/Max" endpoint.
 *
 * Used by:
 *   - chrome-extension/options.html / options.js (settings UI)
 *   - chrome-extension/background.js (reacts to openInSidePanel)
 *   - chrome-extension/librechat_autofill.js (reacts to autoFillLibreChat)
 *   - chrome-extension/tests/test_settings.mjs
 */

export const SETTINGS_KEY = "xbrain_settings_v1";

export const DEFAULT_SETTINGS = Object.freeze({
  openInSidePanel: true,
  autoFillLibreChat: true,
  // Clip defaults (quick task 260512-tcr Wave 3.3/3.5). null means "no
  // default set — overlay opens fresh". When clipSkipOverlay is true and
  // both defaults are present, the 📎 click sends immediately after a
  // 1.5s confirm grace window.
  clipDefaultProject: null,
  // WORKING, not EPHEMERAL, since 2026-08-05. A clip is a deliberate act — somebody
  // saw a page and chose to keep it — and EPHEMERAL is excluded from every recall
  // path, so the old default meant the agent could not see anything anyone clipped
  // until a human promoted it by hand. Nobody did, so clipping did nothing.
  //
  // The cost is real and accepted: recall now includes pages kept on a hunch, and
  // EPHEMERAL no longer has a producer in this path. Anything that must not enter
  // recall can still be sent at EPHEMERAL explicitly.
  clipDefaultTruthLevel: "WORKING",
  clipSkipOverlay: false,
  // Phase 22 push-a-link (D-22-04). When ON (default), an incoming open_url
  // nudge from a teammate shows a consent notification; when OFF, the receive
  // handler ignores the event entirely (no notification). Recipient-side only.
  allowOpenLinkRequests: true,
  // Skip the click. When ON, a link a teammate sends opens straight away instead
  // of waiting for the notification to be clicked. OFF by default ON PURPOSE:
  // letting someone else open tabs in your browser is a real capability, so it
  // stays opt-in and the consent notification remains the default path.
  autoOpenLinkRequests: false,
  // The master switch for every desktop notification this extension raises —
  // clip results, the "selection captured" nudge, and a teammate's link request
  // alike. ON by default because notifications already worked before this
  // setting existed: shipping it OFF would silently take away a behaviour
  // people rely on, which is a regression wearing a feature's clothes.
  showNotifications: true,
});

/**
 * Load settings from chrome.storage.sync, merged over defaults.
 * Unknown keys in storage are ignored; missing keys fall back to defaults.
 *
 * @param {{get: (k: string) => Promise<object>}} storageArea
 * @returns {Promise<{openInSidePanel: boolean, autoFillLibreChat: boolean}>}
 */
export async function loadSettings(storageArea) {
  const raw = await storageArea.get([SETTINGS_KEY]);
  const stored = (raw && raw[SETTINGS_KEY]) || {};
  return mergeSettings(stored);
}

/**
 * Persist settings to chrome.storage.sync. Merges with defaults so callers
 * can pass partial objects (e.g. {openInSidePanel: false}).
 *
 * @param {{set: (obj: object) => Promise<void>}} storageArea
 * @param {Partial<{openInSidePanel: boolean, autoFillLibreChat: boolean}>} patch
 * @returns {Promise<{openInSidePanel: boolean, autoFillLibreChat: boolean}>}
 */
export async function saveSettings(storageArea, patch) {
  const current = await loadSettings(storageArea);
  const next = mergeSettings({ ...current, ...patch });
  await storageArea.set({ [SETTINGS_KEY]: next });
  return next;
}

/**
 * Merge a raw object (possibly missing fields or carrying unknown ones) with
 * DEFAULT_SETTINGS. Only known keys are kept, and only boolean values pass
 * through — defensive against schema drift in chrome.storage.sync.
 */
// Per-key expected types — drives the defensive merge below. null-defaulted
// keys (clipDefaultProject) accept "string" OR null; boolean keys accept
// only boolean; string keys (clipDefaultTruthLevel) accept only string.
const _SCHEMA = {
  openInSidePanel: ["boolean"],
  autoFillLibreChat: ["boolean"],
  clipDefaultProject: ["string", "null"],
  clipDefaultTruthLevel: ["string"],
  clipSkipOverlay: ["boolean"],
  allowOpenLinkRequests: ["boolean"],
  autoOpenLinkRequests: ["boolean"],
  showNotifications: ["boolean"],
};

function _isAllowed(value, allowedTypes) {
  if (value === null) return allowedTypes.includes("null");
  return allowedTypes.includes(typeof value);
}

export function mergeSettings(raw) {
  const out = { ...DEFAULT_SETTINGS };
  if (raw && typeof raw === "object") {
    for (const k of Object.keys(DEFAULT_SETTINGS)) {
      const allowed = _SCHEMA[k] || ["boolean"];
      if (k in raw && _isAllowed(raw[k], allowed)) {
        out[k] = raw[k];
      }
    }
  }
  return out;
}

/**
 * May this extension raise a desktop notification right now?
 *
 * THE ONE QUESTION EVERY NOTIFICATION CALL SITE MUST ASK. A toggle honoured in
 * some places and not others is worse than no toggle: the person turns it off,
 * sees notifications keep arriving, and concludes the setting is broken — which
 * it is, just not in the way they think.
 *
 * FAILS OPEN. An unreadable settings store means we do not know what they chose,
 * and the default is ON; silently muting on a storage hiccup would lose a
 * teammate's link request with nothing anywhere to explain it.
 *
 * @param {{get: (k: string[]) => Promise<object>}} storageArea
 * @returns {Promise<boolean>}
 */
export async function notificationsEnabled(storageArea) {
  try {
    const settings = await loadSettings(storageArea);
    return settings.showNotifications !== false;
  } catch (e) {
    return true;
  }
}

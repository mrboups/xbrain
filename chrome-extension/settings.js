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
  clipDefaultTruthLevel: "EPHEMERAL",
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

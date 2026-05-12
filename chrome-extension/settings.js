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
export function mergeSettings(raw) {
  const out = { ...DEFAULT_SETTINGS };
  if (raw && typeof raw === "object") {
    for (const k of Object.keys(DEFAULT_SETTINGS)) {
      if (typeof raw[k] === "boolean") out[k] = raw[k];
    }
  }
  return out;
}

/**
 * Options page wiring — loads settings on open, persists on change.
 *
 * Pure UI glue. The schema + merge logic lives in ./settings.js so it can
 * be tested in node without chrome.* polyfills.
 */

import { loadSettings, saveSettings } from "./settings.js";

const STATUS_FADE_MS = 1500;

function showStatus(text) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = text;
  setTimeout(() => {
    // Only clear if the message we set is still there — prevents racing
    // multiple in-flight saves clobbering each other's "Saved ✓" timer.
    if (el.textContent === text) el.textContent = "";
  }, STATUS_FADE_MS);
}

async function init() {
  const settings = await loadSettings(chrome.storage.sync);

  const cbSidePanel = document.getElementById("opt-side-panel");
  const cbAutofill = document.getElementById("opt-autofill-librechat");

  cbSidePanel.checked = settings.openInSidePanel;
  cbAutofill.checked = settings.autoFillLibreChat;

  cbSidePanel.addEventListener("change", async () => {
    await saveSettings(chrome.storage.sync, {
      openInSidePanel: cbSidePanel.checked,
    });
    showStatus(
      cbSidePanel.checked
        ? "Saved ✓  —  side panel will be used on next click of the extension icon"
        : "Saved ✓  —  popup mode restored on next click",
    );
  });

  cbAutofill.addEventListener("change", async () => {
    await saveSettings(chrome.storage.sync, {
      autoFillLibreChat: cbAutofill.checked,
    });
    showStatus(
      cbAutofill.checked
        ? "Saved ✓  —  LibreChat auto-fill enabled (reload LibreChat to apply)"
        : "Saved ✓  —  LibreChat auto-fill disabled",
    );
  });
}

document.addEventListener("DOMContentLoaded", init);

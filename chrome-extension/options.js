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

  // === Existing toggles ===
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

  // === Clip defaults (Wave 3.5) ===
  const inProject = document.getElementById("opt-clip-project");
  const radioTruth = document.getElementsByName("opt-clip-truth");
  const cbSkip = document.getElementById("opt-clip-skip-overlay");

  if (inProject) {
    inProject.value = settings.clipDefaultProject || "";
    let _projTimer = null;
    inProject.addEventListener("input", () => {
      // Debounce 300ms so we don't write on every keystroke.
      clearTimeout(_projTimer);
      _projTimer = setTimeout(async () => {
        const v = inProject.value.trim();
        await saveSettings(chrome.storage.sync, {
          clipDefaultProject: v || null,
        });
        showStatus(
          v
            ? `Saved ✓  —  default project: ${v}`
            : "Saved ✓  —  no default project (overlay will ask each time)",
        );
      }, 300);
    });
  }

  if (radioTruth && radioTruth.length) {
    for (const r of radioTruth) {
      r.checked = r.value === settings.clipDefaultTruthLevel;
      r.addEventListener("change", async () => {
        if (!r.checked) return;
        await saveSettings(chrome.storage.sync, {
          clipDefaultTruthLevel: r.value,
        });
        showStatus(`Saved ✓  —  default truth level: ${r.value}`);
      });
    }
  }

  if (cbSkip) {
    cbSkip.checked = settings.clipSkipOverlay;
    cbSkip.addEventListener("change", async () => {
      await saveSettings(chrome.storage.sync, {
        clipSkipOverlay: cbSkip.checked,
      });
      showStatus(
        cbSkip.checked
          ? "Saved ✓  —  clip overlay will auto-send after 1.5s when defaults are set"
          : "Saved ✓  —  clip overlay always opens for confirmation",
      );
    });
  }
}

document.addEventListener("DOMContentLoaded", init);

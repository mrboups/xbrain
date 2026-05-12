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

  // === Claude Pro/Max session ===
  await renderClaudeSession();
  const btnRefreshClaude = document.getElementById("btn-refresh-claude-session");
  if (btnRefreshClaude) {
    btnRefreshClaude.addEventListener("click", refreshClaudeSession);
  }
}

const SUB_PILL_CLASSES = {
  Pro: "is-pro",
  Max: "is-max",
  "Max 5x": "is-max",
  "Max 20x": "is-max",
  Free: "is-free",
  Team: "is-pro",
  Enterprise: "is-pro",
  Unknown: "",
};

async function renderClaudeSession() {
  const dot = document.getElementById("claude-session-dot");
  const status = document.getElementById("claude-session-status");
  const email = document.getElementById("claude-session-email");
  const sub = document.getElementById("claude-session-sub");
  const org = document.getElementById("claude-session-org");
  if (!status || !email || !sub || !org) return;

  status.textContent = "Loading…";
  email.textContent = "—";
  sub.textContent = "—";
  org.textContent = "—";
  if (dot) {
    dot.classList.remove("is-online", "is-offline");
    dot.classList.add("is-unknown");
  }

  let info = null;
  try {
    info = await chrome.runtime.sendMessage({ type: "GET_CLAUDE_SESSION_INFO" });
  } catch (e) {
    status.textContent = `error: ${e.message}`;
    if (dot) {
      dot.classList.remove("is-unknown", "is-online");
      dot.classList.add("is-offline");
    }
    return;
  }
  if (!info) {
    status.textContent = "no response from service worker";
    return;
  }
  if (!info.signed_in) {
    status.textContent = "Not signed in on claude.ai in this Chrome browser";
    if (dot) {
      dot.classList.remove("is-unknown", "is-online");
      dot.classList.add("is-offline");
    }
    return;
  }
  status.textContent = "Connected";
  email.textContent = info.email || "—";
  sub.textContent = info.subscription || "Unknown";
  sub.className = "sub-pill " + (SUB_PILL_CLASSES[info.subscription] || "");
  org.textContent = info.organization_name || "—";
  if (dot) {
    dot.classList.remove("is-unknown", "is-offline");
    dot.classList.add("is-online");
  }
}

async function refreshClaudeSession() {
  const btn = document.getElementById("btn-refresh-claude-session");
  const actionStatus = document.getElementById("claude-session-action-status");
  if (btn) btn.disabled = true;
  if (actionStatus) actionStatus.textContent = "Reconnecting bridge…";
  try {
    const resp = await chrome.runtime.sendMessage({
      type: "REFRESH_CLAUDE_SESSION",
    });
    if (resp && resp.ok) {
      if (actionStatus) actionStatus.textContent = "Reconnected ✓ — fetching session…";
      // Give the WS a beat to finish the register frame, then re-introspect.
      setTimeout(async () => {
        await renderClaudeSession();
        if (actionStatus) actionStatus.textContent = "Refreshed ✓";
        setTimeout(() => {
          if (actionStatus) actionStatus.textContent = "";
        }, 1500);
        if (btn) btn.disabled = false;
      }, 800);
    } else {
      if (actionStatus) {
        actionStatus.textContent = `Refresh failed: ${(resp && resp.error) || "unknown"}`;
      }
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    if (actionStatus) actionStatus.textContent = `Refresh failed: ${e.message}`;
    if (btn) btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", init);

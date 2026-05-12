/**
 * xbrain Web Clipper — Popup script
 *
 * Flow:
 *  1. On load: ask the content script for selected text via GET_SELECTION
 *  2. On "Send to brain" click:
 *     a. Request the ID token from the background service worker (GET_ID_TOKEN)
 *     b. Build the payload from the form fields
 *     c. Send SEND_TO_BRAIN to the background with the token + payload
 *     d. Display the status (success or error)
 *
 * Sessions section (Phase 9 + quick task 260512-eo1):
 *  - On load, read chrome.storage.local for {xbt_token, user_sub}.
 *  - If absent → show "Connect xbrain account" button (single-click onboarding).
 *  - If present → show 🟢 + email + last seen + Disconnect.
 *  - Connect → chrome.runtime.sendMessage({type: "MINT_AND_CONNECT"})
 *  - Disconnect → chrome.runtime.sendMessage({type: "DISCONNECT"})
 */

"use strict";

/** Utility: show a status message in the Web Clipper status area. */
function showStatus(message, type) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.className = type; // "success" | "error" | "loading"
}

function hideStatus() {
  const el = document.getElementById("status");
  el.className = "";
  el.textContent = "";
}

/**
 * Return the active tab in the current window, or null if none accessible
 * (e.g. chrome:// pages).
 */
async function getActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab || null;
  } catch {
    return null;
  }
}

/**
 * Ask the content script for the selected text + URL + title.
 * Returns { selectedText, url, title } or null if the content script
 * isn't reachable (chrome://, extensions://, etc.).
 */
async function getSelectionFromPage(tab) {
  if (!tab || !tab.id) return null;
  // Content scripts don't run on chrome:// or about:// pages.
  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("about:")) {
    return null;
  }

  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, { type: "GET_SELECTION" }, (response) => {
      if (chrome.runtime.lastError) {
        // Content script not injected (e.g. page loaded before the extension was installed).
        resolve(null);
        return;
      }
      resolve(response || null);
    });
  });
}

/**
 * Extract the hostname from a URL for the `source` field.
 * Example: "https://example.com/page" → "example.com"
 */
function hostnameFromUrl(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "unknown";
  }
}

const MEMORY_API_BASE = "https://api.grooveos.app";

/** Shared state */
let currentTabUrl = "";
let currentTabTitle = "";

/**
 * Load the user's teams via GET /v1/teams/my-teams.
 * Populates the <select id="teamScope"> with the real teams of the connected account.
 */
async function loadUserTeams(idToken) {
  const teamSelect = document.getElementById("teamScope");
  try {
    const res = await fetch(`${MEMORY_API_BASE}/v1/teams/my-teams`, {
      headers: { Authorization: `Bearer ${idToken}` },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const teams = await res.json();

    teamSelect.innerHTML = "";
    if (teams.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "— no team —";
      opt.disabled = true;
      opt.selected = true;
      teamSelect.appendChild(opt);
      return;
    }

    for (const t of teams) {
      const opt = document.createElement("option");
      opt.value = t.slug;
      opt.textContent = t.display_name || t.slug;
      teamSelect.appendChild(opt);
    }
    // Auto-select if a single team is available.
    if (teams.length === 1) teamSelect.selectedIndex = 0;
  } catch (err) {
    // Fail-soft: leave the field empty with a message.
    teamSelect.innerHTML = `<option value="" disabled selected>Failed to load teams</option>`;
    console.warn("loadUserTeams failed:", err);
  }
}

/** Initialize on popup load. */
document.addEventListener("DOMContentLoaded", async () => {
  const contentArea = document.getElementById("content");
  const sourceHint = document.getElementById("sourceHint");
  const sendBtn = document.getElementById("sendBtn");
  const teamSelect = document.getElementById("teamScope");

  // Placeholder while loading.
  teamSelect.innerHTML = `<option value="" disabled selected>Connecting…</option>`;
  sendBtn.disabled = true;

  // 1. Get the active tab + selection.
  const tab = await getActiveTab();
  if (tab) {
    currentTabUrl = tab.url || "";
    currentTabTitle = tab.title || "";
  }

  // Prefer a selection captured via the right-click context menu (quick task
  // 260512-cmu/csm) — it survives across popup opens until the user clicks
  // Send. Otherwise, ask the active tab's content script for the current
  // selection.
  let prefilledText = "";
  let pendingTeamScope = null;
  const pending = await chrome.storage.session.get(["pending_selection"]);
  if (pending && pending.pending_selection && pending.pending_selection.selectedText) {
    prefilledText = pending.pending_selection.selectedText.trim();
    if (pending.pending_selection.url) currentTabUrl = pending.pending_selection.url;
    if (pending.pending_selection.title) currentTabTitle = pending.pending_selection.title;
    if (pending.pending_selection.team_scope) {
      pendingTeamScope = pending.pending_selection.team_scope;
    }
    // Clear so the next popup open doesn't replay it.
    await chrome.storage.session.remove(["pending_selection"]);
  } else {
    const selection = await getSelectionFromPage(tab);
    if (selection && selection.selectedText && selection.selectedText.trim()) {
      prefilledText = selection.selectedText.trim();
    }
  }
  if (prefilledText) {
    contentArea.value = prefilledText;
  }
  if (currentTabUrl) {
    sourceHint.textContent = `Source: ${hostnameFromUrl(currentTabUrl)}`;
  }

  // 2. Authenticate (silent only — no popup on extension open) + load teams.
  // If the user isn't signed in, leave the field as "Not signed in" and let
  // them sign in via the Connect button below. Interactive auth only fires
  // on Send to brain (explicit user action).
  const tokenResponse = await chrome.runtime.sendMessage({
    type: "GET_ID_TOKEN",
    silent: true,
  });
  if (tokenResponse && tokenResponse.idToken) {
    await loadUserTeams(tokenResponse.idToken);
    sendBtn.disabled = false;
    // If the right-click context menu carried a team_scope, pre-select it now
    // that the dropdown is populated. Match by slug; ignore silently if the
    // requested team isn't in the user's team list (rare — e.g. cached menu
    // out of sync with current memberships).
    if (pendingTeamScope) {
      const option = Array.from(teamSelect.options).find(
        (o) => o.value === pendingTeamScope,
      );
      if (option) teamSelect.value = pendingTeamScope;
    }
  } else {
    teamSelect.innerHTML = `<option value="" disabled selected>Sign in to load teams</option>`;
    // No error toast — silent failure here is expected for first-time users.
    // We KEEP sendBtn enabled even without a token so clicking Send to brain
    // triggers the full interactive auth path (handleSend uses the default
    // silent: false GET_ID_TOKEN). The user has two entry points to sign in:
    //   1. The Connect xbrain account button in the Sessions section below.
    //   2. Clicking Send to brain (auth fires inline before the upsert).
    sendBtn.disabled = false;
  }

  // 3. Wire the "Send to brain" button.
  sendBtn.addEventListener("click", handleSend);
});

async function handleSend() {
  const sendBtn = document.getElementById("sendBtn");
  const content = document.getElementById("content").value.trim();
  const teamScope = document.getElementById("teamScope").value;
  const projectScope = document.getElementById("projectScope").value.trim() || null;
  const truthLevel = document.querySelector('input[name="truthLevel"]:checked')?.value || "EPHEMERAL";

  if (!content) {
    showStatus("Content cannot be empty.", "error");
    return;
  }
  if (!teamScope) {
    showStatus("Please select a team.", "error");
    return;
  }

  sendBtn.disabled = true;
  showStatus("Authenticating…", "loading");

  try {
    // a. Get the ID token from the background service worker.
    const tokenResponse = await chrome.runtime.sendMessage({ type: "GET_ID_TOKEN" });

    if (!tokenResponse || tokenResponse.error) {
      const errMsg = tokenResponse?.error || "Could not obtain Google token.";
      showStatus(`Auth error: ${errMsg}`, "error");
      sendBtn.disabled = false;
      return;
    }

    const { idToken } = tokenResponse;

    showStatus("Sending to brain…", "loading");

    // b. Build the payload per the xbrain tagging contract.
    const hostname = hostnameFromUrl(currentTabUrl);
    const payload = {
      content,
      team_scope: teamScope,
      project_scope: projectScope,
      visibility: "team",
      confidence: 1.0,
      truth_level: truthLevel,
      source: `chrome:${hostname}`,
      validation_status: "pending",
    };

    // c. Send via the background service worker.
    const sendResponse = await chrome.runtime.sendMessage({
      type: "SEND_TO_BRAIN",
      idToken,
      payload,
    });

    if (!sendResponse || sendResponse.error) {
      const errMsg = sendResponse?.error || "Unknown network error.";

      // On 401 (token expired), clear the cache and ask the user to retry.
      if (errMsg.includes("401")) {
        await chrome.storage.session.remove(["xbrain_id_token", "xbrain_id_token_expiry"]);
        showStatus("Token expired — please retry to sign in again.", "error");
      } else {
        showStatus(`Error: ${errMsg}`, "error");
      }
    } else {
      // d. Success.
      showStatus("Sent to brain!", "success");
      // Reset content after a successful send.
      document.getElementById("content").value = "";
    }
  } catch (err) {
    showStatus(`Unexpected error: ${err.message}`, "error");
  } finally {
    sendBtn.disabled = false;
  }
}

// === Phase 9 + quick task 260512-eo1: Sessions section ===
//
// Surfaces the bridge WebSocket state + the user's xbrain bridge token.
// Contracts:
//   background.js answers chrome.runtime.sendMessage({kind: "ws_status_query"})
//     with {readyState: number} (0 CONNECTING, 1 OPEN, 2 CLOSING, 3 CLOSED, -1 no socket).
//   GET /v1/me/external-sessions returns [{id, provider, extension_id, last_seen_at, metadata}]
//   chrome.runtime.sendMessage({type: "MINT_AND_CONNECT"}) → onboarding flow
//   chrome.runtime.sendMessage({type: "DISCONNECT"}) → revoke + clear
//
// xbt_ token lives in chrome.storage.local under key "xbt_token" (canonical since 260512-eo1).
// CRITICAL: use chrome.runtime.sendMessage, NOT window.postMessage (T-09-05-01 mitigation).
// CRITICAL: never log the xbt_token raw value.

/** Show the inline connect status block (loading / success / error). */
function setConnectStatus(text, type) {
  const el = document.getElementById("connect-status");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.className = type || "";
}

/**
 * Toggle between the disconnected state (Connect button) and the connected
 * state (session-row + Disconnect). Source of truth: chrome.storage.local.xbt_token.
 */
async function renderConnectState() {
  const connectRow = document.getElementById("connect-row");
  const sessionsList = document.getElementById("sessions-list");

  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (xbt_token) {
    if (connectRow) connectRow.hidden = true;
    if (sessionsList) sessionsList.hidden = false;
    await renderSessions();
  } else {
    if (connectRow) connectRow.hidden = false;
    if (sessionsList) sessionsList.hidden = true;
  }
}

/**
 * Zero-click onboarding — silent path ONLY.
 *
 * When the popup opens and no xbt_token is stored, try a fully silent
 * mint. If silent succeeds (user is Chrome-signed-in AND has previously
 * granted consent), the state flips to connected automatically. If silent
 * fails, we DO NOT escalate to a consent popup — the manual Connect button
 * stays visible so the user can opt in explicitly.
 *
 * This was tightened in quick task 260512-cmu after user feedback that
 * an auto-popup on side panel open felt "a bit violent". Now: zero-click
 * when possible, one-click otherwise. Never zero-interaction with a popup.
 *
 * Guard `_autoMintAttempted` prevents an infinite loop if the SW returns
 * {ok: false} during a single popup open.
 */
let _autoMintAttempted = false;

async function maybeAutoMint() {
  if (_autoMintAttempted) return;
  _autoMintAttempted = true;

  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (xbt_token) return; // already connected, nothing to do

  // IMPORTANT: keep the Connect button ENABLED during the silent attempt so
  // the user can click it at any moment to take the interactive path. If the
  // silent attempt eventually succeeds, storage.onChanged will flip the UI
  // to connected. If it fails, this status clears.
  setConnectStatus("Trying silent sign-in…", "loading");

  try {
    // silent: true → SW will NEVER open a consent popup. If the user isn't
    // already authenticated, the SW returns {ok: false} and we just clear
    // the loader. The user can still click the manual Connect button.
    const resp = await chrome.runtime.sendMessage({
      type: "MINT_AND_CONNECT",
      silent: true,
    });
    if (resp && resp.ok) {
      const copied = await copyTokenToClipboard();
      setConnectStatus(
        copied
          ? `Connected as ${resp.email || "your account"} ✓  —  token copied to clipboard`
          : `Connected as ${resp.email || "your account"} ✓`,
        "success",
      );
      setTimeout(() => {
        setConnectStatus("", "");
        renderConnectState();
      }, 1200);
    } else {
      // Silent failed — clear the loader. Connect button is already visible
      // and enabled (we never touched its `disabled` state).
      setConnectStatus("", "");
    }
  } catch {
    setConnectStatus("", "");
  }
}

async function renderSessions() {
  await renderWsStatus();
  await renderClaudeSessionInfo();
  await renderGithubLinkState();
}

/**
 * Toggle the GitHub link/linked rows based on the current user's
 * github_username from /v1/me. Quick task 260512-glk.
 */
async function renderGithubLinkState() {
  const linkRow = document.getElementById("github-link-row");
  const linkedRow = document.getElementById("github-linked-row");
  const linkedName = document.getElementById("github-linked-name");
  if (!linkRow || !linkedRow) return;

  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (!xbt_token) {
    linkRow.hidden = true;
    linkedRow.hidden = true;
    return;
  }
  try {
    const res = await fetch(`${MEMORY_API_BASE}/v1/me`, {
      headers: { Authorization: `Bearer ${xbt_token}` },
    });
    if (!res.ok) {
      linkRow.hidden = true;
      linkedRow.hidden = true;
      return;
    }
    const me = await res.json();
    if (me.github_username) {
      if (linkedName) linkedName.textContent = `@${me.github_username}`;
      linkedRow.hidden = false;
      linkRow.hidden = true;
    } else {
      linkedRow.hidden = true;
      linkRow.hidden = false;
    }
  } catch {
    // Network failure — leave the row state as-is rather than flicker.
  }
}

async function handleLinkGithub() {
  const btn = document.getElementById("btn-link-github");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "…";
  }
  setConnectStatus("Linking your GitHub account…", "loading");
  try {
    const resp = await chrome.runtime.sendMessage({ type: "LINK_GITHUB" });
    if (resp && resp.ok) {
      setConnectStatus(
        `Linked @${resp.github_username} ✓  —  team list refreshed`,
        "success",
      );
      setTimeout(() => {
        setConnectStatus("", "");
        renderSessions();
      }, 1500);
    } else {
      setConnectStatus(
        `Link failed: ${(resp && resp.error) || "unknown error"}`,
        "error",
      );
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Link";
      }
    }
  } catch (err) {
    setConnectStatus(`Link failed: ${err.message}`, "error");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Link";
    }
  }
}

async function renderWsStatus() {
  const dot = document.getElementById("ws-dot");
  if (!dot) return;
  try {
    const resp = await chrome.runtime.sendMessage({ kind: "ws_status_query" });
    dot.classList.remove("active", "idle");
    if (resp && resp.readyState === 1) {
      dot.classList.add("active");
      dot.title = "WebSocket connected to bridge";
    } else {
      dot.classList.add("idle");
      const state = resp && typeof resp.readyState === "number" ? resp.readyState : "unknown";
      dot.title = `WebSocket state: ${state}`;
    }
  } catch (err) {
    dot.classList.remove("active");
    dot.classList.add("idle");
    dot.title = `WS query failed: ${err.message}`;
  }
}

/**
 * Best-effort claude.ai email lookup directly from the popup (decouples display
 * from the SW register frame which may have fired before claude.ai cookies were
 * available). Returns null on any failure — never throws.
 */
async function fetchClaudeEmailDirect() {
  try {
    const r = await fetch("https://claude.ai/api/auth/current_account", {
      credentials: "include",
    });
    if (!r.ok) return null;
    const j = await r.json();
    return (
      j.email_address ||
      j.email ||
      (j.account && (j.account.email_address || j.account.email)) ||
      null
    );
  } catch {
    return null;
  }
}

async function renderClaudeSessionInfo() {
  const emailEl = document.getElementById("claude-email");
  const lastEl = document.getElementById("claude-last-seen");
  if (!emailEl || !lastEl) return;

  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (!xbt_token) {
    emailEl.textContent = "not connected";
    lastEl.textContent = "";
    return;
  }

  // Query claude.ai directly first — that's the freshest source for the user's
  // logged-in email. Fall back to whatever metadata the SW captured at register time.
  const liveEmail = await fetchClaudeEmailDirect();

  try {
    const res = await fetch(`${MEMORY_API_BASE}/v1/me/external-sessions`, {
      headers: { Authorization: `Bearer ${xbt_token}` },
    });
    if (!res.ok) {
      emailEl.textContent = liveEmail || `error ${res.status}`;
      lastEl.textContent = "";
      return;
    }
    const sessions = await res.json();
    const claude = Array.isArray(sessions) ? sessions.find((s) => s.provider === "claude") : null;
    if (!claude) {
      emailEl.textContent = liveEmail || "not detected";
      lastEl.textContent = liveEmail
        ? "Bridge will register on next reconnect"
        : "Log in to claude.ai in this browser";
      return;
    }
    const metaEmail =
      claude.metadata && (claude.metadata.email_logged || claude.metadata.email);
    emailEl.textContent = liveEmail || metaEmail || "—";
    lastEl.textContent = `Last seen: ${formatRelative(claude.last_seen_at)}`;
  } catch (err) {
    emailEl.textContent = liveEmail || `network error`;
    lastEl.textContent = "";
    // err.message intentionally NOT logged — could leak network probe info if a proxy injects it.
  }
}

function formatRelative(isoStamp) {
  if (!isoStamp) return "—";
  const then = new Date(isoStamp);
  if (isNaN(then.getTime())) return "—";
  const diffSec = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (diffSec < 60) return `${Math.floor(diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return then.toLocaleDateString();
}

/**
 * Best-effort copy of the bridge token to the clipboard so the user can
 * Ctrl+V it into LibreChat's "Claude Pro/Max" API key field. Returns true
 * on success — failures (no permission, popup defocused) are silent because
 * the token is also visible via the explicit Copy button on the connected row.
 */
async function copyTokenToClipboard() {
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    if (!xbt_token) return false;
    await navigator.clipboard.writeText(xbt_token);
    return true;
  } catch {
    return false;
  }
}

async function handleConnect() {
  const btn = document.getElementById("btn-connect-xbrain");
  if (btn) btn.disabled = true;
  setConnectStatus("Signing you in with Google…", "loading");

  try {
    // Explicit user click → allow the interactive Google consent popup.
    const resp = await chrome.runtime.sendMessage({
      type: "MINT_AND_CONNECT",
      silent: false,
    });
    if (resp && resp.ok) {
      const copied = await copyTokenToClipboard();
      setConnectStatus(
        copied
          ? `Connected as ${resp.email || "your account"} ✓  —  token copied to clipboard`
          : `Connected as ${resp.email || "your account"} ✓`,
        "success",
      );
      // Give the WS layer a moment to reconnect via storage.onChanged.
      setTimeout(() => {
        setConnectStatus("", "");
        renderConnectState();
      }, 1200);
    } else {
      const errMsg = (resp && resp.error) || "Unknown error.";
      setConnectStatus(`Connection failed: ${errMsg}`, "error");
      if (btn) btn.disabled = false;
    }
  } catch (err) {
    setConnectStatus(`Connection failed: ${err.message}`, "error");
    if (btn) btn.disabled = false;
  }
}

async function handleCopyToken() {
  const btn = document.getElementById("btn-copy-token");
  const ok = await copyTokenToClipboard();
  if (btn) {
    const original = btn.textContent;
    btn.textContent = ok ? "✓" : "✕";
    btn.title = ok ? "Token copied" : "Copy failed";
    setTimeout(() => {
      btn.textContent = original;
      btn.title = "Copy bridge token to clipboard";
    }, 1500);
  }
}

async function handleDisconnect() {
  if (
    !confirm(
      "Disconnect your xbrain account? Your personal API token will be revoked and you'll need to reconnect to use the Claude Pro/Max route.",
    )
  ) {
    return;
  }
  setConnectStatus("Disconnecting…", "loading");
  try {
    const resp = await chrome.runtime.sendMessage({ type: "DISCONNECT" });
    if (resp && resp.ok) {
      setConnectStatus("", "");
      await renderConnectState();
    } else {
      setConnectStatus("Disconnect failed.", "error");
    }
  } catch (err) {
    setConnectStatus(`Disconnect failed: ${err.message}`, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const btnConnect = document.getElementById("btn-connect-xbrain");
  const btnCopy = document.getElementById("btn-copy-token");
  const btnRefresh = document.getElementById("btn-refresh-claude");
  const btnDisc = document.getElementById("btn-disconnect-claude");
  if (btnConnect) {
    btnConnect.addEventListener("click", handleConnect);
  }
  if (btnCopy) {
    btnCopy.addEventListener("click", handleCopyToken);
  }
  if (btnRefresh) {
    btnRefresh.addEventListener("click", renderSessions);
  }
  if (btnDisc) {
    btnDisc.addEventListener("click", handleDisconnect);
  }
  const btnLinkGh = document.getElementById("btn-link-github");
  if (btnLinkGh) {
    btnLinkGh.addEventListener("click", handleLinkGithub);
  }
  // Initial render — fail-soft if background.js isn't wired yet.
  renderConnectState().then(() => {
    // Zero-click onboarding: if we land in the disconnected state, try the
    // silent mint path automatically. Skips itself if a token is already
    // stored. See maybeAutoMint() docstring.
    maybeAutoMint();
  });
});

// Keep the popup in sync when the SW finishes the mint flow asynchronously.
// Without this, a user who closes the popup during the Google consent window
// would need to click Connect a second time after re-opening the popup —
// even though the token is already in chrome.storage.local. Listening on
// storage.onChanged makes the connected state render automatically the
// moment the SW writes the token.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.xbt_token) {
    renderConnectState();
  }
});

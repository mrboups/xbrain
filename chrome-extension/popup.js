/**
 * xbrain extension — popup.js (chat-first, quick task 260512-tcr Wave 3.3)
 *
 * The whole UI is the team chat. Web Clipper collapses into a single 📎
 * button that opens an overlay (Source / Project / Truth-level / Send).
 * Header has team selector, presence counter, ⚙️ Settings, 💬 LibreChat.
 *
 * Centrifugo client connects via a JWT issued by memory-api
 * (POST /v1/me/centrifugo-token) and subscribes to `team:<id>` channels.
 * Messages and agent streams flow over the WS; history paginates over
 * REST (GET /v1/teams/{id}/messages?before=...).
 */

"use strict";

import {
  StreamBuffer,
  detectMentionClient,
  formatRelative,
  hostnameFromUrl,
  authorLabel,
  bubbleClass,
  provenanceLabel,
} from "./chat_stream.js";
import { loadSettings, saveSettings } from "./settings.js";

const MEMORY_API_BASE = "https://api.grooveos.app";

// ---------- App state (single instance per popup open) ----------

const state = {
  me: null,            // /v1/me result {id, source_user_id, email, ...}
  teams: [],           // [{id, slug, display_name, github_org}, ...]
  activeTeamId: null,
  centrifuge: null,    // Centrifuge instance
  subscription: null,  // active team:<id> subscription
  presenceCount: 0,
  streamBuffer: new StreamBuffer(),
  nameCache: {},       // author_user_id → display name
  oldestLoadedTs: null,
  historyPaging: false,
  initialLoaded: false,
};

// ---------- DOM refs ----------

const $ = (id) => document.getElementById(id);

// ---------- Boot ----------

document.addEventListener("DOMContentLoaded", async () => {
  wireHeader();
  wireConnectionCard();
  wireComposer();
  wireClipOverlay();
  await boot();
});

async function boot() {
  // 1. Check connection state. If no xbt_token → show connection card,
  //    let the user click Connect; chat boots once they're authed.
  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (!xbt_token) {
    showConnectionCard();
    return;
  }
  hideConnectionCard();

  // 2. Fetch /v1/me + teams in parallel.
  try {
    state.me = await fetchJson(`${MEMORY_API_BASE}/v1/me`, xbt_token);
    state.teams = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/my-teams`,
      xbt_token,
    );
  } catch (e) {
    console.error("[xbrain] boot failed:", e);
    showConnectionCard();
    return;
  }

  if (!state.teams.length) {
    renderEmptyTeams();
    return;
  }

  renderTeamSelector();
  state.activeTeamId = state.teams[0].id;

  // 3. Mint Centrifugo token + connect.
  await connectCentrifugo();

  // 4. Subscribe to the active team channel + load initial history.
  await switchTeam(state.activeTeamId);

  // 5. Drop the cursor in the composer so the user can start typing
  //    immediately. Side panel never grabs focus by default; popup does
  //    but only on the first interactive element — explicit focus
  //    here is the safe path for both surfaces.
  focusComposer();
}

function focusComposer() {
  const input = document.getElementById("composer-input");
  if (input) {
    // requestAnimationFrame so the focus call runs after the layout
    // settles (composer pill mounted, no autofill race).
    requestAnimationFrame(() => input.focus());
  }
}

// ---------- Header ----------

function wireHeader() {
  $("teamSelector").addEventListener("change", (e) => {
    const id = e.target.value;
    if (id && id !== state.activeTeamId) {
      switchTeam(id);
    }
  });
  $("btn-settings").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
  });
  $("btn-open-librechat").addEventListener("click", () => {
    chrome.tabs.create({ url: "https://chat.grooveos.app/" });
  });
}

function renderTeamSelector() {
  const sel = $("teamSelector");
  sel.innerHTML = "";
  for (const t of state.teams) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.display_name || t.slug;
    sel.appendChild(opt);
  }
  sel.value = state.activeTeamId || state.teams[0].id;
}

function setPresence(n) {
  state.presenceCount = n;
  const badge = $("presenceBadge");
  const count = $("presenceCount");
  if (!badge || !count) return;
  if (n > 0) {
    badge.hidden = false;
    count.textContent = String(n);
  } else {
    badge.hidden = true;
  }
}

// ---------- Connection card ----------

function showConnectionCard() {
  $("connection-card").hidden = false;
  $("chat-body").style.display = "none";
  document.querySelector(".xb-composer").style.display = "none";
}

function hideConnectionCard() {
  $("connection-card").hidden = true;
  $("chat-body").style.display = "";
  document.querySelector(".xb-composer").style.display = "";
}

function wireConnectionCard() {
  // Phase 10 GHA-07 — GitHub-primary sign-in. Mints xbt_ directly via
  // POST /v1/auth/github/signin (no Google ID token required). The handler
  // lives in background.js; we just kick the runtime message and wait for the
  // canonical xbt_token/user_sub/api_token_id triple to land in
  // chrome.storage.local, then reload.
  const btnSigninGithub = $("btn-signin-github");
  if (btnSigninGithub) {
    btnSigninGithub.addEventListener("click", async () => {
      btnSigninGithub.disabled = true;
      setConnectStatus("Authenticating with GitHub…", "loading");
      try {
        const resp = await chrome.runtime.sendMessage({ type: "SIGNIN_GITHUB" });
        if (resp && resp.ok) {
          setConnectStatus(
            `Signed in as ${resp.github_username ? "@" + resp.github_username : resp.email || "your account"} ✓`,
            "success",
          );
          // Reload the popup so the chat UI initializes with the new token.
          // The storage.onChanged listener at the bottom of this file also
          // triggers a boot() but a hard reload guarantees a clean state.
          setTimeout(() => window.location.reload(), 800);
        } else {
          setConnectStatus(
            `GitHub sign-in failed: ${(resp && resp.error) || "unknown"}`,
            "error",
          );
          btnSigninGithub.disabled = false;
        }
      } catch (err) {
        setConnectStatus(`GitHub sign-in error: ${err.message}`, "error");
        btnSigninGithub.disabled = false;
      }
    });
  }

  $("btn-connect-xbrain").addEventListener("click", async () => {
    const btn = $("btn-connect-xbrain");
    btn.disabled = true;
    setConnectStatus("Signing in with Google…", "loading");
    try {
      const resp = await chrome.runtime.sendMessage({
        type: "MINT_AND_CONNECT",
        silent: false,
      });
      if (resp && resp.ok) {
        setConnectStatus(`Connected as ${resp.email || "your account"} ✓`, "success");
        setTimeout(async () => {
          setConnectStatus("", "");
          await boot();
        }, 800);
      } else {
        setConnectStatus(`Connection failed: ${(resp && resp.error) || "unknown"}`, "error");
        btn.disabled = false;
      }
    } catch (err) {
      setConnectStatus(`Connection failed: ${err.message}`, "error");
      btn.disabled = false;
    }
  });

  // GitHub link button (carried over from the prior popup design)
  const btnLinkGh = $("btn-link-github");
  if (btnLinkGh) {
    btnLinkGh.addEventListener("click", async () => {
      btnLinkGh.disabled = true;
      try {
        const resp = await chrome.runtime.sendMessage({ type: "LINK_GITHUB" });
        if (!resp || !resp.ok) {
          alert(`Link failed: ${(resp && resp.error) || "unknown"}`);
          btnLinkGh.disabled = false;
          return;
        }
        // Refresh teams after linking.
        await boot();
      } catch (err) {
        alert(`Link failed: ${err.message}`);
        btnLinkGh.disabled = false;
      }
    });
  }
}

function setConnectStatus(text, type) {
  const el = $("connect-status");
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

// ---------- Centrifugo connect ----------

async function connectCentrifugo() {
  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  const tokenInfo = await fetchJson(
    `${MEMORY_API_BASE}/v1/me/centrifugo-token`,
    xbt_token,
    "POST",
  );

  // Disconnect any prior centrifuge instance (e.g. after token refresh).
  if (state.centrifuge) {
    try {
      state.centrifuge.disconnect();
    } catch {
      /* ignore */
    }
  }

  // eslint-disable-next-line no-undef -- global from vendor/centrifuge.js
  const Centrifuge = globalThis.Centrifuge;
  if (!Centrifuge) {
    console.error("[xbrain] Centrifuge global missing — vendor/centrifuge.js didn't load");
    return;
  }
  state.centrifuge = new Centrifuge(tokenInfo.ws_url, {
    token: tokenInfo.token,
  });
  state.centrifuge.on("connected", () => {
    console.log("[xbrain] centrifuge connected");
  });
  state.centrifuge.on("error", (err) => {
    console.warn("[xbrain] centrifuge error:", err);
  });
  state.centrifuge.connect();
}

// ---------- Team switch ----------

async function switchTeam(teamId) {
  // Tear down any prior subscription.
  if (state.subscription) {
    try {
      state.subscription.unsubscribe();
      state.centrifuge.removeSubscription(state.subscription);
    } catch {
      /* ignore */
    }
    state.subscription = null;
  }

  state.activeTeamId = teamId;
  state.streamBuffer = new StreamBuffer();
  state.oldestLoadedTs = null;
  state.initialLoaded = false;
  clearMessageList();
  setPresence(0);

  const channelName = `team:${teamId}`;
  state.subscription = state.centrifuge.newSubscription(channelName);
  state.subscription.on("publication", (ctx) => handlePublication(ctx.data));
  state.subscription.on("join", () => updatePresenceFromAPI());
  state.subscription.on("leave", () => updatePresenceFromAPI());
  state.subscription.on("subscribed", () => updatePresenceFromAPI());
  state.subscription.subscribe();

  await loadInitialHistory();
}

async function updatePresenceFromAPI() {
  // Use Centrifuge's presence_stats() RPC — returns {num_clients, num_users}.
  try {
    const stats = await state.subscription.presenceStats();
    setPresence(stats?.numUsers ?? 0);
  } catch {
    /* presence is optional */
  }
}

// ---------- History ----------

async function loadInitialHistory() {
  if (state.initialLoaded) return;
  state.initialLoaded = true;
  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  const data = await fetchJson(
    `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/messages?limit=50`,
    xbt_token,
  );
  // Response is newest-first; render oldest-first.
  const msgs = (data.messages || []).slice().reverse();
  if (msgs.length === 0) {
    $("chat-empty").hidden = false;
    return;
  }
  $("chat-empty").hidden = true;
  for (const m of msgs) renderMessage(m, { prepend: false });
  state.oldestLoadedTs = msgs[0].created_at;
  // Initial load: pin to the latest message regardless of scrollTop.
  scrollToBottom({ force: true });
}

async function loadOlderPage() {
  if (state.historyPaging || !state.oldestLoadedTs) return;
  state.historyPaging = true;
  $("history-loader").hidden = false;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const data = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/messages?before=${encodeURIComponent(state.oldestLoadedTs)}&limit=50`,
      xbt_token,
    );
    const olderMsgs = (data.messages || []).slice().reverse();
    const list = $("message-list");
    // Preserve scroll position: measure top sentinel before prepend.
    const scrollEl = $("chat-scroll");
    const prevHeight = scrollEl.scrollHeight;
    for (const m of olderMsgs) {
      renderMessage(m, { prepend: true });
    }
    if (olderMsgs.length > 0) {
      state.oldestLoadedTs = olderMsgs[0].created_at;
    }
    // Re-anchor scroll so the user's viewport doesn't jump.
    scrollEl.scrollTop = scrollEl.scrollHeight - prevHeight + scrollEl.scrollTop;
  } finally {
    state.historyPaging = false;
    $("history-loader").hidden = true;
  }
}

// ---------- Publication handler (incoming WS frames) ----------

function handlePublication(data) {
  if (!data || !data.type) return;
  if (data.type === "message") {
    renderMessage(data.message, { prepend: false });
    scrollToBottom();
    $("chat-empty").hidden = true;
    return;
  }
  if (data.type === "agent_stream_start") {
    state.streamBuffer.start(data.message_id);
    renderAgentBubble({
      id: data.message_id,
      agent_name: data.agent_name,
      routed_via: data.routed_via,
      streaming: true,
    });
    scrollToBottom();
    $("chat-empty").hidden = true;
    return;
  }
  if (data.type === "agent_stream_chunk") {
    state.streamBuffer.append(data.message_id, data.delta);
    const bodyEl = document.querySelector(
      `[data-msg-id="${data.message_id}"] .xb-msg-bubble`,
    );
    if (bodyEl) {
      bodyEl.textContent = state.streamBuffer.get(data.message_id);
      scrollToBottom();
    }
    return;
  }
  if (data.type === "agent_stream_end") {
    state.streamBuffer.finalize(data.message_id);
    const bodyEl = document.querySelector(
      `[data-msg-id="${data.message_id}"] .xb-msg-bubble`,
    );
    if (bodyEl) bodyEl.classList.remove("streaming");
    return;
  }
  if (data.type === "agent_stream_error") {
    const bodyEl = document.querySelector(
      `[data-msg-id="${data.message_id}"] .xb-msg-bubble`,
    );
    if (bodyEl) {
      bodyEl.classList.remove("streaming");
      bodyEl.textContent += `\n\n(error: ${data.error || "unknown"})`;
    }
    return;
  }
}

// ---------- Render messages ----------

function clearMessageList() {
  $("message-list").innerHTML = "";
}

function renderMessage(msg, { prepend = false } = {}) {
  if (document.querySelector(`[data-msg-id="${msg.id}"]`)) return; // de-dupe
  const wrapper = buildBubbleNode(msg);
  const list = $("message-list");
  if (prepend) list.insertBefore(wrapper, list.firstChild);
  else list.appendChild(wrapper);
}

function renderAgentBubble({ id, agent_name, routed_via, streaming }) {
  if (document.querySelector(`[data-msg-id="${id}"]`)) return;
  const wrapper = buildBubbleNode({
    id,
    kind: "agent",
    agent_name,
    routed_via,
    content: "",
    created_at: new Date().toISOString(),
  });
  if (streaming) {
    wrapper.querySelector(".xb-msg-bubble").classList.add("streaming");
  }
  $("message-list").appendChild(wrapper);
}

function buildBubbleNode(msg) {
  // Row layout — LibreChat-style flat grid: avatar | (meta + body).
  const rowClass = bubbleClass(msg, state.me?.id); // is-self / is-user / is-agent
  const wrapper = document.createElement("div");
  wrapper.className = `xb-msg ${rowClass}`;
  wrapper.dataset.msgId = msg.id;

  // Avatar — letter for users, 🤖 for agents
  const avatar = document.createElement("div");
  avatar.className = "xb-msg-avatar";
  if (msg.kind === "agent") {
    avatar.textContent = "🤖";
  } else {
    const label = authorLabel({
      msg,
      selfUserId: state.me?.id,
      nameCache: state.nameCache,
    });
    avatar.textContent = (label[0] || "?").toUpperCase();
  }
  wrapper.appendChild(avatar);

  // Meta row (sender + time + provenance)
  const meta = document.createElement("div");
  meta.className = "xb-msg-meta";

  const author = document.createElement("span");
  author.className = "xb-msg-author";
  if (msg.kind === "agent") author.classList.add("is-agent");
  author.textContent = authorLabel({
    msg,
    selfUserId: state.me?.id,
    nameCache: state.nameCache,
  });
  meta.appendChild(author);

  const time = document.createElement("span");
  time.className = "xb-msg-time";
  time.textContent = formatRelative(msg.created_at);
  time.title = new Date(msg.created_at).toLocaleString();
  meta.appendChild(time);

  const prov = provenanceLabel(msg.routed_via);
  if (prov) {
    const provSpan = document.createElement("span");
    provSpan.className = `xb-msg-provenance ${prov.cls}`;
    provSpan.textContent = prov.text;
    meta.appendChild(provSpan);
  }
  wrapper.appendChild(meta);

  // Body (no bubble — flat text per LibreChat)
  const body = document.createElement("div");
  body.className = "xb-msg-bubble";
  body.textContent = msg.content || "";
  wrapper.appendChild(body);

  return wrapper;
}

function scrollToBottom({ force = false } = {}) {
  const el = $("chat-scroll");
  // Force=true: always pin to bottom (use this on initial chat load so the
  // user lands on the latest message right above the composer).
  // Force=false (default): only auto-scroll if the user is near the bottom —
  // don't yank them up if they scrolled history to read something older.
  if (force) {
    // Defer to next frame so layout has settled (avatar grid + body sizes).
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return;
  }
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  if (nearBottom) {
    el.scrollTop = el.scrollHeight;
  }
}

// ---------- Composer ----------

function wireComposer() {
  const input = $("composer-input");
  input.addEventListener("input", () => autoResize(input));
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    // SHIFT+ENTER → newline (textarea default, don't intercept)
    if (e.shiftKey) return;
    // Plain ENTER or CTRL/META+ENTER → send
    e.preventDefault();
    sendMessage();
  });
  $("btn-send").addEventListener("click", sendMessage);
  $("btn-clip").addEventListener("click", openClipOverlay);

  // Scroll-up history paging.
  $("chat-scroll").addEventListener("scroll", () => {
    if ($("chat-scroll").scrollTop < 80) loadOlderPage();
  });
}

function autoResize(el) {
  // Let CSS min-height set the 2-row floor; we just grow up to max-height.
  // Reset height to "auto" first so scrollHeight reflects the true content size.
  el.style.height = "auto";
  // Read the CSS max-height ceiling so JS + CSS stay in sync.
  const maxH = parseFloat(getComputedStyle(el).maxHeight) || 200;
  const target = Math.min(el.scrollHeight, maxH);
  el.style.height = target + "px";
  // Toggle scrollbar visibility — hidden while content fits, auto when not.
  if (el.scrollHeight > maxH) {
    el.classList.add("is-overflowing");
  } else {
    el.classList.remove("is-overflowing");
  }
}

async function sendMessage() {
  const input = $("composer-input");
  const content = input.value.trim();
  if (!content || !state.activeTeamId) return;
  const sendBtn = $("btn-send");
  sendBtn.disabled = true;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/messages`,
      xbt_token,
      "POST",
      { content },
    );
    input.value = "";
    autoResize(input);
  } catch (e) {
    console.warn("[xbrain] send failed:", e);
    alert(`Send failed: ${e.message}`);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

// ---------- Clip overlay ----------

function wireClipOverlay() {
  $("btn-clip-close").addEventListener("click", closeClipOverlay);
  $("btn-clip-cancel").addEventListener("click", closeClipOverlay);
  $("btn-clip-send").addEventListener("click", submitClip);
}

async function openClipOverlay() {
  // Pre-fill defaults from settings.
  const settings = await loadSettings(chrome.storage.sync);
  if (settings.clipDefaultProject) {
    $("clip-project").value = settings.clipDefaultProject;
  }
  if (settings.clipDefaultTruthLevel) {
    const radio = document.querySelector(
      `input[name="clipTruthLevel"][value="${settings.clipDefaultTruthLevel}"]`,
    );
    if (radio) radio.checked = true;
  }

  // Resolve active tab + selection — source is auto-determined:
  //   selection text present → ✂️ Selection mode
  //   else                   → 📄 Page mode
  let tab = null;
  try {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch {
    /* ignore */
  }

  let selectedText = "";
  if (tab && tab.id && tab.url && !tab.url.startsWith("chrome://")) {
    try {
      const sel = await new Promise((resolve) => {
        chrome.tabs.sendMessage(tab.id, { type: "GET_SELECTION" }, (resp) => {
          if (chrome.runtime.lastError) resolve(null);
          else resolve(resp);
        });
      });
      selectedText = (sel && sel.selectedText && sel.selectedText.trim()) || "";
    } catch {
      /* ignore */
    }
  }

  // Stash on the overlay so submitClip doesn't need to re-query the tab.
  const overlayEl = $("clip-overlay");
  overlayEl.dataset.clipMode = selectedText ? "selection" : "page";
  overlayEl.dataset.clipUrl = (tab && tab.url) || "";
  overlayEl.dataset.clipTitle = (tab && tab.title) || "";
  overlayEl.dataset.clipSelection = selectedText;

  // Render the "what will be sent" preview.
  const modeEl = $("clip-preview-mode");
  const detailEl = $("clip-preview-detail");
  if (selectedText) {
    modeEl.textContent = "✂️ Selection";
    detailEl.textContent =
      selectedText.length > 240
        ? selectedText.slice(0, 240).trim() + "…"
        : selectedText;
  } else {
    modeEl.textContent = "📄 Page";
    detailEl.textContent = tab
      ? `${tab.title || "(no title)"} — ${hostnameFromUrl(tab.url || "")}`
      : "No active tab";
  }

  // If skip overlay is ON and defaults are present → auto-send after 1.5s grace.
  if (settings.clipSkipOverlay && settings.clipDefaultProject != null) {
    setClipStatus("Sending in 1.5s — click Cancel to stop…", "loading");
    setTimeout(() => {
      if ($("clip-overlay").hidden) return;
      submitClip();
    }, 1500);
  } else {
    setClipStatus("", "");
  }

  overlayEl.hidden = false;
}

function closeClipOverlay() {
  $("clip-overlay").hidden = true;
  setClipStatus("", "");
}

function setClipStatus(text, type) {
  const el = $("clip-status");
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

async function submitClip() {
  const sendBtn = $("btn-clip-send");
  sendBtn.disabled = true;
  setClipStatus("Sending…", "loading");
  try {
    const overlayEl = $("clip-overlay");
    const mode = overlayEl.dataset.clipMode || "page";
    const tabUrl = overlayEl.dataset.clipUrl || "";
    const tabTitle = overlayEl.dataset.clipTitle || "";
    const selectedText = overlayEl.dataset.clipSelection || "";

    const truthLevel = document.querySelector(
      'input[name="clipTruthLevel"]:checked',
    ).value;
    const project = $("clip-project").value.trim() || null;
    const useDefaults = $("clip-use-defaults").checked;

    // Content shape — the URL is ALWAYS attached so the memory item is
    // self-contained when listed later.
    let content = "";
    if (mode === "selection") {
      // Selection mode: link + selection only. Title surfaced as context.
      const header = tabTitle
        ? `From ${tabTitle} <${tabUrl}>`
        : `From ${tabUrl}`;
      content = `${header}\n\n${selectedText}`;
    } else {
      // Page mode: title + URL. (Phase 2 could add og:description / page
      // excerpt from a content script; for v1, title is the "data".)
      content = tabTitle
        ? `${tabTitle}\n${tabUrl}`
        : tabUrl;
    }
    const source = `chrome:${hostnameFromUrl(tabUrl)}`;

    if (!content.trim()) {
      setClipStatus("Nothing to send", "error");
      sendBtn.disabled = false;
      return;
    }

    // Resolve current team's slug.
    const team = state.teams.find((t) => t.id === state.activeTeamId);
    if (!team) {
      setClipStatus("No active team", "error");
      sendBtn.disabled = false;
      return;
    }

    // Build memory upsert payload.
    const tokenResp = await chrome.runtime.sendMessage({
      type: "GET_ID_TOKEN",
      silent: true,
    });
    const idToken = tokenResp && tokenResp.idToken;
    if (!idToken) {
      setClipStatus("Sign-in required", "error");
      sendBtn.disabled = false;
      return;
    }

    await chrome.runtime.sendMessage({
      type: "SEND_TO_BRAIN",
      idToken,
      payload: {
        content,
        team_scope: team.slug,
        project_scope: project,
        visibility: "team",
        confidence: 1.0,
        truth_level: truthLevel,
        source,
        validation_status: "pending",
      },
    });

    if (useDefaults) {
      await saveSettings(chrome.storage.sync, {
        clipDefaultProject: project,
        clipDefaultTruthLevel: truthLevel,
        clipSkipOverlay: true,
      });
    }

    setClipStatus("Sent ✓", "success");
    setTimeout(closeClipOverlay, 700);
  } catch (e) {
    setClipStatus(`Failed: ${e.message}`, "error");
  } finally {
    sendBtn.disabled = false;
  }
}

// ---------- HTTP helpers ----------

async function fetchJson(url, token, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

function renderEmptyTeams() {
  $("teamSelector").innerHTML = `<option disabled selected>No teams yet</option>`;
  const emptyEl = $("chat-empty");
  emptyEl.hidden = false;
  emptyEl.innerHTML = "";

  const wrapper = document.createElement("div");
  wrapper.style.cssText =
    "display:flex;flex-direction:column;gap:12px;align-items:center;text-align:center;max-width:320px;";

  const msg = document.createElement("p");
  msg.style.cssText = "margin:0;color:var(--xb-text-mute);line-height:1.5;";
  msg.textContent =
    "You don't belong to a team yet. Create your solo workspace to start clipping memory and chatting with Claude — you can invite teammates later.";
  wrapper.appendChild(msg);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "connect-btn";
  btn.style.maxWidth = "260px";
  btn.textContent = "✨ Create my solo workspace";
  btn.addEventListener("click", () => createSoloTeam(btn));
  wrapper.appendChild(btn);

  const hint = document.createElement("p");
  hint.style.cssText =
    "margin:0;font-size:11px;color:var(--xb-text-dim);line-height:1.45;";
  hint.innerHTML =
    "If you're part of a GitHub org, <strong>link GitHub above</strong> to discover org teams automatically.";
  wrapper.appendChild(hint);

  emptyEl.appendChild(wrapper);
  emptyEl.style.pointerEvents = "auto";
}

async function createSoloTeam(btn) {
  btn.disabled = true;
  btn.textContent = "Creating…";
  try {
    // /v1/teams/self-solo requires kind=user (Google ID token), NOT xbt_.
    // Reuse the SW silent path that already mints Google tokens for the
    // mint-and-connect flow.
    const tokenResp = await chrome.runtime.sendMessage({
      type: "GET_ID_TOKEN",
      silent: false,
    });
    const idToken = tokenResp && tokenResp.idToken;
    if (!idToken) {
      throw new Error(
        (tokenResp && tokenResp.error) || "could not get Google token",
      );
    }
    await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/self-solo`,
      idToken,
      "POST",
      {},
    );
    // Tell the SW to rebuild the CortX OS right-click submenu so the new
    // solo team appears there too. Fire-and-forget — the menu refresh is
    // a nice-to-have, not blocking the popup reboot.
    chrome.runtime.sendMessage({ type: "REFRESH_TEAMS_MENU" }).catch(() => {});
    // Re-boot so the new team appears in the selector and the chat connects.
    btn.textContent = "Ready ✓";
    setTimeout(() => boot(), 500);
  } catch (e) {
    console.warn("[xbrain] self-solo failed:", e);
    alert(`Could not create solo workspace: ${e.message}`);
    btn.disabled = false;
    btn.textContent = "✨ Create my solo workspace";
  }
}

// ---------- React to storage changes (token mint, GitHub link) ----------

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.xbt_token) {
    // Token appeared or was cleared — rerun boot.
    boot();
  }
});

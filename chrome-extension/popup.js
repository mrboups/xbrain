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
  buildMentionRegex,
  formatRelative,
  hostnameFromUrl,
  authorLabel,
  bubbleClass,
  provenanceLabel,
  brainSummaryLabel,
  savedToBrainLabel,
  sameDay,
  dayLabel,
} from "./chat_stream.js";
import { loadSettings, saveSettings } from "./settings.js";
import { handleOpenUrl, isSafeHttpUrl } from "./nudge_open.js";
import {
  THEME_STORAGE_KEY,
  resolveInitialTheme,
  applyTheme,
} from "./theme.js";

const MEMORY_API_BASE = "https://api.grooveos.app";

// ---------- App state (single instance per popup open) ----------

const state = {
  me: null,            // /v1/me result {id, source_user_id, email, ...}
  teams: [],           // [{id, slug, display_name, github_org}, ...]
  activeTeamId: null,
  centrifuge: null,    // Centrifuge instance
  subscription: null,  // active team:<id> subscription
  userSubscription: null, // user:<source_user_id> subscription (open_url nudges)
  presenceCount: 0,
  streamBuffer: new StreamBuffer(),
  nameCache: {},       // author_user_id → display name
  oldestLoadedTs: null,
  historyPaging: false,
  initialLoaded: false,
  // Agent-mention aliases for the active team. Built from the server's
  // effective list (GET /v1/teams/{id}/agent-aliases); defaults to ["agent"]
  // until the first fetch. Drives only the optimistic composer hint — the
  // server is authoritative for the actual summon.
  agentAliases: ["agent"],
  mentionRe: buildMentionRegex(["agent"]),
  // Catch-me-up (Phase 23, CATCHUP-01). The banner count is captured against
  // the STALE, pre-visit read cursor on team open (see switchTeam ordering).
  //   markReadInFlight — dedupe guard so scroll/focus don't spam POST /mark-read
  //   since           — the current unread window (`since` from unread-summary)
  //   dismissedSince  — the window the user dismissed; suppresses re-nag for the
  //                     SAME window (undefined = nothing dismissed yet)
  switchGen: 0,  // WR-03: incremented per switchTeam; a stale invocation's tail bails
  catchup: {
    markReadInFlight: false,
    since: null,
    dismissedSince: undefined,
    // BL-01: the scroll/focus auto-mark-read must NOT fire until switchTeam's
    // initial refreshUnreadBanner()+markRead() pair has completed — otherwise the
    // scroll event that loadInitialHistory's scrollToBottom() emits races ahead,
    // bumps the server cursor, and the banner count comes back 0 (banner suppressed).
    readyForAutoMarkRead: false,
    activeMessageId: null,  // WR-01: the catch-me-up stream currently being rendered
  },
};

// ---------- DOM refs ----------

const $ = (id) => document.getElementById(id);

// ---------- Boot ----------

document.addEventListener("DOMContentLoaded", async () => {
  // Apply the theme before anything paints so there is no light/dark flash.
  await wireTheme();
  wireHeader();
  wireConnectionCard();
  wireComposer();
  wireClipOverlay();
  wireSendLink();
  wireInvite();
  wireCatchup();
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
  // "+" beside the last team square. It shows the same create-or-join panel the
  // empty state uses, because "add a team" means both — a founder types a name, an
  // invitee pastes a code, and neither should need a different entry point.
  const btnTeamAdd = $("btn-team-add");
  if (btnTeamAdd) btnTeamAdd.addEventListener("click", () => renderEmptyTeams());

  // "add to memory" header button — opens the clip overlay (same as the
  // old 📎 composer button). The composer 📎 is now the file attach trigger.
  const btnAddToMemory = $("btn-add-to-memory");
  if (btnAddToMemory) {
    btnAddToMemory.addEventListener("click", openClipOverlay);
  }
  // "board" header button (Phase 26, BOARD-01) — opens the ACTIVE team's
  // collaborative board in a new tab, mirroring how btn-open-librechat opens a
  // tab. Wired here, alongside the other header actions, rather than in its own
  // wireX() because it owns no overlay/panel — the board is a tab, not a popup
  // surface.
  const btnBoard = $("btn-board");
  if (btnBoard) {
    btnBoard.addEventListener("click", openTeamBoard);
  }
}

// ---------- Collaborative board (Phase 26, BOARD-01) ----------
//
// A single header action that opens the ACTIVE team's board in a browser tab.
// The extension does NOT own the board UI (Excalidraw is 2.76 MB behind a React
// peer — it cannot live in a 400 px popup, D-26-01). It asks memory-api for the
// team's board and opens the URL the server returns. Everything security-bearing
// (membership check, board-token mint) already happened server-side in 26-02.
//
// The server returns `open_url` with a short-lived board token in its URL
// fragment — a bearer secret for that one board (T-26-32). Two rules follow:
//   1. The extension NEVER builds this URL; the server owns it (T-26-33).
//   2. The URL / token is NEVER logged (only err.message on failure, T-26-32).
// The returned URL is scheme-validated with the existing isSafeHttpUrl guard so
// a non-http(s) scheme can never reach chrome.tabs.create (T-26-31) — the same
// helper that guards the Phase-22 nudge path. No new manifest permission is
// needed: chrome.tabs.create already opens teammate-supplied URLs (Phase 22).
async function openTeamBoard() {
  if (!state.activeTeamId) return; // nothing to open yet
  const btn = $("btn-board");
  // Disable for the duration of the request so a double-click can't fire two
  // board-creation calls (T-26-34); re-enabled in finally.
  if (btn) btn.disabled = true;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/boards`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: "Team board" }),
      },
    );
    if (!res.ok) throw new Error(`board ${res.status}`);
    const data = await res.json();
    // Validate the server-built URL's scheme before handing it to the browser,
    // and never log it — the fragment is a bearer secret for this one board.
    if (!data.open_url || !isSafeHttpUrl(data.open_url)) {
      throw new Error("board url rejected");
    }
    chrome.tabs.create({ url: data.open_url });
  } catch (err) {
    // Report the FAILURE only — never the URL or the token. Fail-soft: a short
    // English status flashes on the button itself (no dedicated status surface,
    // the board owns no overlay), then the label is restored.
    console.warn("[xbrain] open board failed:", err && err.message);
    flashBoardError(btn);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Briefly swap the board button's label to a short English failure state, then
// restore it. Adds no new id/overlay — the board is a bare header action.
function flashBoardError(btn) {
  if (!btn) return;
  const prev = btn.textContent;
  btn.textContent = "board failed";
  setTimeout(() => {
    btn.textContent = prev;
  }, 2000);
}

// ---------- Send-link affordance (Phase 22, D-22-05) ----------
//
// A small no-navigation control in the header opens an overlay to pick a
// SAME-TEAM member + a URL and POST the Plan-01 nudge-open endpoint. Every
// server-side check (target membership, url scheme, per-sender rate limit) is
// authoritative; the client isSafeHttpUrl pre-check is UX only, NOT the security
// boundary (T-22-13). The recipient still has to click their notification for a
// tab to open (D-22-02) — this control only sends the request.

function wireSendLink() {
  // The header button is now the PEOPLE icon and opens the members overlay: you
  // almost always mean "send this page to that person", so the recipient should be
  // a click, not a form. The old URL-first sendlink overlay stays wired below as
  // the mechanism the people overlay delegates to.
  const openBtn = $("btn-send-link");
  if (openBtn) openBtn.addEventListener("click", openPeople);
  const peopleClose = $("btn-people-close");
  if (peopleClose) peopleClose.addEventListener("click", closePeople);
  const peopleCancel = $("btn-people-cancel");
  if (peopleCancel) peopleCancel.addEventListener("click", closePeople);
  const sendAll = $("btn-people-send-all");
  if (sendAll) sendAll.addEventListener("click", sendLinkToEveryone);
  const closeBtn = $("btn-sendlink-close");
  if (closeBtn) closeBtn.addEventListener("click", closeSendLink);
  const cancelBtn = $("btn-sendlink-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", closeSendLink);
  const submitBtn = $("btn-sendlink-submit");
  if (submitBtn) submitBtn.addEventListener("click", submitSendLink);
}

// ---------- People overlay (members + send a link/file) ----------

function setPeopleStatus(text, type) {
  const el = $("people-status");
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

function closePeople() {
  const panel = $("people-panel");
  if (panel) panel.hidden = true;
  setPeopleStatus("", "");
}

/** Current tab URL — the default thing you want to send. Fail-soft: an unreadable
 *  tab just leaves the field empty rather than blocking the overlay. */
async function currentTabUrl() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab && tab.url && /^https?:/i.test(tab.url) ? tab.url : "";
  } catch {
    return "";
  }
}

async function openPeople(opts) {
  const panel = $("people-panel");
  if (!panel) return;
  if (!state.activeTeamId) {
    setPeopleStatus("No active team.", "error");
    return;
  }
  panel.hidden = false;
  setPeopleStatus("", "");
  const urlInput = $("people-url");
  if (urlInput && !urlInput.value) urlInput.value = await currentTabUrl();
  await renderPeopleList(opts && opts.focusUserId);
}

/**
 * List the team's members, marking who is connected right now.
 *
 * Presence comes from Centrifugo's `presence()` RPC, which returns the connected
 * clients for the team channel. If the channel does not expose presence the list
 * still renders — every row simply shows no dot, because an invented "offline" is
 * worse than an absent one.
 */
async function renderPeopleList(focusUserId) {
  const list = $("people-list");
  if (!list) return;
  list.innerHTML = "";

  let members = [];
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/members`,
      { headers: { Authorization: `Bearer ${xbt_token}` } },
    );
    if (!res.ok) {
      setPeopleStatus(`Could not load members (HTTP ${res.status}).`, "error");
      return;
    }
    members = await res.json();
  } catch {
    setPeopleStatus("Network error loading members.", "error");
    return;
  }

  // Who is online — best effort, never fatal.
  const online = new Set();
  try {
    const p = await state.subscription.presence();
    for (const c of Object.values(p.clients || {})) {
      if (c && c.user) online.add(String(c.user));
    }
  } catch {
    /* presence unavailable on this channel — rows render without a dot */
  }

  for (const m of members) {
    const row = document.createElement("li");
    row.className = "xb-people-row";
    // Opened by clicking this person's name in the chat — mark their row so the
    // overlay answers "that one" instead of making you find them again.
    if (focusUserId && String(m.user_id || m.id) === String(focusUserId)) {
      row.classList.add("is-focused");
    }

    const dot = document.createElement("span");
    dot.className =
      "xb-people-dot" + (online.has(String(m.source_user_id)) ? " is-online" : "");
    dot.title = online.has(String(m.source_user_id)) ? "Active now" : "Not active";
    row.appendChild(dot);

    const name = document.createElement("span");
    name.className = "xb-people-name";
    // textContent — member names are user-supplied strings.
    name.textContent = m.display_name || m.email || m.source_user_id || "member";
    row.appendChild(name);

    const linkBtn = document.createElement("button");
    linkBtn.type = "button";
    linkBtn.className = "xb-btn-secondary";
    linkBtn.textContent = "Link";
    linkBtn.title = "Send the link above to this person";
    linkBtn.addEventListener("click", () => sendLinkToMember(m, linkBtn));
    row.appendChild(linkBtn);

    const fileBtn = document.createElement("button");
    fileBtn.type = "button";
    fileBtn.className = "xb-btn-secondary";
    fileBtn.textContent = "File";
    fileBtn.title = "Upload a file and send it to this person";
    fileBtn.addEventListener("click", () => pickFileForMember(m));
    row.appendChild(fileBtn);

    list.appendChild(row);
  }

  if (!members.length) setPeopleStatus("No members yet.", "");
}

/**
 * Send a FILE to one member: upload it to the team, then nudge the signed URL the
 * upload returns.
 *
 * The file goes through the ordinary team media path — it becomes a team memory_item
 * like any other upload — and the recipient gets the same consent notification a link
 * gets. `signed_url` is used rather than `raw_path` because the recipient's browser
 * follows a bare URL and cannot send the Bearer header `raw_path` requires.
 */
function pickFileForMember(member) {
  const picker = $("people-file");
  if (!picker) return;
  picker.value = "";                       // re-picking the same file must re-fire
  picker.onchange = async () => {
    const file = picker.files && picker.files[0];
    if (!file) return;
    // X-Team-Scope is the team SLUG, not its id — derived from state.teams the same
    // way uploadFile() does, so both upload paths agree on the scope they write to.
    const team = state.teams.find((t) => t.id === state.activeTeamId);
    if (!team) {
      setPeopleStatus("No active team.", "error");
      return;
    }
    setPeopleStatus(`Uploading ${file.name}…`, "loading");
    try {
      const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
      const form = new FormData();
      form.append("file", file);
      form.append("caption", file.name);
      const up = await fetch(`${MEMORY_API_BASE}/v1/media/upload`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "X-Team-Scope": team.slug,
        },
        body: form,
      });
      if (up.status !== 201) {
        setPeopleStatus(
          up.status === 413
            ? "That file is too large (25 MB max)."
            : `Upload failed (HTTP ${up.status}).`,
          "error",
        );
        return;
      }
      const { signed_url } = await up.json();
      if (!signed_url) {
        setPeopleStatus("Upload succeeded but returned no shareable link.", "error");
        return;
      }
      const status = await postNudge(
        member.user_id || member.id,
        `${MEMORY_API_BASE}${signed_url}`,
      );
      setPeopleStatus(
        status === 202
          ? `Sent ${file.name} to ${member.display_name || "them"} ✓`
          : mapNudgeError(status),
        status === 202 ? "success" : "error",
      );
    } catch {
      setPeopleStatus("Network error — try again.", "error");
    }
  };
  picker.click();
}

/** Send the composed link to ONE member (Phase 22 nudge). */
async function sendLinkToMember(member, btn) {
  const url = ($("people-url") || {}).value || "";
  if (!url.trim()) {
    setPeopleStatus("Enter a link to send.", "error");
    return;
  }
  if (btn) btn.disabled = true;
  try {
    const status = await postNudge(member.user_id || member.id, url.trim());
    setPeopleStatus(
      status === 202
        ? `Sent to ${member.display_name || "them"} ✓`
        : mapNudgeError(status),
      status === 202 ? "success" : "error",
    );
  } finally {
    if (btn) btn.disabled = false;
  }
}

/**
 * Send to the whole team.
 *
 * TODO(server): this fans out ONE request per member from the client because
 * /v1/teams/{id}/nudge-open takes a single target_user_id. That is fine for a small
 * team but it burns the caller's rate-limit budget and is not atomic — a proper
 * team-wide nudge belongs server-side (one call, one membership read, one publish
 * per member). Tracked in .planning/BACKLOG.md.
 */
async function sendLinkToEveryone() {
  const url = ($("people-url") || {}).value || "";
  if (!url.trim()) {
    setPeopleStatus("Enter a link to send.", "error");
    return;
  }
  const btn = $("btn-people-send-all");
  if (btn) btn.disabled = true;
  setPeopleStatus("Sending…", "loading");
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/members`,
      { headers: { Authorization: `Bearer ${xbt_token}` } },
    );
    const members = res.ok ? await res.json() : [];
    let sent = 0;
    let failed = 0;
    for (const m of members) {
      const id = m.user_id || m.id;
      if (!id) continue;
      const status = await postNudge(id, url.trim());
      // The server 422s a self-nudge; that is expected here and not a failure.
      if (status === 202 || status === 422) sent += status === 202 ? 1 : 0;
      else failed += 1;
    }
    setPeopleStatus(
      failed ? `Sent to ${sent}, ${failed} failed.` : `Sent to ${sent} teammate${sent === 1 ? "" : "s"} ✓`,
      failed ? "error" : "success",
    );
  } catch {
    setPeopleStatus("Network error — try again.", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** One nudge POST. Returns the HTTP status so callers can map it themselves. */
async function postNudge(targetUserId, url) {
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/nudge-open`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ target_user_id: targetUserId, url }),
      },
    );
    return res.status;
  } catch {
    return 0;
  }
}

// mapNudgeError is shared with the sendlink overlay and defined further down —
// same endpoint, same status codes, so one mapping serves both surfaces.

async function openSendLink() {
  const panel = $("sendlink-panel");
  if (!panel) return;
  panel.hidden = false;
  setSendLinkStatus("", "");
  await populateSendLinkMembers();
}

function closeSendLink() {
  const panel = $("sendlink-panel");
  if (panel) panel.hidden = true;
}

// Lazily fetch the active team's members and fill the picker, excluding the
// current user (you cannot nudge yourself) and any blocked member.
async function populateSendLinkMembers() {
  const select = $("sendlink-member");
  if (!select) return;
  if (!state.activeTeamId) {
    select.innerHTML = `<option value="" disabled selected>No active team</option>`;
    return;
  }
  select.innerHTML = `<option value="" disabled selected>Loading members…</option>`;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const members = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/members`,
      xbt_token,
    );
    const myId = state.me && state.me.id;
    const others = (Array.isArray(members) ? members : []).filter(
      (m) => m.user_id !== myId && !m.blocked_at,
    );
    if (!others.length) {
      select.innerHTML = `<option value="" disabled selected>No other members on this team</option>`;
      return;
    }
    select.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.selected = true;
    placeholder.textContent = "Choose a member…";
    select.appendChild(placeholder);
    for (const m of others) {
      const opt = document.createElement("option");
      opt.value = m.user_id;
      // display_name / email are server-provided; use textContent (no innerHTML).
      opt.textContent = m.display_name || m.email || m.user_id;
      select.appendChild(opt);
    }
  } catch (e) {
    console.warn("[xbrain] send-link members fetch failed:", e);
    select.innerHTML = `<option value="" disabled selected>Couldn't load members</option>`;
  }
}

// Validate + POST the nudge. The client scheme pre-check blocks the obvious
// cases early; the server re-validates and is the real boundary.
async function submitSendLink() {
  const select = $("sendlink-member");
  const urlInput = $("sendlink-url");
  const submitBtn = $("btn-sendlink-submit");
  if (!select || !urlInput) return;

  const targetUserId = select.value;
  const url = urlInput.value.trim();
  if (!targetUserId) {
    setSendLinkStatus("Pick a team member first.", "error");
    return;
  }
  if (!isSafeHttpUrl(url)) {
    setSendLinkStatus("Enter a valid http or https link.", "error");
    return;
  }
  if (!state.activeTeamId) {
    setSendLinkStatus("No active team.", "error");
    return;
  }

  if (submitBtn) submitBtn.disabled = true;
  setSendLinkStatus("Sending…", "loading");
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/nudge-open`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ target_user_id: targetUserId, url }),
      },
    );
    if (res.status === 202) {
      setSendLinkStatus("Sent ✓", "success");
      urlInput.value = "";
    } else {
      setSendLinkStatus(mapNudgeError(res.status), "error");
    }
  } catch (e) {
    console.warn("[xbrain] send-link POST failed:", e);
    setSendLinkStatus("Network error — try again.", "error");
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// Map the nudge-open endpoint's rejection codes to clear English text.
function mapNudgeError(status) {
  if (status === 403) return "That member is not on this team.";
  if (status === 422) return "Rejected — that is you, or the link is not a plain http/https URL.";
  if (status === 429) return "Too many link requests — wait a moment and retry.";
  if (status === 404) return "Team not found.";
  if (status === 0) return "Network error — try again.";
  return `Could not send (HTTP ${status}).`;
}

function setSendLinkStatus(text, type) {
  const el = $("sendlink-status");
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

// ---------- Invite-code affordance (Phase 25, JOINCODE-01) ----------
//
// A header "invite" control opens an overlay with two surfaces:
//   1. ADMIN mint — POST /v1/teams/{activeTeamId}/invite-codes reveals the
//      plaintext code ONCE (rendered via textContent, never innerHTML — T-25-17)
//      with a Copy button. The client cannot reliably tell admin status from the
//      my-teams payload, so it shows the action to everyone and surfaces the
//      server's authoritative 403 as a clear message (T-25-16) — the server is
//      the real authZ boundary.
//   2. JOIN — POST /v1/teams/join-by-code {code} redeems a pasted code, then
//      refreshes the team list so the newly-joined team appears.
// The revealed code is cleared on close so the one-time secret does not linger in
// the DOM across opens (T-25-19). popup.js hardcodes no code or team — it renders
// only server responses (T-25-18).

function wireInvite() {
  const openBtn = $("btn-invite");
  if (openBtn) openBtn.addEventListener("click", openInvite);
  const closeBtn = $("btn-invite-close");
  if (closeBtn) closeBtn.addEventListener("click", closeInvite);
  const cancelBtn = $("btn-invite-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", closeInvite);
  const mintBtn = $("btn-invite-mint");
  if (mintBtn) mintBtn.addEventListener("click", mintInvite);
  const copyBtn = $("btn-invite-copy");
  if (copyBtn) copyBtn.addEventListener("click", copyInvite);
  const joinBtn = $("btn-invite-join");
  if (joinBtn) joinBtn.addEventListener("click", joinByCode);
  const emailBtn = $("btn-invite-email");
  if (emailBtn) emailBtn.addEventListener("click", inviteByEmail);
}

/**
 * ADMIN — add someone to the team by email.
 *
 * The endpoint resolves an EXISTING user row, so it 404s for anyone who has never
 * signed into xbrain. That is not an error to paper over: the copy points straight
 * back at the invite link, which is the path that works for a brand-new teammate.
 */
async function inviteByEmail() {
  if (!state.activeTeamId) {
    setInviteEmailStatus("No active team.", "error");
    return;
  }
  const input = $("invite-email");
  const email = input ? input.value.trim() : "";
  if (!email) {
    setInviteEmailStatus("Enter an email address.", "error");
    return;
  }
  const btn = $("btn-invite-email");
  if (btn) btn.disabled = true;
  setInviteEmailStatus("Adding...", "loading");
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/invite`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, role: "member" }),
      },
    );
    if (res.status === 201) {
      if (input) input.value = "";
      setInviteEmailStatus(`${email} added to the team ✓`, "success");
    } else if (res.status === 404) {
      setInviteEmailStatus(
        "No xbrain account for that email — send them the invite link above instead.",
        "error",
      );
    } else if (res.status === 403) {
      setInviteEmailStatus("Only a team admin can add members.", "error");
    } else if (res.status === 409) {
      setInviteEmailStatus("They are already on this team.", "success");
    } else {
      setInviteEmailStatus(`Could not add them (HTTP ${res.status}).`, "error");
    }
  } catch (e) {
    console.warn("[xbrain] invite by email failed");
    setInviteEmailStatus("Network error — try again.", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openInvite() {
  const panel = $("invite-panel");
  if (!panel) return;
  panel.hidden = false;
  setInviteStatus("", "");
  setInviteJoinStatus("", "");
  const row = $("invite-code-row");
  if (row) row.hidden = true;
}

function closeInvite() {
  const panel = $("invite-panel");
  if (panel) panel.hidden = true;
  // Clear the one-time revealed code so it does not persist across opens (T-25-19).
  const out = $("invite-code-output");
  if (out) out.textContent = "";
  const row = $("invite-code-row");
  if (row) row.hidden = true;
  // WR-02: a PASTED code is a bearer secret too — clear the join input (and both status
  // lines) so it does not survive closing the overlay unsubmitted.
  const joinInput = $("invite-join-code");
  if (joinInput) joinInput.value = "";
  const emailInput = $("invite-email");
  if (emailInput) emailInput.value = "";
  const mintStatus = $("invite-status");
  if (mintStatus) mintStatus.textContent = "";
  const joinStatus = $("invite-join-status");
  if (joinStatus) joinStatus.textContent = "";
  const emailStatus = $("invite-email-status");
  if (emailStatus) emailStatus.textContent = "";
}

// ADMIN action — mint an invite code and reveal the plaintext ONCE. The server
// 403s non-admins; the client only presents the outcome (T-25-16).
async function mintInvite() {
  if (!state.activeTeamId) {
    setInviteStatus("No active team.", "error");
    return;
  }
  const mintBtn = $("btn-invite-mint");
  if (mintBtn) mintBtn.disabled = true;
  setInviteStatus("Creating...", "loading");
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/invite-codes`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({}),
      },
    );
    if (res.status === 201) {
      const j = await res.json();
      // textContent — never innerHTML — the code is an untrusted server string (T-25-17).
      $("invite-code-output").textContent = j.code;
      const row = $("invite-code-row");
      if (row) row.hidden = false;
      setInviteStatus("Code created — copy it now (shown once).", "success");
    } else {
      setInviteStatus(mapMintError(res.status), "error");
    }
  } catch (e) {
    console.warn("[xbrain] mint invite failed:", e);
    setInviteStatus("Network error — try again.", "error");
  } finally {
    if (mintBtn) mintBtn.disabled = false;
  }
}

// Map the mint endpoint's rejection codes to clear English text.
function mapMintError(status) {
  if (status === 403) return "Only a team admin can create invite codes.";
  if (status === 429) return "Too many requests — wait a moment.";
  if (status === 404) return "Team not found.";
  return `Could not create a code (HTTP ${status}).`;
}

/**
 * Build the shareable join link for an invite code.
 *
 * The code travels in the URL FRAGMENT (`#c=`), never a query string: a fragment is
 * never transmitted to a server, so the invite secret cannot land in hosting logs or
 * in a Referer header. app-site/join/ reads it and strips it on load.
 *
 * The origin is DERIVED from MEMORY_API_BASE (drop a leading `api.`) rather than
 * hardcoded a second time, so a rebrand of that one constant carries the join link
 * with it instead of silently pointing at the old domain.
 */
function buildJoinLink(code) {
  let origin;
  try {
    const u = new URL(MEMORY_API_BASE);
    u.hostname = u.hostname.replace(/^api\./, "");
    origin = u.origin;
  } catch (e) {
    return null;
  }
  return `${origin}/join/#c=${encodeURIComponent(code)}`;
}

// Copies the shareable LINK, not the bare code — the recipient clicks it and joins,
// with no code to paste. The code stays visible above as the manual fallback.
async function copyInvite() {
  const out = $("invite-code-output");
  const code = out ? out.textContent : "";
  if (!code) {
    setInviteStatus("Create a code first.", "error");
    return;
  }
  const link = buildJoinLink(code);
  if (!link) {
    setInviteStatus("Could not build the link — copy the code instead.", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(link);
    setInviteStatus("Invite link copied ✓ — share it, it joins in one click.", "success");
  } catch (e) {
    // Never log the link or the code — both carry the invite secret.
    console.warn("[xbrain] copy invite link failed");
    setInviteStatus("Copy failed — select the code manually.", "error");
  }
}

// ANY MEMBER — redeem a pasted code. On success, refresh the team list so the
// newly-joined team shows up in the selector.
async function joinByCode() {
  const input = $("invite-join-code");
  const code = input ? input.value.trim() : "";
  if (!code) {
    setInviteJoinStatus("Paste a code first.", "error");
    return;
  }
  const joinBtn = $("btn-invite-join");
  if (joinBtn) joinBtn.disabled = true;
  setInviteJoinStatus("Joining...", "loading");
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(`${MEMORY_API_BASE}/v1/teams/join-by-code`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${xbt_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ code }),
    });
    if (res.status === 200) {
      const j = await res.json();
      if (j.already_member) {
        setInviteJoinStatus("You're already on that team.", "success");
      } else {
        // j.display_name is a server string; setInviteJoinStatus renders it via
        // textContent (never innerHTML) — no XSS surface (T-25-17).
        setInviteJoinStatus(`Joined ${j.display_name} ✓`, "success");
      }
      if (input) input.value = "";
      await refreshTeamsAfterJoin();
    } else {
      setInviteJoinStatus(mapJoinError(res.status), "error");
    }
  } catch (e) {
    console.warn("[xbrain] join-by-code failed:", e);
    setInviteJoinStatus("Network error — try again.", "error");
  } finally {
    if (joinBtn) joinBtn.disabled = false;
  }
}

// Map the join endpoint's rejection codes. The server returns a single generic
// 404 for invalid/expired/revoked/exhausted (no oracle) — the client mirrors that.
function mapJoinError(status) {
  if (status === 404) return "That code is invalid, expired, or used up.";
  if (status === 429) return "Too many attempts — wait a moment.";
  return `Could not join (HTTP ${status}).`;
}

// Refresh the team list after a successful join. If the popup had no active team
// yet (the user just joined their FIRST team), run the full boot path so
// centrifugo connects and the chat loads; otherwise re-fetch my-teams and
// re-render the selector so the new team appears without a reconnect.
async function refreshTeamsAfterJoin() {
  if (!state.activeTeamId) {
    await boot();
    return;
  }
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const teams = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/my-teams`,
      xbt_token,
    );
    if (Array.isArray(teams) && teams.length) {
      state.teams = teams;
      renderTeamSelector();
    }
  } catch (e) {
    console.warn("[xbrain] team refresh after join failed:", e);
  }
}

// Two single-target status helpers (mirror setSendLinkStatus) — one per status
// div. Kept separate so both #invite-status and #invite-join-status are bound by
// literal $() calls (the popup contract test freezes them).
function setInviteStatus(text, type) {
  const el = $("invite-status");
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

function setInviteJoinStatus(text, type) {
  const el = $("invite-join-status");
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

function setInviteEmailStatus(text, type) {
  const el = $("invite-email-status");
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

// ---------- Theme (in-popup light/dark toggle) ----------

/**
 * Wire the header light/dark segmented toggle. Theme logic lives in the pure
 * theme.js module; this function owns the impure parts: reading/writing
 * chrome.storage.local and stamping the DOM. The stored choice wins over the
 * OS preference and persists across popup opens (Plan 20-01).
 */
async function wireTheme() {
  const root = document.documentElement;
  const lightBtn = $("btn-theme-light");
  const darkBtn = $("btn-theme-dark");

  // Reflect the active mode on the segmented control (aria-pressed).
  const reflect = (mode) => {
    if (lightBtn) lightBtn.setAttribute("aria-pressed", String(mode === "light"));
    if (darkBtn) darkBtn.setAttribute("aria-pressed", String(mode === "dark"));
  };

  // Apply a mode to the DOM + control; optionally persist the explicit choice.
  const set = async (mode, persist) => {
    applyTheme(root, mode);
    reflect(mode);
    if (persist) {
      try {
        await chrome.storage.local.set({ [THEME_STORAGE_KEY]: mode });
      } catch (e) {
        console.warn("[xbrain] theme persist failed:", e);
      }
    }
  };

  // Boot: stored choice wins; first-ever open falls back to prefers-color-scheme.
  let storedTheme = null;
  try {
    const got = await chrome.storage.local.get([THEME_STORAGE_KEY]);
    storedTheme = got ? got[THEME_STORAGE_KEY] : null;
  } catch (e) {
    console.warn("[xbrain] theme read failed:", e);
  }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  await set(resolveInitialTheme({ storedTheme, prefersDark }), false);

  if (lightBtn) lightBtn.addEventListener("click", () => set("light", true));
  if (darkBtn) darkBtn.addEventListener("click", () => set("dark", true));
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
  renderTeamRail();
}

/** Two-letter mark for a team square — initials of the first two words, else the
 *  first two characters. Falls back to "?" so a nameless team still renders. */
function teamInitials(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/**
 * Render one square per team. Clicking a square switches the chat; the active one
 * is filled. Unread counts are badged onto the square itself so a team with new
 * messages is visible without opening anything — which a <select> could not do.
 */
function renderTeamRail() {
  const rail = $("team-rail");
  if (!rail) return;
  rail.innerHTML = "";
  for (const t of state.teams) {
    const name = t.display_name || t.slug;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "xb-team-icon";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", String(t.id === state.activeTeamId));
    btn.title = name;
    btn.setAttribute("aria-label", name);
    btn.dataset.teamId = t.id;
    btn.textContent = teamInitials(name);
    btn.addEventListener("click", () => {
      if (t.id !== state.activeTeamId) switchTeam(t.id);
    });
    rail.appendChild(btn);
  }
  refreshTeamBadges();
}

/**
 * Badge each INACTIVE team with its unread count (Phase 23's unread-summary, the
 * same cursor the catch-me-up banner reads). The active team is skipped on purpose:
 * you are looking at it, and switchTeam marks it read a moment later, so a badge
 * there would flash and vanish. Fail-soft — a badge is a nicety, never a blocker.
 */
async function refreshTeamBadges() {
  const rail = $("team-rail");
  if (!rail) return;
  let token;
  try {
    ({ xbt_token: token } = await chrome.storage.local.get(["xbt_token"]));
  } catch { return; }
  if (!token) return;

  for (const btn of rail.querySelectorAll(".xb-team-icon")) {
    const id = btn.dataset.teamId;
    if (!id || id === state.activeTeamId) {
      const old = btn.querySelector(".xb-team-badge");
      if (old) old.remove();
      continue;
    }
    try {
      const res = await fetch(
        `${MEMORY_API_BASE}/v1/teams/${id}/unread-summary`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!res.ok) continue;
      const { count } = await res.json();
      let badge = btn.querySelector(".xb-team-badge");
      if (!count) {
        if (badge) badge.remove();
        continue;
      }
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "xb-team-badge";
        btn.appendChild(badge);
      }
      badge.textContent = count > 99 ? "99+" : String(count);
    } catch {
      /* one team's badge failing must not stop the others */
    }
  }
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
    // The prior user subscription belonged to the old instance; drop the ref so
    // subscribeUserChannel() re-creates it on the fresh instance below.
    state.userSubscription = null;
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

  // Subscribe to the caller's OWN user channel for direct open_url nudges. The
  // centrifugo-token endpoint already grants `user:<source_user_id>`, so this is
  // just claiming a channel the token authorizes. Built ONCE per connection and
  // independent of team switches (a team switch never touches this sub).
  subscribeUserChannel();

  state.centrifuge.connect();
}

// ---------- User channel (direct open_url nudges — Phase 22, D-22-02) ----------

/**
 * Subscribe to the caller's own `user:<source_user_id>` Centrifugo channel and
 * route incoming `open_url` events to the consent-gated handler. Idempotent: a
 * second call while a subscription is live is a no-op, so a reconnect or team
 * switch can never double-subscribe.
 *
 * The handler NEVER opens a browser tab — it only raises a notification via
 * nudge_open.handleOpenUrl and stashes the url in chrome.storage.session. The
 * tab is opened ONLY when the recipient clicks that notification, wired in
 * background.js (the required user gesture + the consent gate).
 */
function subscribeUserChannel() {
  if (!state.centrifuge) return;
  if (!state.me || !state.me.source_user_id) return;
  if (state.userSubscription) return; // already subscribed on this instance

  const channelName = `user:${state.me.source_user_id}`;
  const sub = state.centrifuge.newSubscription(channelName);
  sub.on("publication", (ctx) => handleUserPublication(ctx.data));
  sub.subscribe();
  state.userSubscription = sub;
}

/**
 * Route a frame from the user channel. Only `open_url` events are handled; any
 * other frame is ignored. Delegates entirely to nudge_open.handleOpenUrl with
 * chrome-backed deps — the popup itself has no tab-opening capability here.
 *
 * @param {{type?: string}|null|undefined} data
 */
async function handleUserPublication(data) {
  if (!data || !data.type) return;

  // Ephemeral "Catch me up" summary frames (Phase 23, D-23-04). They arrive on
  // the caller's OWN user channel, render into #catchup-summary-text via
  // textContent (XSS-safe, T-23-08), and are NEVER inserted into #message-list
  // nor persisted. The open_url branch below is preserved unchanged.
  // WR-01: catch-me-up frames are correlated by message_id so two overlapping
  // streams (double-click / team switch) can't interleave into one panel. A
  // `start` claims the panel; later frames whose message_id doesn't match the
  // active stream are dropped. The run button stays disabled until the active
  // stream's end/error frame arrives.
  if (data.type === "catchup_stream_start") {
    state.catchup.activeMessageId = data.message_id || null;
    const textEl = $("catchup-summary-text");
    const panel = $("catchup-summary");
    if (textEl) textEl.textContent = "";
    if (panel) panel.hidden = false;
    return;
  }
  if (data.type === "catchup_stream_chunk") {
    if (data.message_id !== state.catchup.activeMessageId) return;
    const textEl = $("catchup-summary-text");
    if (textEl && typeof data.delta === "string") {
      textEl.textContent += data.delta;
    }
    return;
  }
  if (data.type === "catchup_stream_end") {
    if (data.message_id !== state.catchup.activeMessageId) return;
    // Empty-window close frame carries a `note` and no streamed text — surface
    // it so the panel isn't left blank.
    const textEl = $("catchup-summary-text");
    if (textEl && data.note && !textEl.textContent) {
      textEl.textContent = data.note;
    }
    state.catchup.activeMessageId = null;
    const runBtn = $("btn-catchup-run");
    if (runBtn) runBtn.disabled = false;  // WR-01: stream done, allow another run
    return;
  }
  if (data.type === "catchup_stream_error") {
    if (data.message_id !== state.catchup.activeMessageId) return;
    const textEl = $("catchup-summary-text");
    if (textEl) {
      textEl.textContent += `\n\n(error: ${data.error || "unknown"})`;
    }
    state.catchup.activeMessageId = null;
    const runBtn = $("btn-catchup-run");
    if (runBtn) runBtn.disabled = false;
    return;
  }

  if (data.type !== "open_url") return;
  try {
    await handleOpenUrl(data, {
      getSettings: () => loadSettings(chrome.storage.sync),
      notify: (opts) =>
        new Promise((resolve) => chrome.notifications.create(opts, resolve)),
      persistPending: (id, url) =>
        chrome.storage.session.set({ ["nudge_" + id]: url }),
      // Used only when the recipient opted into auto-open. Re-validates at the
      // point of action exactly like the notification-click path does — the URL
      // is never trusted just because it already passed a check upstream.
      openDirect: (url) => {
        if (!isSafeHttpUrl(url)) return;
        chrome.tabs.create({ url });
      },
    });
  } catch (e) {
    console.warn("[xbrain] open_url handling failed:", e);
  }
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
  // Reset catch-me-up UI/state — dismissal and window are per-team-visit.
  state.catchup.since = null;
  state.catchup.dismissedSince = undefined;
  state.catchup.readyForAutoMarkRead = false;  // BL-01: armed only after the banner is captured
  const switchGen = ++state.switchGen;  // WR-03: re-entrancy token for this switch
  const prevBanner = $("catchup-banner");
  if (prevBanner) prevBanner.hidden = true;
  const prevSummary = $("catchup-summary");
  if (prevSummary) prevSummary.hidden = true;
  clearMessageList();
  setPresence(0);

  const channelName = `team:${teamId}`;
  state.subscription = state.centrifuge.newSubscription(channelName);
  state.subscription.on("publication", (ctx) => handlePublication(ctx.data));
  state.subscription.on("join", () => updatePresenceFromAPI());
  state.subscription.on("leave", () => updatePresenceFromAPI());
  state.subscription.on("subscribed", () => updatePresenceFromAPI());
  state.subscription.subscribe();

  // Refresh the team's agent-alias list so the composer hint matches exactly
  // what the server will summon for THIS team — no cross-team leakage, and a
  // name an admin just changed takes effect with no restart. Fail-soft.
  await refreshAgentAliases();

  await loadInitialHistory();

  // WR-03: if the user switched to another team while we were loading, this stale
  // invocation must NOT run its catch-up tail — otherwise it would mark-read /
  // refresh the banner for whatever team is active NOW, mutating the wrong cursor.
  if (switchGen !== state.switchGen) return;

  // CATCH-ME-UP ordering is LOAD-BEARING (checker BLOCKER, D-23-03): capture the
  // unread banner against the STALE, pre-visit read cursor FIRST, THEN advance
  // the cursor with markRead(). If markRead ran first it would bump the cursor
  // to now() and the banner count would always be 0. Do NOT call markRead from
  // the initial-load path before the banner is captured.
  await refreshUnreadBanner();
  await markRead();
  // BL-01: only NOW may the scroll/focus side-channels auto-mark-read — the
  // banner has been captured against the stale cursor, so advancing it is safe.
  if (switchGen === state.switchGen) state.catchup.readyForAutoMarkRead = true;
}

// ---------- Catch me up (Phase 23, CATCHUP-01) ----------
//
// mark-read on focus / scroll-to-bottom, a threshold-gated opt-in banner, and
// the ephemeral streamed summary. The server is the single source of truth for
// both the unread count AND the threshold (mirrors refreshAgentAliases); the
// client never auto-runs the (paid) summary — only the run-button click POSTs
// catch-me-up (T-23-03).

/**
 * Advance the caller's OWN read cursor for the active team (POST /mark-read).
 * Fail-soft (no UI on error) and de-duped: a call while one is already in flight
 * is a no-op so the scroll listener + focus handler can't spam the endpoint.
 * The endpoint takes no body — a caller can only ever move their own cursor.
 */
async function markRead() {
  if (!state.activeTeamId) return;
  if (state.catchup.markReadInFlight) return;
  state.catchup.markReadInFlight = true;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    if (!xbt_token) return;
    await fetch(`${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/mark-read`, {
      method: "POST",
      headers: { Authorization: `Bearer ${xbt_token}` },
    });
  } catch (e) {
    console.warn("[xbrain] mark-read failed:", e);
  } finally {
    state.catchup.markReadInFlight = false;
  }
}

/**
 * Poll GET /unread-summary for the active team and show the threshold-gated
 * banner. The server returns {count, since, threshold}; the banner shows ONLY
 * when count >= threshold (both from the response — server is authoritative,
 * never a client-side threshold). A dismissed `since` window stays hidden so we
 * don't re-nag (T-23-10). Fail-soft: on any error the previous UI is kept.
 */
async function refreshUnreadBanner() {
  if (!state.activeTeamId) return;
  const banner = $("catchup-banner");
  const textEl = $("catchup-banner-text");
  if (!banner || !textEl) return;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    if (!xbt_token) return;
    const data = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/unread-summary`,
      xbt_token,
    );
    const count = Number(data && data.count) || 0;
    const threshold = Number(data && data.threshold) || 0;
    const since = data && data.since != null ? data.since : null;
    state.catchup.since = since;

    // Below the server threshold → no affordance at all (never nag on a quiet
    // thread, and below-threshold means the run button is unreachable → no
    // accidental LLM spend, T-23-03).
    if (threshold <= 0 || count < threshold) {
      banner.hidden = true;
      return;
    }
    // Already dismissed for THIS exact window → stay hidden (don't re-nag).
    if (
      state.catchup.dismissedSince !== undefined &&
      state.catchup.dismissedSince === since
    ) {
      banner.hidden = true;
      return;
    }
    const noun = count === 1 ? "message" : "messages";
    textEl.textContent = `You have ${count} new ${noun} since your last visit.`;
    banner.hidden = false;
  } catch (e) {
    console.warn("[xbrain] unread-summary refresh failed:", e);
  }
}

/**
 * Wire the static catch-me-up controls once at boot: the run button (the ONLY
 * place catch-me-up is ever POSTed — no auto-run), the dismiss button (records
 * the dismissed window), the summary close button, and mark-read on window
 * focus when a team is active.
 */
function wireCatchup() {
  const runBtn = $("btn-catchup-run");
  if (runBtn) runBtn.addEventListener("click", runCatchMeUp);

  const dismissBtn = $("btn-catchup-dismiss");
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      state.catchup.dismissedSince = state.catchup.since;
      const banner = $("catchup-banner");
      if (banner) banner.hidden = true;
    });
  }

  const summaryCloseBtn = $("btn-catchup-summary-close");
  if (summaryCloseBtn) {
    summaryCloseBtn.addEventListener("click", () => {
      const panel = $("catchup-summary");
      if (panel) panel.hidden = true;
    });
  }

  // Mark-read when the popup/side-panel regains focus and a team is open.
  // BL-01: gated on readyForAutoMarkRead so it can't race ahead of the banner capture.
  window.addEventListener("focus", () => {
    if (state.catchup.readyForAutoMarkRead && state.activeTeamId) markRead();
  });
}

/**
 * Opt-in "Catch me up" — the ONLY caller of POST /catch-me-up (never auto-run,
 * T-23-03). On 202 the summary streams to the caller's own user channel, so we
 * hide the banner and reveal the ephemeral panel with a placeholder. On 200
 * (nothing_to_summarize) we hide quietly. On 429 / other we surface a short
 * English status in the banner and leave it visible.
 */
async function runCatchMeUp() {
  if (!state.activeTeamId) return;
  const runBtn = $("btn-catchup-run");
  const banner = $("catchup-banner");
  const bannerText = $("catchup-banner-text");
  if (runBtn) runBtn.disabled = true;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/catch-me-up`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${xbt_token}` },
      },
    );
    if (res.status === 202) {
      if (banner) banner.hidden = true;
      showCatchupSummary("Summarizing…");
      // WR-01: leave the run button DISABLED — the summary is still streaming.
      // It is re-enabled only when a matching catchup_stream_end/error frame
      // arrives (or is force-cleared on a team switch). Returning here skips the
      // finally re-enable below.
      return;
    } else if (res.status === 200) {
      // nothing_to_summarize — nothing changed since the cursor; hide quietly.
      if (banner) banner.hidden = true;
    } else if (res.status === 429) {
      if (bannerText) {
        bannerText.textContent = "Rate-limited — try again in a moment.";
      }
    } else {
      if (bannerText) {
        bannerText.textContent = `Could not summarize (HTTP ${res.status}).`;
      }
    }
    if (runBtn) runBtn.disabled = false;  // re-enable for the non-streaming outcomes
  } catch (e) {
    console.warn("[xbrain] catch-me-up failed:", e);
    if (bannerText) bannerText.textContent = "Network error — try again.";
    if (runBtn) runBtn.disabled = false;
  }
}

/**
 * Reveal the ephemeral summary panel with a placeholder while the stream fills
 * it. The streamed deltas replace/append the text via textContent in
 * handleUserPublication (XSS-safe). Never touches #message-list.
 */
function showCatchupSummary(placeholder) {
  const textEl = $("catchup-summary-text");
  const panel = $("catchup-summary");
  if (textEl) textEl.textContent = placeholder || "";
  if (panel) panel.hidden = false;
}

// Fetch the active team's EFFECTIVE agent-alias list and rebuild the client
// regex from it (JS-escape + longest-first via buildMentionRegex). The server
// is the single source of truth; on any error we keep the previous regex and
// never throw into the UI.
async function refreshAgentAliases() {
  if (!state.activeTeamId) return;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    if (!xbt_token) return;
    const data = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/agent-aliases`,
      xbt_token,
    );
    const aliases = Array.isArray(data && data.aliases) ? data.aliases : null;
    if (aliases && aliases.length) {
      state.agentAliases = aliases;
      state.mentionRe = buildMentionRegex(aliases);
    }
  } catch (e) {
    console.warn("[xbrain] agent-aliases refresh failed:", e);
  }
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
  syncDaySeparators();
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
    syncDaySeparators();
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
    syncDaySeparators();
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
    const textEl = streamTextTarget(data.message_id);
    if (textEl) {
      textEl.textContent = state.streamBuffer.get(data.message_id);
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
    const textEl = streamTextTarget(data.message_id);
    if (bodyEl) bodyEl.classList.remove("streaming");
    if (textEl) {
      textEl.textContent += `\n\n(error: ${data.error || "unknown"})`;
    }
    return;
  }
}

/**
 * Resolve where an agent stream should write its text.
 *
 * The bubble now also holds the agent label and the sources disclosure, so the
 * stream writes into the dedicated .xb-msg-text span instead of replacing the
 * whole bubble's textContent (which would wipe them). Falls back to the bubble
 * itself for any row built before the span existed.
 *
 * @param {string} messageId
 * @returns {HTMLElement|null}
 */
function streamTextTarget(messageId) {
  const bubble = document.querySelector(
    `[data-msg-id="${messageId}"] .xb-msg-bubble`,
  );
  if (!bubble) return null;
  return bubble.querySelector(".xb-msg-text") || bubble;
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
  syncDaySeparators();
}

function buildBubbleNode(msg) {
  // Row layout (mockup .row) — 2-col grid: avatar | (meta + bubble [+ savetag]).
  // .is-self mirrors the columns so the avatar/bubble sit on the right.
  const rowClass = bubbleClass(msg, state.me?.id); // is-self / is-user / is-agent
  const wrapper = document.createElement("div");
  wrapper.className = `xb-msg ${rowClass}`;
  wrapper.dataset.msgId = msg.id;
  // Day-separator input (syncDaySeparators reads this off the DOM).
  if (msg.created_at) wrapper.dataset.createdAt = msg.created_at;

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
  // Click a teammate's name to act on THEM: opens the people overlay with their row
  // highlighted, so sending a link or a file starts from the message you are reading
  // instead of reopening a list and finding them again. Skipped for the agent and for
  // your own messages — neither is someone you send things to.
  if (
    msg.kind !== "agent" &&
    msg.author_user_id &&
    msg.author_user_id !== state.me?.id
  ) {
    author.style.cursor = "pointer";
    author.title = "Send them a link or a file";
    author.addEventListener("click", () =>
      openPeople({ focusUserId: msg.author_user_id }),
    );
  }
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

  // Body — the bubble (own = --primary, others = --muted, agent = --card).
  const body = document.createElement("div");
  body.className = "xb-msg-bubble";

  // Agent block label (mockup .agent-bubble .who) — mono uppercase, styled by
  // CSS. Lives inside the bubble but OUTSIDE .xb-msg-text so the streaming
  // writer can replace the text without wiping the label.
  if (msg.kind === "agent") {
    const who = document.createElement("div");
    who.className = "xb-msg-agent-label";
    who.textContent = "agent · from your brain";
    body.appendChild(who);
  }

  // BL-003 Slice 4 — render media inline when the message carries a media
  // attachment. The URL is a server-minted signed path (/v1/media/{id}/img?t=...)
  // so no Bearer header is needed for the <img src>.
  if (msg.metadata && msg.metadata.media && msg.metadata.media.item_id) {
    renderMediaInto(body, msg.metadata.media);
    // Also show the caption/filename as small text below the attachment.
    if (msg.content) {
      const caption = document.createElement("div");
      caption.className = "xb-msg-caption";
      caption.textContent = msg.content;
      body.appendChild(caption);
    }
  } else {
    // Text lives in its own span — the target the agent stream writes into.
    const text = document.createElement("span");
    text.className = "xb-msg-text";
    text.textContent = msg.content || "";
    body.appendChild(text);
  }

  // Sources disclosure — driven by the REAL memory_items count the agent
  // pipeline persisted. Renders nothing when the agent used no brain items.
  if (msg.kind === "agent") {
    const summaryLabel = brainSummaryLabel(msg.metadata);
    if (summaryLabel) {
      body.appendChild(buildSourcesNode(summaryLabel, msg.metadata.sources));
    }
  }

  wrapper.appendChild(body);

  // Saved-to-brain badge — only on a genuine indexed-attachment signal.
  const savedLabel = savedToBrainLabel(msg);
  if (savedLabel) {
    const tag = document.createElement("span");
    tag.className = "xb-msg-savetag";
    tag.textContent = savedLabel;
    wrapper.appendChild(tag);
  }

  return wrapper;
}

/**
 * Build the agent's `<details>` sources disclosure.
 *
 * The summary line is the real memory_items count. Individual source rows are
 * rendered ONLY when the server actually sends `metadata.sources` — the backend
 * does not emit it today, so this stays future-proof and fabricates nothing
 * (threat T-20-03-02). Truth levels come from the payload; the chip styling is
 * selected by data-level, never hardcoded.
 *
 * @param {string} summaryLabel - e.g. "2 sources from the brain"
 * @param {Array<object>|undefined} sources - server-sent rows, if any
 * @returns {HTMLDetailsElement}
 */
function buildSourcesNode(summaryLabel, sources) {
  const details = document.createElement("details");
  details.className = "xb-msg-sources";

  const summary = document.createElement("summary");
  summary.textContent = summaryLabel;
  details.appendChild(summary);

  if (Array.isArray(sources)) {
    for (const s of sources) {
      const row = document.createElement("div");
      row.className = "xb-msg-src";

      const level =
        s && typeof s.truth_level === "string" ? s.truth_level.toLowerCase() : null;
      if (level) {
        const chip = document.createElement("span");
        chip.className = "xb-msg-chip";
        chip.dataset.level = level;
        chip.textContent = level;
        row.appendChild(chip);
      }

      const label = document.createElement("span");
      label.textContent = (s && (s.text || s.title)) || "";
      row.appendChild(label);

      details.appendChild(row);
    }
  }

  return details;
}

/**
 * Reconcile day separators across the whole thread.
 *
 * Recomputed from the rows currently in the DOM (rather than tracked
 * incrementally) so it stays correct for the append path, the prepend
 * pagination path, and live inserts alike. Separators carry no data-msg-id, so
 * the de-dupe lookup in renderMessage never sees them.
 */
function syncDaySeparators() {
  const list = $("message-list");
  for (const sep of Array.from(list.querySelectorAll(".xb-msg-daysep"))) {
    sep.remove();
  }
  let prevIso = null;
  for (const row of Array.from(list.children)) {
    const iso = row.dataset ? row.dataset.createdAt : null;
    if (!iso) continue;
    if (!prevIso || !sameDay(prevIso, iso)) {
      const sep = document.createElement("div");
      sep.className = "xb-msg-daysep";
      sep.textContent = dayLabel(iso);
      list.insertBefore(sep, row);
    }
    prevIso = iso;
  }
}

/**
 * Render a media attachment (image or document chip) into an existing element.
 * All DOM construction uses createElement/textContent — no innerHTML with
 * interpolated user data — to stay XSS-safe.
 *
 * @param {HTMLElement} el   - Target container element (the bubble body div).
 * @param {object}      media - {item_id, mime, size, filename, url?}
 */
function renderMediaInto(el, media) {
  // Build the absolute URL from the server-minted relative path.
  const src = media.url ? `${MEMORY_API_BASE}${media.url}` : null;

  if (media.mime && media.mime.startsWith("image/") && src) {
    // Image: render a thumbnail that opens the full image in a new tab.
    const img = document.createElement("img");
    img.className = "xb-msg-thumb";
    img.alt = media.filename || "image";
    img.src = src;
    img.addEventListener("click", () => {
      window.open(src, "_blank", "noopener");
    });
    el.appendChild(img);
  } else {
    // Document: render a file chip with filename + size.
    const chip = document.createElement("a");
    chip.className = "xb-msg-file-chip";
    chip.href = src || "#";
    chip.target = "_blank";
    chip.rel = "noopener";

    const icon = document.createTextNode("📄 "); // 📄
    chip.appendChild(icon);

    const nameNode = document.createTextNode(media.filename || "file");
    chip.appendChild(nameNode);

    if (media.size) {
      const sizeNode = document.createTextNode(` (${formatBytes(media.size)})`);
      chip.appendChild(sizeNode);
    }
    el.appendChild(chip);
  }
}

/**
 * Format a byte count into a compact human-readable string (KB / MB).
 * @param {number} n - byte count
 * @returns {string}
 */
function formatBytes(n) {
  if (n == null || n < 0) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
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
  const mentionHint = createMentionHint();
  input.addEventListener("input", () => {
    autoResize(input);
    updateMentionHint(input, mentionHint);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    // SHIFT+ENTER → newline (textarea default, don't intercept)
    if (e.shiftKey) return;
    // Plain ENTER or CTRL/META+ENTER → send
    e.preventDefault();
    sendMessage();
  });
  $("btn-send").addEventListener("click", sendMessage);
  // 📎 now triggers file attach; clip overlay moved to header "add to memory".
  $("btn-clip").addEventListener("click", openFilePicker);
  $("file-picker").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    // Reset immediately so re-selecting the same file triggers change again.
    e.target.value = "";
    if (file) await uploadFile(file);
  });

  // Scroll-up history paging + mark-read at the bottom.
  $("chat-scroll").addEventListener("scroll", () => {
    const el = $("chat-scroll");
    if (el.scrollTop < 80) loadOlderPage();
    // Mark-read once the user reaches the bottom (reuse the ~120px near-bottom
    // test from scrollToBottom). markRead() is fail-soft and de-dupes in-flight
    // calls, so this can fire on every scroll frame without spamming the server.
    if (
      state.catchup.readyForAutoMarkRead &&
      state.activeTeamId &&
      el.scrollHeight - el.scrollTop - el.clientHeight < 120
    ) {
      markRead();
    }
  });
}

function autoResize(el) {
  // Let CSS min-height set the ONE-row floor; we just grow up to max-height.
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

// Optimistic composer hint: a lightweight text line under the composer that
// shows "Will summon @<alias>" while the draft contains a live mention. Built
// dynamically (no popup.html/css contract touched) and styled inline with the
// existing shadcn tokens. UX-only — the server makes the real summon decision.
function createMentionHint() {
  const composer = document.querySelector(".xb-composer");
  if (!composer) return null;
  const hint = document.createElement("div");
  hint.className = "xb-mention-hint";
  hint.hidden = true;
  hint.setAttribute("aria-live", "polite");
  hint.style.cssText =
    "padding:2px 12px 0;font-size:11px;line-height:1.4;color:var(--muted-fg);";
  composer.appendChild(hint);
  return hint;
}

function updateMentionHint(input, hint) {
  if (!hint) return;
  const hit = detectMentionClient(input.value, state.mentionRe);
  if (hit) {
    hint.textContent = `Will summon @${hit.trigger}`;
    hint.hidden = false;
  } else {
    hint.textContent = "";
    hint.hidden = true;
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
    const sent = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/messages`,
      xbt_token,
      "POST",
      { content },
    );
    input.value = "";
    autoResize(input);
    // Optimistic render: show the message immediately from the POST response
    // instead of waiting for the Centrifugo echo (which can lag or be missed
    // while the popup is backgrounded). renderMessage() de-dupes by id, so the
    // later "message" publication for the same id is a no-op.
    if (sent && sent.id) {
      renderMessage(sent, { prepend: false });
      scrollToBottom();
      $("chat-empty").hidden = true;
    }
  } catch (e) {
    console.warn("[xbrain] send failed:", e);
    alert(`Send failed: ${e.message}`);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

// ---------- File attach (BL-003 Slice 3) ----------

function openFilePicker() {
  $("file-picker").click();
}

/**
 * Upload a file to /v1/media/upload and post a media message into team chat.
 * Uses raw fetch (not fetchJson) because multipart requires the browser to set
 * the Content-Type boundary automatically — never set it manually.
 */
async function uploadFile(file) {
  const MAX_BYTES = 25 * 1024 * 1024; // 25 MB — must match backend constant
  if (file.size > MAX_BYTES) {
    console.warn("[xbrain] file too large:", file.size);
    // Surface inline error in the composer area without an alert.
    const statusEl = document.querySelector(".xb-composer");
    if (statusEl) {
      const err = document.createElement("div");
      err.className = "xb-upload-error";
      err.textContent = `File too large (max 25 MB): ${file.name}`;
      statusEl.prepend(err);
      setTimeout(() => err.remove(), 4000);
    }
    return;
  }

  const team = state.teams.find((t) => t.id === state.activeTeamId);
  if (!team) {
    console.warn("[xbrain] uploadFile: no active team");
    return;
  }

  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (!xbt_token) {
    console.warn("[xbrain] uploadFile: no xbt_token");
    return;
  }

  const clipBtn = $("btn-clip");
  if (clipBtn) clipBtn.disabled = true;

  try {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("source_surface", "extension");
    fd.append("truth_level", "WORKING");

    const resp = await fetch(`${MEMORY_API_BASE}/v1/media/upload`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${xbt_token}`,
        "X-Team-Scope": team.slug,
        // NOTE: Do NOT set Content-Type — let the browser set it with the
        //       multipart boundary automatically.
      },
      body: fd,
    });

    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
    }

    const item = await resp.json();
    await postMediaMessage(item, file.name);
  } catch (e) {
    console.warn("[xbrain] uploadFile failed:", e);
  } finally {
    if (clipBtn) clipBtn.disabled = false;
  }
}

/**
 * Post a team chat message that carries structured media metadata.
 * Follows the same optimistic-render + dedup pattern as sendMessage().
 */
async function postMediaMessage(item, filename) {
  if (!state.activeTeamId) return;
  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  try {
    const sent = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/messages`,
      xbt_token,
      "POST",
      {
        content: filename,
        media: {
          item_id: item.item_id,
          mime: item.mime,
          size: item.size,
          filename,
        },
      },
    );
    // Optimistic render: show immediately from the POST response;
    // the Centrifugo echo for the same id will be de-duped by renderMessage().
    if (sent && sent.id) {
      renderMessage(sent, { prepend: false });
      scrollToBottom();
      $("chat-empty").hidden = true;
    }
  } catch (e) {
    console.warn("[xbrain] postMediaMessage failed:", e);
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

  // Ask the content script for selection + url + title. content.js runs on
  // <all_urls>, so window.location.href / document.title are always readable
  // from the page context — unlike chrome.tabs.query, whose tab.url/tab.title
  // are stripped to empty on hosts not in host_permissions when only the
  // activeTab grant applies (e.g. the side panel on a third-party page).
  // We therefore prefer the content-script values and fall back to tab.*.
  let selectedText = "";
  let csUrl = "";
  let csTitle = "";
  if (tab && tab.id && !(tab.url || "").startsWith("chrome://")) {
    try {
      const sel = await new Promise((resolve) => {
        chrome.tabs.sendMessage(tab.id, { type: "GET_SELECTION" }, (resp) => {
          if (chrome.runtime.lastError) resolve(null);
          else resolve(resp);
        });
      });
      selectedText = (sel && sel.selectedText && sel.selectedText.trim()) || "";
      csUrl = (sel && sel.url) || "";
      csTitle = (sel && sel.title) || "";
    } catch {
      /* ignore */
    }
  }

  // Stash on the overlay so submitClip doesn't need to re-query the tab.
  // Content-script values win; tab.* is the fallback (it may be empty without
  // host permission, which is exactly the "Nothing to send" bug we fix here).
  const resolvedUrl = csUrl || (tab && tab.url) || "";
  const resolvedTitle = csTitle || (tab && tab.title) || "";
  const overlayEl = $("clip-overlay");
  overlayEl.dataset.clipMode = selectedText ? "selection" : "page";
  overlayEl.dataset.clipUrl = resolvedUrl;
  overlayEl.dataset.clipTitle = resolvedTitle;
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
    detailEl.textContent = resolvedUrl
      ? `${resolvedTitle || "(no title)"} — ${hostnameFromUrl(resolvedUrl)}`
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

    // Auth with the xbt_ personal token (works for GitHub OR Google sign-in —
    // same universal credential the chat uses), not the legacy Google-only flow.
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    if (!xbt_token) {
      setClipStatus("Sign-in required", "error");
      sendBtn.disabled = false;
      return;
    }

    await chrome.runtime.sendMessage({
      type: "SEND_TO_BRAIN",
      token: xbt_token,
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

/**
 * Empty state — the product is a TEAM chat, so this proposes creating a team and
 * then immediately inviting people into it. It deliberately no longer offers a
 * "solo workspace": a chat with nobody in it is not the thing being sold, and the
 * old copy also pointed at a "link GitHub above" button that is now hidden.
 */
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
    "Start your team chat. Create a team, then invite your teammates — everyone shares one memory, and @agent answers from it.";
  wrapper.appendChild(msg);

  const input = document.createElement("input");
  input.type = "text";
  input.id = "new-team-name";
  input.placeholder = "Team name";
  input.autocomplete = "off";
  input.maxLength = 64;
  input.style.cssText =
    "width:100%;max-width:260px;padding:8px 10px;background:var(--xb-bg);color:var(--xb-text);" +
    "border:1px solid var(--xb-border);font-family:inherit;font-size:inherit;outline:none;";
  wrapper.appendChild(input);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "connect-btn";
  btn.style.maxWidth = "260px";
  btn.textContent = "Create team";
  btn.addEventListener("click", () => createTeam(btn, input));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") createTeam(btn, input);
  });
  wrapper.appendChild(btn);

  // Second first-class path: someone who was INVITED must be able to act right
  // here. Burying it in a hint that points at another overlay makes the invited
  // half of the audience hunt for the one thing they came to do.
  const divider = document.createElement("p");
  divider.style.cssText =
    "margin:4px 0 0;font-size:11px;color:var(--xb-text-dim);letter-spacing:0.04em;";
  divider.textContent = "— or join a team you were invited to —";
  wrapper.appendChild(divider);

  const codeInput = document.createElement("input");
  codeInput.type = "text";
  codeInput.id = "empty-join-code";
  codeInput.placeholder = "Invite code (xbi_…)";
  codeInput.autocomplete = "off";
  codeInput.spellcheck = false;
  codeInput.style.cssText = input.style.cssText;
  wrapper.appendChild(codeInput);

  const joinBtn = document.createElement("button");
  joinBtn.type = "button";
  joinBtn.className = "connect-btn";
  joinBtn.style.maxWidth = "260px";
  joinBtn.textContent = "Join team";
  joinBtn.addEventListener("click", () => joinFromEmptyState(joinBtn, codeInput));
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") joinFromEmptyState(joinBtn, codeInput);
  });
  wrapper.appendChild(joinBtn);

  const hint = document.createElement("p");
  hint.style.cssText =
    "margin:0;font-size:11px;color:var(--xb-text-dim);line-height:1.45;";
  hint.textContent =
    "If you were sent an invite link, opening it joins you automatically.";
  wrapper.appendChild(hint);

  emptyEl.appendChild(wrapper);
  emptyEl.style.pointerEvents = "auto";
  input.focus();
}

/**
 * Redeem an invite code straight from the empty state. Same endpoint the invite
 * overlay uses; kept separate so it can report inline instead of into the overlay
 * status line that is not on screen here.
 */
async function joinFromEmptyState(btn, codeInput) {
  const code = codeInput ? codeInput.value.trim() : "";
  if (!code) {
    codeInput.focus();
    return;
  }
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Joining…";
  // The status line is created here and held in a closure rather than looked up by
  // id: it belongs to this dynamically-built empty state, so it has no place in the
  // static popup.html — and the popup contract test rightly refuses ids that are
  // referenced but never declared there.
  let statusLine = null;
  const say = (text, isError) => {
    if (!statusLine) {
      statusLine = document.createElement("p");
      statusLine.style.cssText = "margin:0;font-size:11px;line-height:1.45;";
      btn.parentElement.appendChild(statusLine);
    }
    statusLine.style.color = isError
      ? "var(--xb-danger,#f85149)"
      : "var(--xb-text-mute)";
    statusLine.textContent = text;
  };
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const res = await fetch(`${MEMORY_API_BASE}/v1/teams/join-by-code`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${xbt_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ code }),
    });
    if (res.status === 200) {
      const j = await res.json();
      btn.textContent = "Joined ✓";
      say(`You are in ${j.display_name || j.slug || "the team"}.`, false);
      chrome.runtime.sendMessage({ type: "REFRESH_TEAMS_MENU" }).catch(() => {});
      await boot();
      return;
    }
    // 404 is the server's deliberate no-oracle answer for unknown/revoked/expired/
    // used-up codes — do not guess which one it was.
    say(
      res.status === 404
        ? "That code is not valid — ask for a new invite."
        : res.status === 429
          ? "Too many attempts — wait a moment."
          : `Could not join (HTTP ${res.status}).`,
      true,
    );
  } catch (e) {
    console.warn("[xbrain] join from empty state failed");
    say("Network error — try again.", true);
  } finally {
    if (btn.textContent === "Joining…") btn.textContent = original;
    btn.disabled = false;
  }
}

/**
 * Turn a typed team name into a slug the API accepts (`^[a-z][a-z0-9-]*$`, <=64).
 * Falls back to a generic base when the name has no usable ASCII letters, so a
 * name written entirely in another script still produces a valid slug.
 */
function slugifyTeamName(name) {
  let s = (name || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")   // strip accents
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  if (!/^[a-z]/.test(s)) s = "team" + (s ? "-" + s : "");
  return s.slice(0, 64).replace(/-+$/, "");
}

/**
 * Create the team, then go STRAIGHT to inviting — that is the first thing a new
 * team needs, so the invite overlay opens with a link already minted rather than
 * leaving the user in an empty room to figure out the next step.
 */
async function createTeam(btn, input) {
  const displayName = (input && input.value.trim()) || "My team";
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Creating…";
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    const base = slugifyTeamName(displayName);
    let res;
    // One retry on a slug collision (409) with a short suffix — two people naming
    // their team the same thing must not dead-end on an error they cannot act on.
    for (const slug of [base, `${base}-${Math.random().toString(36).slice(2, 6)}`]) {
      res = await fetch(`${MEMORY_API_BASE}/v1/teams/self`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${xbt_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ slug, display_name: displayName }),
      });
      if (res.status !== 409) break;
    }
    if (!res || res.status !== 201) {
      const code = res ? res.status : "network";
      throw new Error(`HTTP ${code}`);
    }

    // Rebuild the right-click submenu so the new team shows up there too.
    chrome.runtime.sendMessage({ type: "REFRESH_TEAMS_MENU" }).catch(() => {});

    btn.textContent = "Ready ✓";
    await boot();                       // team selector + chat now have the team
    if (state.activeTeamId) {
      openInvite();                     // first stop: bring people in
      await mintInvite();               // link ready to copy, no extra click
    }
  } catch (e) {
    console.warn("[xbrain] create team failed:", e && e.message ? e.message : e);
    btn.disabled = false;
    btn.textContent = original;
    const hint = document.createElement("p");
    hint.style.cssText = "margin:0;font-size:11px;color:var(--xb-danger,#f85149);";
    hint.textContent = "Could not create the team — check your connection and try again.";
    btn.parentElement.appendChild(hint);
  }
}

// createSoloTeam() lived here and posted to /v1/teams/self-solo. Removed with the
// solo-workspace empty state: the product is a team chat, so the first action is
// creating a TEAM and inviting people into it (see createTeam above). The endpoint
// still exists server-side for the first-login path; nothing in the popup calls it.

// ---------- React to storage changes (token mint, GitHub link) ----------

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.xbt_token) {
    // Token appeared or was cleared — rerun boot (which re-fetches teams,
    // reconnects, and via switchTeam refreshes the team's agent-alias list).
    boot();
    // Auth/team context may have shifted — also refresh the alias list for the
    // currently active team so the composer hint never lags behind a re-auth.
    if (state.activeTeamId) refreshAgentAliases();
  }
});

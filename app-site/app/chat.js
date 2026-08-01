/**
 * The PWA's team-chat surface (Phase 27, Plan 27-06).
 *
 * This file is a SURFACE, not a chat. It owns state, DOM wiring and the calls
 * into `chat_core/` — nothing else. Every behaviour that is not specific to
 * being a web page (building a bubble, routing a websocket frame, opening the
 * socket, talking to memory-api) belongs in `packages/chat-core/` and arrives
 * here through the generated `chat_core/` copy. D-27-04: a second copy of the
 * chat would drift from the extension's within a week, so a helper that feels
 * portable while being written goes upstream and gets re-synced instead of
 * being defined below. `chrome-extension/tests/test_pwa_chat.mjs` enforces that
 * mechanically — it fails if this file declares any name chat-core declares.
 *
 * Two things this file must never contain, both grep-asserted:
 *   - an origin literal. `MEMORY_API_BASE` comes from auth.js, which is the one
 *     module that decides where the API lives.
 *   - a socket URL. The server hands it back on POST /v1/me/centrifugo-token
 *     and chat-core's realtime module uses what it is given (D-27-03).
 *
 * OUT OF SCOPE, deliberately (27-CONTEXT defers them): the board, the invite
 * overlay, the people overlay, the personal-summary panel, the clipper and file
 * upload. The team switcher, history, the composer and realtime are the slice.
 */

import { createApi } from "./chat_core/api.js";
import { createRenderer } from "./chat_core/render.js";
import { createPublicationRouter } from "./chat_core/publication.js";
import { connectRealtime } from "./chat_core/realtime.js";
import { StreamBuffer } from "./chat_core/chat_stream.js";
import { handleOpenUrl, isSafeHttpUrl } from "./chat_core/nudge_open.js";
import { webPlatform } from "./platform_web.js";
import { MEMORY_API_BASE, getToken, signOut } from "./auth.js";

const el = (id) => document.getElementById(id);

/**
 * The team a person last read, remembered per device.
 *
 * A phone user opens this app from a home-screen icon many times a day; making
 * them re-pick a team every launch would be the single most annoying thing
 * about it. Only an id they are already a member of is ever stored, and it is
 * re-validated against /v1/teams/my-teams on the next boot.
 */
const TEAM_STORAGE_KEY = "xbrain_pwa_team";

const state = {
  me: null,
  teams: [],
  activeTeamId: null,
  realtime: null,
  streamBuffer: new StreamBuffer(),
  nameCache: {},
  oldestLoadedTs: null,
  historyPaging: false,
  initialLoaded: false,
};

const api = createApi({ baseUrl: MEMORY_API_BASE, getToken });

const renderer = createRenderer({
  doc: document,
  listEl: el("message-list"),
  scrollEl: el("chat-scroll"),
  apiBase: MEMORY_API_BASE,
  getSelfUserId: () => state.me && state.me.id,
  getNameCache: () => state.nameCache,
  onAuthorClick: null, // the people overlay is not part of the PWA slice
});

/**
 * A live view onto state.streamBuffer.
 *
 * switchTeam replaces the buffer instance, and anything built once at boot that
 * captured the old instance would keep appending an agent's answer to the
 * PREVIOUS team's buffer — the answer would then render as empty text, silently,
 * from the first team switch onwards. Reading it late is what stops that.
 */
const streamBufferFacade = {
  start: (id) => state.streamBuffer.start(id),
  append: (id, delta) => state.streamBuffer.append(id, delta),
  get: (id) => state.streamBuffer.get(id),
  finalize: (id, text) => state.streamBuffer.finalize(id, text),
};

/**
 * Every frame on the active team's channel — messages and agent streams alike —
 * goes through chat-core's router, which is the same code path the extension
 * runs. The surface only supplies the renderer, the buffer and the one fact the
 * router cannot know: which element says "no messages yet".
 *
 * The channel's NAME is deliberately not quoted anywhere in this file. Building
 * one here would be the first step towards this surface deciding what it may
 * subscribe to; chat-core's realtime module owns that, and the contract test
 * reads this file for a quoted channel token like any other source text.
 */
const handleTeamPublication = createPublicationRouter({
  renderer,
  streamBuffer: streamBufferFacade,
  onNonEmpty: () => {
    const empty = el("chat-empty");
    if (empty) empty.hidden = true;
  },
});

// ---------- Small surface helpers ----------

/**
 * A dead stored token answers 401/403 on the first call. That is a signed-out
 * person, not an outage, and it must end at the sign-in card rather than in a
 * retry loop against a credential that will never work.
 */
function isAuthError(e) {
  return /^HTTP\s+40[13]\b/.test(String((e && e.message) || ""));
}

/** Connection state line. `null` clears it. */
function setConnectionBanner(message) {
  const banner = el("connection-banner");
  if (!banner) return;
  banner.textContent = message || "";
  banner.hidden = !message;
}

/**
 * The composer's inline error line, created on demand if the markup predates it.
 *
 * A failed send reports HERE and not in a modal dialog: a blocking dialog inside
 * a standalone PWA on a phone has no window chrome to dismiss it against, and it
 * would also throw away the text the person just typed by stealing focus.
 */
function composerErrorEl() {
  let node = el("composer-error");
  if (node) return node;
  const composer = el("composer");
  if (!composer || !composer.parentNode) return null;
  node = document.createElement("p");
  node.id = "composer-error";
  node.className = "xb-composer-error";
  node.hidden = true;
  composer.parentNode.insertBefore(node, composer);
  return node;
}

function showComposerError(message) {
  const node = composerErrorEl();
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
}

function clearComposerError() {
  const node = el("composer-error");
  if (node) node.hidden = true;
}

function focusComposer() {
  const input = el("composer-input");
  if (!input) return;
  // After layout settles, so the caret does not land mid-reflow.
  window.requestAnimationFrame(() => input.focus());
}

function renderEmptyTeams() {
  const empty = el("chat-empty");
  if (empty) {
    empty.textContent =
      "You are not a member of any team yet. Ask a teammate to invite you.";
    empty.hidden = false;
  }
  const selector = el("team-selector");
  if (selector) selector.hidden = true;
  const composer = el("composer");
  if (composer) composer.hidden = true;
}

// ---------- Teams ----------

let selectorWired = false;

/** Fill the header's team picker from state.teams. */
function renderTeamSelector() {
  const selector = el("team-selector");
  if (!selector) return;
  while (selector.firstChild) selector.removeChild(selector.firstChild);
  for (const team of state.teams) {
    const option = document.createElement("option");
    option.value = team.id;
    // textContent, never markup: a team name is a string somebody typed.
    option.textContent = team.display_name || team.slug || "team";
    selector.appendChild(option);
  }
  selector.value = state.activeTeamId || state.teams[0].id;
  selector.hidden = false;
  if (!selectorWired) {
    selector.addEventListener("change", (event) => {
      switchTeam(event.target.value);
    });
    selectorWired = true;
  }
}

/**
 * The remembered team, but only if the person is STILL a member of it.
 * Membership can be revoked between two launches; a stale id would otherwise
 * produce a 403 on every history load with no way back to a working team.
 */
async function preferredTeamId() {
  try {
    const stored = await webPlatform.storage.get([TEAM_STORAGE_KEY]);
    const remembered = stored[TEAM_STORAGE_KEY];
    if (remembered && state.teams.some((t) => String(t.id) === String(remembered))) {
      return remembered;
    }
  } catch (e) {
    // A storage read that throws is a preference we do not have, nothing more.
  }
  return null;
}

/**
 * Point the whole surface at another team.
 *
 * Teardown happens BEFORE the list is cleared so a frame from the team being
 * left cannot land in a list that is about to be emptied.
 */
async function switchTeam(teamId) {
  if (state.realtime) state.realtime.unsubscribeTeam();

  state.activeTeamId = teamId;
  state.streamBuffer = new StreamBuffer();
  state.oldestLoadedTs = null;
  state.initialLoaded = false;
  renderer.clear();
  clearComposerError();
  const empty = el("chat-empty");
  if (empty) empty.hidden = true;

  // A null realtime handle means the vendored client did not load: history still
  // renders and sending still works, only live updates are missing.
  if (state.realtime) state.realtime.subscribeTeam(teamId);

  await webPlatform.storage.set({ [TEAM_STORAGE_KEY]: teamId });
  await refreshNameCache();
  await loadInitialHistory();
}

/**
 * Author id -> display name, so other people's messages carry their name.
 *
 * Fail-soft on purpose: chat-core already falls back to the "Teammate" label, so
 * a member list that cannot be read costs a name, never a message.
 */
async function refreshNameCache() {
  if (!state.activeTeamId) return;
  try {
    const members = await api.request(`/v1/teams/${state.activeTeamId}/members`);
    if (!Array.isArray(members)) return;
    const cache = {};
    for (const member of members) {
      const id = member.user_id || member.id;
      const name = member.display_name || member.email;
      if (id && name) cache[String(id)] = name;
    }
    state.nameCache = cache;
  } catch (e) {
    console.warn("[xbrain] member list unavailable:", e);
  }
}

// ---------- History ----------

async function loadInitialHistory() {
  if (state.initialLoaded) return;
  state.initialLoaded = true;
  const empty = el("chat-empty");
  let data;
  try {
    data = await api.listMessages(state.activeTeamId);
  } catch (e) {
    console.warn("[xbrain] history load failed:", e);
    if (empty) {
      empty.textContent = "Could not load this team's messages. Reload to try again.";
      empty.hidden = false;
    }
    return;
  }
  // The endpoint answers newest-first; the thread reads oldest-first.
  const messages = ((data && data.messages) || []).slice().reverse();
  if (messages.length === 0) {
    if (empty) {
      empty.textContent = "No messages in this team yet. Say something.";
      empty.hidden = false;
    }
    return;
  }
  if (empty) empty.hidden = true;
  for (const message of messages) renderer.renderMessage(message, { prepend: false });
  renderer.syncDaySeparators();
  state.oldestLoadedTs = messages[0].created_at;
  // First paint pins to the newest message whatever the scroll position was.
  renderer.scrollToBottom({ force: true });
}

/**
 * One page of older messages, prepended.
 *
 * Prepending grows the document upwards, which would otherwise yank the reader's
 * viewport to a different message. The scrollTop arithmetic below re-anchors on
 * the height the list gained, so the message they were reading stays under their
 * eyes.
 */
async function loadOlderPage() {
  if (state.historyPaging || !state.oldestLoadedTs) return;
  state.historyPaging = true;
  const loader = el("history-loader");
  if (loader) loader.hidden = false;
  const scrollEl = el("chat-scroll");
  try {
    const data = await api.listMessages(state.activeTeamId, {
      before: state.oldestLoadedTs,
    });
    const older = ((data && data.messages) || []).slice().reverse();
    const previousHeight = scrollEl ? scrollEl.scrollHeight : 0;
    for (const message of older) renderer.renderMessage(message, { prepend: true });
    renderer.syncDaySeparators();
    if (older.length > 0) state.oldestLoadedTs = older[0].created_at;
    if (scrollEl) {
      scrollEl.scrollTop = scrollEl.scrollHeight - previousHeight + scrollEl.scrollTop;
    }
  } catch (e) {
    console.warn("[xbrain] older page failed:", e);
  } finally {
    state.historyPaging = false;
    if (loader) loader.hidden = true;
  }
}

// ---------- Composer ----------

function wireComposer() {
  const input = el("composer-input");
  const sendBtn = el("btn-send");
  const scrollEl = el("chat-scroll");

  if (input) {
    input.addEventListener("input", () => autoResize(input));
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      // SHIFT+ENTER is a newline — the textarea's own behaviour, left alone.
      if (event.shiftKey) return;
      event.preventDefault();
      sendMessage();
    });
  }
  if (sendBtn) sendBtn.addEventListener("click", () => sendMessage());

  if (scrollEl) {
    scrollEl.addEventListener("scroll", () => {
      if (scrollEl.scrollTop < 80) loadOlderPage();
    });
  }
}

/** Grow the textarea with its content, up to the ceiling CSS declares. */
function autoResize(input) {
  input.style.height = "auto";
  const view = document.defaultView;
  const maxHeight =
    (view && parseFloat(view.getComputedStyle(input).maxHeight)) || 120;
  input.style.height = `${Math.min(input.scrollHeight, maxHeight)}px`;
}

async function sendMessage() {
  const input = el("composer-input");
  const sendBtn = el("btn-send");
  if (!input || !state.activeTeamId) return;
  const content = input.value.trim();
  if (!content) return;

  if (sendBtn) sendBtn.disabled = true;
  try {
    const sent = await api.postMessage(state.activeTeamId, { content });
    // Cleared only AFTER the server took it: clearing first would throw away
    // what they wrote whenever the send fails.
    input.value = "";
    autoResize(input);
    clearComposerError();
    // Optimistic render straight from the POST response instead of waiting for
    // the websocket echo, which can lag or be missed entirely while the surface
    // is backgrounded. renderMessage de-dupes by id, so the echo is a no-op.
    if (sent && sent.id) {
      renderer.renderMessage(sent, { prepend: false });
      renderer.syncDaySeparators();
      renderer.scrollToBottom();
      const empty = el("chat-empty");
      if (empty) empty.hidden = true;
    }
  } catch (e) {
    showComposerError(`Message not sent: ${e.message}`);
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    input.focus();
  }
}

// ---------- Personal channel: a teammate pushing a link (Phase 22) ----------

/**
 * In-page fallback for a nudge, used when no OS notification could be raised.
 *
 * `webPlatform.notify` returns null unless notification access was already
 * granted, and this surface never asks for it on its own (D-27-05 puts that
 * prompt behind one explicit click, owned by plan 27-07). Without this banner a
 * nudge would simply vanish for everyone who has not opted in.
 *
 * It shows the sender and the FULL, unshortened destination (T-22-10): a
 * truncated or prettified link is exactly how somebody gets talked into opening
 * something they would have refused had they read it.
 */
function showNudgeBanner(sender, url) {
  const banner = el("nudge-banner");
  if (!banner) return;
  while (banner.firstChild) banner.removeChild(banner.firstChild);

  const line = document.createElement("span");
  line.className = "xb-nudge-text";
  // textContent, never markup — this string came from another user.
  line.textContent = `${sender} wants to open: ${url}`;
  banner.appendChild(line);

  const open = document.createElement("button");
  open.type = "button";
  open.className = "xb-btn";
  open.textContent = "Open";
  open.addEventListener("click", () => {
    banner.hidden = true;
    // Re-validated AT THE POINT OF ACTION, not merely on arrival. A URL is never
    // trusted because it passed a check upstream; the platform shim checks it a
    // third time before the browser ever sees it.
    if (!isSafeHttpUrl(url)) return;
    webPlatform.openUrl(url);
  });
  banner.appendChild(open);

  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "xb-btn";
  dismiss.textContent = "Dismiss";
  dismiss.addEventListener("click", () => {
    banner.hidden = true;
  });
  banner.appendChild(dismiss);

  banner.hidden = false;
}

/**
 * Frames on the caller's own `user:<sub>` channel.
 *
 * This surface handles exactly ONE type — the Phase-22 link nudge — and ignores
 * every other frame. The personal-summary frames the extension renders have no
 * panel here (27-CONTEXT defers them out of the PWA), and an unknown frame from
 * a newer server must never throw in an older client.
 *
 * No opener is handed to handleOpenUrl, so this surface has no capability to
 * move the browser on its own: a teammate's link can only be reached through a
 * click, which is the whole of D-22-02.
 */
async function handleUserPublication(data) {
  if (!data || data.type !== "open_url") return;
  try {
    const notified = await handleOpenUrl(data, {
      getSettings: async () => ({
        allowOpenLinkRequests: true,
        autoOpenLinkRequests: false,
      }),
      notify: (opts) =>
        webPlatform.notify({ title: opts.title, message: opts.message }),
    });
    // A null result means no notification was shown. That is either "access not
    // granted" (fall back to the banner) or "the URL failed validation" — and
    // the second must NOT produce a banner offering to open it, so the check is
    // repeated here rather than inferred from the null.
    if (!notified && isSafeHttpUrl(data.url)) {
      const from = data.from || {};
      showNudgeBanner(from.display_name || from.sub || "A teammate", data.url);
    }
  } catch (e) {
    console.warn("[xbrain] link nudge handling failed:", e);
  }
}

// ---------- Boot ----------

/**
 * Bring the chat up for a signed-in person.
 *
 * @param {{onSignedOut?: () => void}} [refs]
 *   onSignedOut — shown when the stored token turns out to be dead. The sign-in
 *   card belongs to app.js, so this surface reports the fact instead of owning
 *   the markup for it.
 */
export async function bootChat(refs = {}) {
  const { onSignedOut } = refs;

  // 1. A token, or there is nothing to boot.
  if (!(await getToken())) {
    if (onSignedOut) onSignedOut();
    return;
  }

  // 2. Identity + teams.
  try {
    state.me = await api.me();
    state.teams = await api.myTeams();
  } catch (e) {
    if (isAuthError(e)) {
      await signOut();
      if (onSignedOut) onSignedOut();
      return;
    }
    console.error("[xbrain] chat boot failed:", e);
    setConnectionBanner("Could not reach the server. Reload to try again.");
    return;
  }

  // 3. No teams is a real state, not an error.
  if (!Array.isArray(state.teams) || state.teams.length === 0) {
    renderEmptyTeams();
    return;
  }

  // 4. The picker, defaulting to the team they read last.
  state.activeTeamId = (await preferredTeamId()) || state.teams[0].id;
  renderTeamSelector();

  // 5. Connect. The socket target is whatever POST /v1/me/centrifugo-token
  //    returned — this file names no host and no scheme (D-27-03).
  //    A failure here must cost live updates and nothing else: the token mint is
  //    a network call, and letting it throw would take history and sending down
  //    with it.
  try {
    state.realtime = await connectRealtime({
      // The vendored client publishes a global; chat-core never reaches for one,
      // so the surface that loaded it hands the constructor in.
      Centrifuge: globalThis.Centrifuge,
      api,
      getUserSub: () => (state.me && state.me.source_user_id) || null,
      onTeamPublication: handleTeamPublication,
      onUserPublication: handleUserPublication,
      // No onPresenceChange: this surface has no presence badge, and wiring the
      // callback would ship handlers that recompute nothing.
      onConnected: () => setConnectionBanner(null),
      onError: () => setConnectionBanner("Reconnecting..."),
    });
  } catch (e) {
    console.warn("[xbrain] realtime unavailable:", e);
    state.realtime = null;
  }
  if (!state.realtime) {
    setConnectionBanner("Live updates are off - reload to retry.");
  }

  // 6. Claim the channel and load the thread.
  wireComposer();
  await switchTeam(state.activeTeamId);

  // 7. Drop the caret in the composer so they can just type.
  focusComposer();
}

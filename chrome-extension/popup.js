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

// The label/class helpers (authorLabel, bubbleClass, provenanceLabel,
// brainSummaryLabel, indexedAttachment, formatRelative, sameDay, dayLabel) are
// no longer imported here — chat_core/render.js is their only consumer now.
import {
  StreamBuffer,
  buildMentionRegex,
  withAgentMention,
  hostnameFromUrl,
} from "./chat_core/chat_stream.js";
import { loadSettings, saveSettings } from "./settings.js";
import { handleOpenUrl, isSafeHttpUrl } from "./chat_core/nudge_open.js";
import {
  THEME_STORAGE_KEY,
  resolveInitialTheme,
  applyTheme,
} from "./chat_core/theme.js";
import { createApi, MAX_MEDIA_BYTES } from "./chat_core/api.js";
import { createRenderer } from "./chat_core/render.js";
import { createPublicationRouter } from "./chat_core/publication.js";
import { createMessageMenu, removeMessageRow } from "./chat_core/message_menu.js";
import { connectRealtime } from "./chat_core/realtime.js";
import { createTeamRail } from "./chat_core/team_rail.js";
import { createPeoplePanel } from "./chat_core/people.js";
import { createInvitePanel } from "./chat_core/invite.js";
import { renderTeamStarter } from "./chat_core/teams.js";
import { chromePlatform } from "./platform_chrome.js";

const MEMORY_API_BASE = "https://api.grooveos.app";

// ---------- memory-api client (Phase 27, D-27-04) ----------
//
// ONE client for the whole popup: it is the only place a Bearer header is
// assembled, and the only place a non-2xx becomes an Error. `baseUrl` is
// injected here because the origin belongs to THIS surface — chat_core/api.js
// carries no origin at all, so the PWA cannot be repointed by a shared edit.
// The token is read through the platform shim rather than the extension's
// storage API directly, which is what makes the same client work in the PWA.
const api = createApi({
  baseUrl: MEMORY_API_BASE,
  getToken: async () =>
    (await chromePlatform.storage.get(["xbt_token"])).xbt_token || null,
});

// ---------- App state (single instance per popup open) ----------

const state = {
  me: null,            // /v1/me result {id, source_user_id, email, ...}
  teams: [],           // [{id, slug, display_name, github_org}, ...]
  activeTeamId: null,
  realtime: null,      // chat_core/realtime.js handle (connection + subscriptions)
  presenceCount: 0,
  streamBuffer: new StreamBuffer(),
  nameCache: {},       // author_user_id → display name
  oldestLoadedTs: null,
  historyPaging: false,
  initialLoaded: false,
  // Agent-mention aliases for the active team. Built from the server's
  // effective list (GET /v1/teams/{id}/agent-aliases); defaults to ["agent"]
  // until the first fetch. The server is authoritative for the actual summon —
  // this copy exists so the agent toggle writes the team's OWN alias and can
  // tell an already-mentioned draft from a bare one.
  agentAliases: ["agent"],
  mentionRe: buildMentionRegex(["agent"]),
  // Agent toggle: armed before sending, cleared by a successful send. Never
  // persisted — a shared team chat must not open with the agent silently armed.
  agentArmed: false,
  // Whether the SERVER says this person may remove OTHER people's messages in
  // the team that is open. It rides on the history response (one reader; a
  // per-message field would be broadcast to the whole team) and is re-read on
  // every switch, because the answer is per-team. False until told otherwise.
  viewerCanModerate: false,
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

// ---------- Shared chat core (Phase 27, D-27-04) ----------
//
// The renderer and the websocket frame router live in packages/chat-core and are
// imported, not copied — the PWA drives the SAME code, so a fix to a bubble or a
// stream lands on both surfaces at once. Everything extension-specific (the
// document, the two containers, the API origin, the people overlay) is injected
// here and nowhere else.
//
// Built after DOM ready because the renderer captures #message-list and
// #chat-scroll; every caller runs after boot(), so a module-level `let` is safe.
let renderer = null;
let routeTeamFrame = () => {};
let messageMenu = null;

// The two overlays, also from packages/chat-core. They used to be ~450 lines of
// popup-only code that the PWA had to either copy or go without; both surfaces
// now drive the same modules and this file supplies only what is genuinely
// local — its document, its element ids, its API origin and its platform shim.
let peoplePanel = null;
let invitePanel = null;

// state.streamBuffer is REPLACED on every team switch (a fresh buffer per team).
// The router is built once, so it reads the buffer through this facade rather
// than capturing an instance a later switch would leave stale.
const streamBufferFacade = {
  start: (id) => state.streamBuffer.start(id),
  append: (id, delta) => state.streamBuffer.append(id, delta),
  get: (id) => state.streamBuffer.get(id),
  finalize: (id) => state.streamBuffer.finalize(id),
};

function buildChatCore() {
  renderer = createRenderer({
    doc: document,
    listEl: $("message-list"),
    scrollEl: $("chat-scroll"),
    apiBase: MEMORY_API_BASE,
    getSelfUserId: () => state.me?.id,
    getNameCache: () => state.nameCache,
    // Click a teammate's name to open the people overlay on their row. Read
    // late through the module-level binding: the panel is built in the same
    // DOMContentLoaded pass, and a captured null would leave every name dead.
    onAuthorClick: (uid) => peoplePanel && peoplePanel.open({ focusUserId: uid }),
    // Hovering an attachment's marker shows the text the brain actually indexed
    // from it. The renderer calls this at most once per item and only once
    // somebody asks, so a thread of fifty images costs nothing until it is used.
    // The route is scoped by team SLUG, not by the chat's team id.
    fetchIndexedText: (itemId) => {
      const team = state.teams.find((t) => t.id === state.activeTeamId);
      if (!team) return Promise.resolve(null);
      return api.indexedText(team.slug, itemId);
    },
  });
  routeTeamFrame = createPublicationRouter({
    renderer,
    streamBuffer: streamBufferFacade,
    // The surface owns its own "no messages yet" panel, so the router only
    // reports that a frame proved the thread is non-empty.
    onNonEmpty: () => {
      $("chat-empty").hidden = true;
    },
    onMessageDeleted: (messageId) => dropMessageRow(messageId),
  });

  // Right-click, long-press, or the keyboard: the per-message actions overlay.
  // It listens on the LIST, so it is attached once and covers every row that
  // will ever arrive — including the ones the websocket brings in later.
  messageMenu = createMessageMenu({
    doc: document,
    listEl: $("message-list"),
    scrollEl: $("chat-scroll"),
    getActiveTeamId: () => state.activeTeamId,
    getSelfUserId: () => state.me?.id,
    // The server's own answer, from the history response. Drawing the control
    // anywhere else would mean guessing at a rule the server enforces.
    getViewerCanModerate: () => state.viewerCanModerate,
    deleteMessage: (teamId, messageId, scope) =>
      api.deleteMessageRaw(teamId, messageId, scope),
    onDeleted: (messageId) => dropMessageRow(messageId),
  });
  messageMenu.attach();
}

/**
 * Take a removed message off this screen and put the thread back together.
 *
 * Both paths land here — the frame that says somebody else removed it, and the
 * local call after this person's own DELETE returned 200. Removal is keyed on
 * the id, so running twice is a no-op; that is what lets both exist without
 * either knowing about the other, and it is what keeps the feature working when
 * the socket is down.
 *
 * The separator reconcile is not cosmetic: the removed row may have been the only
 * message under a date heading, and a heading left standing over nothing is the
 * visible half of a deletion that only half happened.
 */
function dropMessageRow(messageId) {
  if (!removeMessageRow($("message-list"), messageId)) return;
  renderer.syncDaySeparators();
  if (($("message-list").children || []).length === 0) {
    $("chat-empty").hidden = false;
  }
}

/** The live team subscription — owned by the realtime handle, read for presence. */
const teamSub = () => (state.realtime ? state.realtime.teamSubscription : null);

/**
 * Hand the two shared panels this surface's elements.
 *
 * Every id is written as a literal $() call, which is also what the popup
 * contract test walks: an element the markup stopped shipping goes red here
 * rather than at the click that needed it.
 */
function buildPanels() {
  peoplePanel = createPeoplePanel({
    doc: document,
    api,
    apiBase: MEMORY_API_BASE,
    // The extension CAN see the page you are looking at, so the URL field
    // arrives pre-filled. The shared panel writes the hint that says so — and
    // writes the "paste a link" one on a surface that cannot (the PWA).
    platform: chromePlatform,
    els: {
      panel: $("people-panel"),
      list: $("people-list"),
      urlInput: $("people-url"),
      status: $("people-status"),
      hint: $("people-hint"),
      filePicker: $("people-file"),
    },
    getActiveTeamId: () => state.activeTeamId,
    getTeams: () => state.teams,
    getTeamSubscription: teamSub,
  });

  invitePanel = createInvitePanel({
    doc: document,
    api,
    apiBase: MEMORY_API_BASE,
    els: {
      panel: $("invite-panel"),
      codeRow: $("invite-code-row"),
      codeOutput: $("invite-code-output"),
      status: $("invite-status"),
      emailInput: $("invite-email"),
      emailStatus: $("invite-email-status"),
      joinInput: $("invite-join-code"),
      joinStatus: $("invite-join-status"),
      mintBtn: $("btn-invite-mint"),
      emailBtn: $("btn-invite-email"),
      joinBtn: $("btn-invite-join"),
    },
    getActiveTeamId: () => state.activeTeamId,
    onJoined: refreshTeamsAfterJoin,
  });
}

// ---------- Boot ----------

document.addEventListener("DOMContentLoaded", async () => {
  // Apply the theme before anything paints so there is no light/dark flash.
  await wireTheme();
  buildPanels();
  buildChatCore();
  wireHeader();
  wireConnectionCard();
  wireComposer();
  wireClipOverlay();
  wirePeople();
  wireInvite();
  wireCatchup();
  await boot();
});

async function boot() {
  // 1. Check connection state. If no xbt_token → show connection card,
  //    let the user click Connect; chat boots once they're authed.
  const { xbt_token } = await chromePlatform.storage.get(["xbt_token"]);
  if (!xbt_token) {
    showConnectionCard();
    return;
  }
  hideConnectionCard();

  // 2. Fetch /v1/me + teams in parallel.
  try {
    state.me = await api.me();
    state.teams = await api.myTeams();
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

  // 3. Mint Centrifugo token + connect. The socket URL is whatever
  //    POST /v1/me/centrifugo-token returned — never a constant (D-27-03).
  state.realtime = await connectRealtime({
    // The vendored client publishes a global; the shared module never reaches
    // for one, so the surface hands the constructor in.
    // eslint-disable-next-line no-undef -- global from vendor/centrifuge.js
    Centrifuge: globalThis.Centrifuge,
    api,
    getUserSub: () => state.me?.source_user_id || null,
    onTeamPublication: (data) => routeTeamFrame(data),
    onUserPublication: handleUserPublication,
    onPresenceChange: updatePresenceFromAPI,
    previous: state.realtime,
  });

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
  wireNotificationToggle();
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
    const data = await api.request(
      `/v1/teams/${state.activeTeamId}/boards`,
      { method: "POST", body: { title: "Team board" } },
    );
    // Validate the server-built URL's scheme before handing it to the browser,
    // and never log it — the fragment is a bearer secret for this one board.
    // `data` is null when the server answers with an empty body — treat that as
    // a rejection rather than dereferencing it.
    if (!data || !data.open_url || !isSafeHttpUrl(data.open_url)) {
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

/// ---------- People + invite overlays (Phase 22 D-22-05, Phase 25 JOINCODE-01) ----------
//
// Both overlays are packages/chat-core modules now (D-27-04). What used to live
// here — the member list, the presence dots, the nudge fan-out, the file upload,
// the mint/copy/join/email flows and their six status helpers — was ~450 lines
// that the PWA could only have by copying them. The rules inside are not
// re-derivable on a second reading (a self-nudge 422 is EXPECTED, an upload is
// nudged by its signed URL because the recipient's browser sends no Bearer
// header, the join code rides in the URL FRAGMENT so it never reaches a log),
// which is exactly the kind of code that must not exist twice.
//
// This file keeps only what is local to the extension: which element is which,
// and which of them opens what.

function wirePeople() {
  // The header button is the PEOPLE icon: you almost always mean "send this page
  // to that person", so the recipient is a click, not a form.
  const openBtn = $("btn-send-link");
  if (openBtn) openBtn.addEventListener("click", () => peoplePanel.open());
  const peopleClose = $("btn-people-close");
  if (peopleClose) peopleClose.addEventListener("click", () => peoplePanel.close());
  const peopleCancel = $("btn-people-cancel");
  if (peopleCancel) peopleCancel.addEventListener("click", () => peoplePanel.close());
  const sendAll = $("btn-people-send-all");
  if (sendAll) {
    sendAll.addEventListener("click", () => peoplePanel.sendToEveryone(sendAll));
  }
}

function wireInvite() {
  const openBtn = $("btn-invite");
  if (openBtn) openBtn.addEventListener("click", () => invitePanel.open());
  const closeBtn = $("btn-invite-close");
  if (closeBtn) closeBtn.addEventListener("click", () => invitePanel.close());
  const cancelBtn = $("btn-invite-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => invitePanel.close());
  const mintBtn = $("btn-invite-mint");
  if (mintBtn) mintBtn.addEventListener("click", () => invitePanel.mint());
  const copyBtn = $("btn-invite-copy");
  if (copyBtn) copyBtn.addEventListener("click", () => invitePanel.copy());
  const joinBtn = $("btn-invite-join");
  if (joinBtn) joinBtn.addEventListener("click", () => invitePanel.join());
  const emailBtn = $("btn-invite-email");
  if (emailBtn) emailBtn.addEventListener("click", () => invitePanel.addByEmail());
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
    const teams = await api.myTeams();
    if (Array.isArray(teams) && teams.length) {
      state.teams = teams;
      renderTeamSelector();
    }
  } catch (e) {
    console.warn("[xbrain] team refresh after join failed:", e);
  }
}

// ---------- Theme (in-popup light/dark toggle) ----------

/**
 * Wire the header light/dark segmented toggle. Theme logic lives in the pure
 * chat_core/theme.js module; this function owns the impure parts: reading and
 * writing through the platform storage shim, and stamping the DOM. The stored
 * choice wins over the OS preference and persists across popup opens (20-01).
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
        await chromePlatform.storage.set({ [THEME_STORAGE_KEY]: mode });
      } catch (e) {
        console.warn("[xbrain] theme persist failed:", e);
      }
    }
  };

  // Boot: stored choice wins; first-ever open falls back to prefers-color-scheme.
  let storedTheme = null;
  try {
    const got = await chromePlatform.storage.get([THEME_STORAGE_KEY]);
    storedTheme = got ? got[THEME_STORAGE_KEY] : null;
  } catch (e) {
    console.warn("[xbrain] theme read failed:", e);
  }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  await set(resolveInitialTheme({ storedTheme, prefersDark }), false);

  if (lightBtn) lightBtn.addEventListener("click", () => set("light", true));
  if (darkBtn) darkBtn.addEventListener("click", () => set("dark", true));
}

/**
 * The bell — mute every desktop notification this extension raises.
 *
 * It edits ONE setting, which every call site consults (settings.js's
 * notificationsEnabled). The alternative — muting only what the popup itself
 * raises — would leave clip results and "selection captured" arriving from a
 * switch the person had just turned off, and that is worse than no switch.
 *
 * The stored value is read on open, not assumed: chrome.storage.sync means the
 * choice may have been made on another machine, or on the options page a moment
 * ago. Both bells are already in the markup; only the data-state moves.
 */
function wireNotificationToggle() {
  const btn = $("btn-notifications");
  if (!btn) return;

  const paint = (on) => {
    const label = on ? "Notifications on" : "Notifications off";
    btn.setAttribute("data-state", on ? "on" : "off");
    btn.setAttribute("aria-pressed", String(on));
    btn.title = label;
    btn.setAttribute("aria-label", label);
  };

  // Reflect what is actually stored. On a read failure the default (ON) stands,
  // which is also what every call site falls back to — so the icon still tells
  // the truth about what will happen.
  loadSettings(chrome.storage.sync)
    .then((s) => paint(s.showNotifications !== false))
    .catch(() => paint(true));

  btn.addEventListener("click", async () => {
    const next = btn.getAttribute("data-state") !== "on";
    paint(next); // optimistic: the click must feel instant
    try {
      await saveSettings(chrome.storage.sync, { showNotifications: next });
    } catch (e) {
      console.warn("[xbrain] notification setting not saved:", e);
      paint(!next); // roll the icon back rather than lie about what was stored
    }
  });
}

/**
 * The rail, built by the SHARED module (D-27-04).
 *
 * Everything it needs is read late — the team list and the active id both change
 * after boot — so one instance serves the whole session and nothing here has to
 * remember to rebuild it.
 *
 * The unread lookup goes through rawFetch rather than api.unreadSummary because a
 * badge must never surface an error: a non-200 answers "no badge" and the next
 * team is still tried.
 */
const teamRail = createTeamRail({
  doc: document,
  railEl: $("team-rail"),
  storage: chromePlatform.storage,
  getTeams: () => state.teams,
  getActiveTeamId: () => state.activeTeamId,
  onSelectTeam: (id) => switchTeam(id),
  getUnreadCount: async (teamId) => {
    // Phase 23's unread-summary — the same cursor the catch-me-up banner reads.
    const { xbt_token: token } = await chromePlatform.storage.get(["xbt_token"]);
    if (!token) return null;
    const res = await api.rawFetch(`/v1/teams/${teamId}/unread-summary`);
    if (!res.ok) return null;
    const { count } = await res.json();
    return count;
  },
});

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
  // Fire-and-forget: the rail reads the saved order from storage, so it paints a tick
  // later than the (hidden) select. Caught so a storage hiccup can't surface as an
  // unhandled rejection — a rail that fails to paint must not break the chat.
  teamRail.render().catch(() => {});
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

// ---------- User channel (direct open_url nudges — Phase 22, D-22-02) ----------
//
// The subscription itself is created by chat_core/realtime.js — it claims
// `user:<source_user_id>` ONCE per connection, so a reconnect or a team switch
// can never double-subscribe. What stays here is the frame handler: catch-me-up
// is extension-only for now, and the nudge path needs the browser capabilities
// the shared module deliberately has none of.

/**
 * Route a frame from the user channel. Only `open_url` events are handled; any
 * other frame is ignored. Delegates entirely to nudge_open.handleOpenUrl with
 * chrome-backed deps — the popup itself has no tab-opening capability here.
 *
 * @param {{type?: string}|null|undefined} data
 */
async function handleUserPublication(data) {
  if (!data || !data.type) return;

  // Brain-tag frames (migration 0034) arrive HERE, on the personal channel,
  // because that is the only place they are published. The channel is
  // CROSS-TEAM — one socket carries every team this person belongs to — so the
  // team_id on the frame is what stops a note written in team A from painting
  // into team B's open thread. A frame without one is not a chat frame.
  if (data.team_id && data.team_id === state.activeTeamId) {
    const t = String(data.type);
    if (
      t === "message" ||
      t === "message_deleted" ||
      t === "message_starred" ||
      t.startsWith("agent_stream_")
    ) {
      routeTeamFrame(data);
      return;
    }
  }

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
      // Through the shim, not a bare create: that is the one place the popup
      // raises a notification, and the one place the mute setting is honoured.
      // A null id (muted) is a case handleOpenUrl already handles.
      notify: (opts) => chromePlatform.notify(opts),
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
  // Tear down any prior subscription FIRST, so no frame from the team we are
  // leaving can land in the list we are about to clear.
  if (state.realtime) state.realtime.unsubscribeTeam();

  state.activeTeamId = teamId;
  state.streamBuffer = new StreamBuffer();
  state.oldestLoadedTs = null;
  state.initialLoaded = false;
  // Reset catch-me-up UI/state — dismissal and window are per-team-visit.
  state.catchup.since = null;
  state.catchup.dismissedSince = undefined;
  state.catchup.readyForAutoMarkRead = false;  // BL-01: armed only after the banner is captured
  // Moderation is per-team and the answer arrives with this team's history. Held
  // false across the switch so a moment of the previous team's permission can
  // never draw a control in the new one.
  state.viewerCanModerate = false;
  if (messageMenu) messageMenu.close();
  const switchGen = ++state.switchGen;  // WR-03: re-entrancy token for this switch
  const prevBanner = $("catchup-banner");
  if (prevBanner) prevBanner.hidden = true;
  const prevSummary = $("catchup-summary");
  if (prevSummary) prevSummary.hidden = true;
  renderer.clear();
  resetChatEmpty();
  setPresence(0);

  // Claim the new team channel. The realtime handle owns the whole lifecycle —
  // publication routing and the presence callbacks are rewired with it, and the
  // user channel is untouched. A null handle means the vendored client did not
  // load: history still renders, only live updates are missing.
  if (state.realtime) state.realtime.subscribeTeam(teamId);

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
    await api.markRead(state.activeTeamId);
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
    const data = await api.unreadSummary(state.activeTeamId);
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
    const res = await api.rawFetch(
      `/v1/teams/${state.activeTeamId}/catch-me-up`,
      { method: "POST" },
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
//
// The composer no longer narrates a pending mention, so the regex's remaining
// job is the agent toggle's de-dupe: a draft that already names the agent must
// not have a second mention prepended to it.
async function refreshAgentAliases() {
  if (!state.activeTeamId) return;
  try {
    const data = await api.agentAliases(state.activeTeamId);
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
    const stats = await teamSub().presenceStats();
    setPresence(stats?.numUsers ?? 0);
  } catch {
    /* presence is optional */
  }
}

// ---------- History ----------

async function loadInitialHistory() {
  if (state.initialLoaded) return;
  state.initialLoaded = true;
  const data = await api.listMessages(state.activeTeamId);
  // What the SERVER says this person may do to other people's messages here.
  // Read before anything is drawn, so the first right-click after a switch
  // already has the right answer.
  state.viewerCanModerate = Boolean(data?.viewer?.can_moderate);
  // Response is newest-first; render oldest-first.
  const msgs = (data.messages || []).slice().reverse();
  if (msgs.length === 0) {
    $("chat-empty").hidden = false;
    return;
  }
  $("chat-empty").hidden = true;
  for (const m of msgs) renderer.renderMessage(m, { prepend: false });
  renderer.syncDaySeparators();
  state.oldestLoadedTs = msgs[0].created_at;
  // Initial load: pin to the latest message regardless of scrollTop.
  renderer.scrollToBottom({ force: true });
}

async function loadOlderPage() {
  if (state.historyPaging || !state.oldestLoadedTs) return;
  state.historyPaging = true;
  $("history-loader").hidden = false;
  try {
    const data = await api.listMessages(state.activeTeamId, {
      before: state.oldestLoadedTs,
    });
    const olderMsgs = (data.messages || []).slice().reverse();
    const list = $("message-list");
    // Preserve scroll position: measure top sentinel before prepend.
    const scrollEl = $("chat-scroll");
    const prevHeight = scrollEl.scrollHeight;
    for (const m of olderMsgs) {
      renderer.renderMessage(m, { prepend: true });
    }
    renderer.syncDaySeparators();
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
  $("btn-agent").addEventListener("click", () => setAgentArmed(!state.agentArmed));
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

/**
 * Arm or disarm the agent toggle.
 *
 * The button carries the state in TWO attributes because they answer two
 * different readers: [data-state] is what the stylesheet keys the filled
 * selected look off, [aria-pressed] is what a screen reader announces. Writing
 * one and not the other would leave the control looking armed and reading unarmed.
 */
function setAgentArmed(on) {
  state.agentArmed = Boolean(on);
  const btn = $("btn-agent");
  if (!btn) return;
  btn.dataset.state = state.agentArmed ? "on" : "off";
  btn.setAttribute("aria-pressed", state.agentArmed ? "true" : "false");
}

async function sendMessage() {
  const input = $("composer-input");
  const typed = input.value.trim();
  if (!typed || !state.activeTeamId) return;
  // ONE summon mechanism: the toggle writes the mention a person would type, and
  // the server's detector decides from the text either way. withAgentMention
  // leaves an already-mentioned draft alone, so toggling AND typing "@agent"
  // summons once — as it would anyway, since the server acts on the first
  // mention only, but "@agent @agent ..." is a message nobody wrote.
  const content = state.agentArmed
    ? withAgentMention(typed, { aliases: state.agentAliases, regex: state.mentionRe })
    : typed;
  const sendBtn = $("btn-send");
  sendBtn.disabled = true;
  try {
    const sent = await api.postMessage(state.activeTeamId, { content });
    input.value = "";
    autoResize(input);
    // Disarmed once the message is away — and only on success, so a failed send
    // leaves the draft AND its intent intact. This is a shared team chat: an
    // armed toggle nobody noticed would send the next line meant for teammates
    // to the agent as well, in front of everyone.
    setAgentArmed(false);
    // Optimistic render: show the message immediately from the POST response
    // instead of waiting for the Centrifugo echo (which can lag or be missed
    // while the popup is backgrounded). renderMessage() de-dupes by id, so the
    // later "message" publication for the same id is a no-op.
    if (sent && sent.id) {
      renderer.renderMessage(sent, { prepend: false });
      renderer.scrollToBottom();
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
 * The transport lives in chat-core's client (api.uploadMedia) so the PWA sends
 * the same request rather than growing a second multipart call site.
 */
async function uploadFile(file) {
  if (file.size > MAX_MEDIA_BYTES) {
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

  const clipBtn = $("btn-clip");
  if (clipBtn) clipBtn.disabled = true;

  try {
    const item = await api.uploadMedia(team.slug, file, "extension");
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
  try {
    const sent = await api.postMessage(state.activeTeamId, {
      content: filename,
      media: {
        item_id: item.item_id,
        mime: item.mime,
        size: item.size,
        filename,
      },
    });
    // Optimistic render: show immediately from the POST response;
    // the Centrifugo echo for the same id will be de-duped by renderMessage().
    if (sent && sent.id) {
      renderer.renderMessage(sent, { prepend: false });
      renderer.scrollToBottom();
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
    const { xbt_token } = await chromePlatform.storage.get(["xbt_token"]);
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
//
// The popup's local JSON helper lived here until Phase 27. It now lives in
// packages/chat-core/api.js — same Bearer header, same `HTTP <status>: <body>`
// throw shape — so the PWA runs the identical client instead of a second copy
// that would drift (D-27-04). The module-level `api` at the top of this file is
// the only entry point; multipart uploads stay on raw fetch because the browser
// must own their Content-Type boundary.

/**
 * Put the ordinary "no messages yet" copy back into #chat-empty.
 *
 * The empty-TEAMS state renders the create-or-join panel into this SAME element,
 * so without this the person who has just created their first team is shown a
 * "Create team" form again — inside the empty chat they just created. Rebuilt
 * from nodes rather than a markup string, because this element has just held a
 * team name somebody typed.
 *
 * A no-op when the default copy is already there, so the ordinary team switch
 * pays nothing for it.
 */
function resetChatEmpty() {
  const el = $("chat-empty");
  if (!el || !el.querySelector(".xb-starter")) return;
  while (el.firstChild) el.removeChild(el.firstChild);
  el.style.pointerEvents = "";
  el.appendChild(
    document.createTextNode("No messages yet. Be the first to say hello — or mention "),
  );
  const code = document.createElement("code");
  code.textContent = "@agent";
  el.appendChild(code);
  el.appendChild(document.createTextNode(" to ask the team brain a question."));
}

/**
 * Empty state — the product is a TEAM chat, so this offers the only two things
 * a person with no team can do: create one, or redeem the invite they were
 * sent. Both doors are chat-core's now (the PWA opens the same two), so what is
 * left here is what belongs to THIS surface: the hidden <select> the popup
 * still writes through, and what "the team exists now" means for an extension.
 *
 * The same panel is what the "+" beside the team rail shows — "add a team"
 * means both, and a founder and an invitee should not need different doors.
 */
function renderEmptyTeams() {
  // Two situations, one panel. With no teams this IS the screen; with teams it
  // is what the "+" opened, over a chat somebody was reading — so the hidden
  // selector keeps its real options, and there is a way back.
  const hasTeams = state.teams.length > 0;
  if (!hasTeams) {
    $("teamSelector").innerHTML = `<option disabled selected>No teams yet</option>`;
  }
  const emptyEl = $("chat-empty");
  emptyEl.hidden = false;
  emptyEl.style.pointerEvents = "auto";

  renderTeamStarter({
    doc: document,
    hostEl: emptyEl,
    api,
    // Only when there is a chat behind this panel to go back to.
    onCancel: hasTeams
      ? () => {
          resetChatEmpty();
          // Hidden again, unless the thread really is empty — in which case the
          // ordinary "no messages yet" copy is exactly what belongs here.
          emptyEl.hidden = Boolean($("message-list").firstChild);
        }
      : null,
    // Created: rebuild the right-click submenu so the new team shows up there
    // too, boot so the rail and the chat have it, and then go STRAIGHT to
    // inviting with a link already minted. A team of one is not the product,
    // and leaving somebody alone in an empty room to work out the next step is
    // how that happens.
    onTeamCreated: async () => {
      chrome.runtime.sendMessage({ type: "REFRESH_TEAMS_MENU" }).catch(() => {});
      await boot();
      if (state.activeTeamId) await invitePanel.openAndMint();
    },
    onTeamJoined: async () => {
      chrome.runtime.sendMessage({ type: "REFRESH_TEAMS_MENU" }).catch(() => {});
      await boot();
    },
  });
}

// createSoloTeam() lived here and posted to /v1/teams/self-solo. Removed with the
// solo-workspace empty state: the product is a team chat, so the first action is
// creating a TEAM and inviting people into it (chat-core's teams.js owns both
// doors now). The endpoint still exists server-side for the first-login path;
// nothing in the popup calls it.

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

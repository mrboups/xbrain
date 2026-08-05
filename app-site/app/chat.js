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
 * OUT OF SCOPE, deliberately: the board, the personal-summary panel and the web
 * clipper. The members overlay, the invite overlay and the create-or-join panel
 * are in — they live in panels.js, which owns their state and their ids so this
 * file stays the chat and nothing else.
 */

import { createApi, MAX_MEDIA_BYTES } from "./chat_core/api.js";
import {
  createRenderer,
  createViewportAnchor,
  isNearBottom,
} from "./chat_core/render.js";
import { createPublicationRouter } from "./chat_core/publication.js";
import { createMessageMenu, removeMessageRow } from "./chat_core/message_menu.js";
import { connectRealtime, createConnectionStatus } from "./chat_core/realtime.js";
import { createTeamRail } from "./chat_core/team_rail.js";
import { createSwipeNavigator } from "./chat_core/swipe_nav.js";
import {
  StreamBuffer,
  buildMentionRegex,
  withAgentMention,
  agentRouteStatusText,
  createSubscriptionWatcher,
  SUBSCRIPTION_LOST_NOTICE,
  SUBSCRIPTION_NOTICE_ACTION,
} from "./chat_core/chat_stream.js";
import { handleOpenUrl, isSafeHttpUrl } from "./chat_core/nudge_open.js";
import { keepFocusOnPress } from "./chat_core/dom.js";
import { webPlatform } from "./platform_web.js";
import { onViewportChange } from "./viewport.js";
import { ensureBridge } from "./bridge_link.js";
import { bootPanels } from "./panels.js";
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
  // The active team's EFFECTIVE agent aliases, from the server. The server is
  // authoritative for the actual summon; this copy exists so the agent toggle
  // writes the team's OWN alias — a team that renamed its agent must not get a
  // mention the detector ignores — and can tell an already-mentioned draft from
  // a bare one. Defaults to the additive base alias until the first fetch.
  agentAliases: ["agent"],
  mentionRe: buildMentionRegex(["agent"]),
  // Agent toggle: armed before sending, cleared by a successful send. Never
  // persisted — a shared team chat must not open with the agent silently armed.
  agentArmed: false,
  // Whether the SERVER says this person may remove other people's messages in
  // the team that is open. It comes back on the history response (one reader,
  // never broadcast) and is re-read on every team switch, because the answer is
  // per-team. False until told otherwise: a control that appears and then gets a
  // 403 is worse than one that appears a second late.
  viewerCanModerate: false,
};

/** The refs bootChat was last called with — see reloadTeams for why. */
let lastBootRefs = {};

const api = createApi({ baseUrl: MEMORY_API_BASE, getToken });

/**
 * The overlays. Built here, at module scope, so the renderer below can hand a
 * live callback into them; everything they read about the current team is a
 * function, because none of it exists yet at this point in the file.
 */
const panels = bootPanels({
  api,
  getActiveTeamId: () => state.activeTeamId,
  getTeams: () => state.teams,
  getTeamSubscription: () => (state.realtime ? state.realtime.teamSubscription : null),
  onTeamsChanged: (preferTeamId) => reloadTeams(preferTeamId),
  onStarterDismissed: showEmptyThreadCopy,
});

const renderer = createRenderer({
  doc: document,
  listEl: el("message-list"),
  scrollEl: el("chat-scroll"),
  apiBase: MEMORY_API_BASE,
  getSelfUserId: () => state.me && state.me.id,
  getNameCache: () => state.nameCache,
  // Click a teammate's name to act on THEM: the members list opens with their
  // row highlighted, so sending a link or a file starts from the message you
  // are reading instead of reopening a list and finding them again.
  onAuthorClick: (userId) => panels.openPeople(userId),
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
  onMessageDeleted: (messageId) => dropMessageRow(messageId),
});

/**
 * Take a removed message off this screen and put the thread back together.
 *
 * Both paths land here — the frame that says somebody else removed it, and the
 * local call after this person's own DELETE returned 200. Removal is keyed on
 * the id, so running twice is a no-op, which is what lets both exist without
 * either having to know about the other.
 *
 * The separator reconcile is not optional: the removed row may have been the only
 * message under a date heading, and a heading left standing over nothing is the
 * visible half of a deletion that only half happened.
 */
function dropMessageRow(messageId) {
  if (!removeMessageRow(el("message-list"), messageId)) return;
  renderer.syncDaySeparators();
  const list = el("message-list");
  if (list && (list.children || []).length === 0) showEmptyThreadCopy();
}

/**
 * Right-click, long-press, or the keyboard: the per-message actions overlay.
 *
 * Built here rather than inside the renderer — it reads the rows the renderer
 * produced and never draws one. The server is the authority on who may delete;
 * `getViewerCanModerate` is the answer it gave on the history response, so the
 * control is drawn exactly where the server would allow it.
 */
const messageMenu = createMessageMenu({
  doc: document,
  listEl: el("message-list"),
  scrollEl: el("chat-scroll"),
  getActiveTeamId: () => state.activeTeamId,
  getSelfUserId: () => state.me && state.me.id,
  getViewerCanModerate: () => state.viewerCanModerate,
  deleteMessage: (teamId, messageId, scope) =>
    api.deleteMessageRaw(teamId, messageId, scope),
  onDeleted: (messageId) => dropMessageRow(messageId),
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
 * The socket's state, as one line of text or none.
 *
 * The machine lives in chat-core; this surface owns only where the string goes.
 * Nothing here reacts to a Centrifuge `error` — that is an incident, and the
 * banner reports a state.
 */
const connectionStatus = createConnectionStatus({ render: setConnectionBanner });

// ---------- Which model is answering, and who pays ----------
//
// Both halves read ONE server answer: GET /v1/me/agent-route, which runs the
// agent's own resolution. Nothing here infers routing from whether an extension
// replied to a message — a client that decides that for itself eventually
// disagrees with the thing that routes the turn, and then the status is wrong
// in the one direction that destroys trust.

/** How often to re-ask while the page is in front of somebody. */
const ROUTE_POLL_MS = 60_000;

const subscriptionWatcher = createSubscriptionWatcher();
let routePollTimer = null;

/** The quiet line above the composer. Hidden when there is nothing to say. */
function renderRouteStatus(status) {
  const node = el("agent-route-status");
  if (!node) return;
  const text = agentRouteStatusText(status);
  node.textContent = text || "";
  node.hidden = !text;
}

/**
 * The notice shown when a bridge that WAS live goes away.
 *
 * Built rather than toggled so the dismiss button is wired exactly once per
 * appearance, and torn down completely when it should not be on screen.
 */
function renderSubscriptionNotice(showing) {
  const node = el("subscription-notice");
  if (!node) return;
  if (!showing) {
    node.hidden = true;
    while (node.firstChild) node.removeChild(node.firstChild);
    return;
  }
  if (!node.hidden && node.firstChild) return; // already up — do not rebuild
  while (node.firstChild) node.removeChild(node.firstChild);

  const text = document.createElement("span");
  text.className = "xb-subscription-notice-text";
  text.textContent = SUBSCRIPTION_LOST_NOTICE;
  node.appendChild(text);

  // THE ROUTE THE NOTICE USED TO LACK. It named a remedy - a team API key -
  // that could only be set on the desktop admin page, which a standalone app
  // with no address bar cannot reach. The advice was a dead end in exactly the
  // situation it appears in.
  //
  // A DESTINATION, NOT AN ACT: the write is admin-only, and a control that read
  // as "set the key" would promise a capability half the readers do not have.
  // What it opens tells an admin how and tells a member who to ask.
  if (typeof lastBootRefs.onOpenTeamKeys === "function") {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "xb-subscription-notice-action";
    open.textContent = SUBSCRIPTION_NOTICE_ACTION;
    // On a click and nothing else. A bridge drops whenever a laptop sleeps; a
    // sheet that opened by itself over a half-typed message, again and again,
    // is how a warning becomes something people dismiss without reading.
    open.addEventListener("click", () => lastBootRefs.onOpenTeamKeys());
    node.appendChild(open);
  }

  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "xb-subscription-notice-dismiss";
  dismiss.textContent = "Dismiss";
  dismiss.addEventListener("click", () => {
    subscriptionWatcher.dismiss();
    renderSubscriptionNotice(false);
  });
  node.appendChild(dismiss);
  node.hidden = false;
}

/**
 * Ask the server what the routing is, and act on the answer.
 *
 * Fail-soft in the strongest sense: a poll that cannot be answered changes
 * nothing on screen. A failed request is not evidence the bridge died, and
 * treating it as such would fire the notice every time a phone changed cell.
 */
async function refreshAgentRoute() {
  if (!state.activeTeamId) return;
  let status = null;
  try {
    status = await api.agentRoute(state.activeTeamId);
  } catch {
    return; // silent: this is an enhancement, not a feature that can fail
  }
  renderRouteStatus(status);
  renderSubscriptionNotice(subscriptionWatcher.observe(status));
}

/**
 * Poll while the page is in front of somebody, and stop when it is not.
 *
 * The routing changes rarely, so a tight interval would be a phone battery
 * spent on an answer that is almost always the same one. Hidden pages poll not
 * at all; becoming visible asks once, immediately, because that is exactly when
 * the answer is most likely to have changed while nobody was looking.
 */
function startRoutePolling() {
  if (routePollTimer !== null) return;
  const tick = () => {
    if (document.visibilityState === "visible") refreshAgentRoute();
  };
  routePollTimer = setInterval(tick, ROUTE_POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshAgentRoute();
  });
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

/**
 * No team yet — which is a starting point, not a dead end.
 *
 * It used to say "ask a teammate to invite you", which leaves the reader with
 * nothing they can do on this screen. The starter offers the two things they
 * actually have: create a team, or redeem the invite they were sent. The
 * members and invite controls stay hidden until there is a team for them to act
 * on; a control that can only 404 is worse than an absent one.
 */
function renderEmptyTeams() {
  panels.hide();
  panels.openStarter();
  const composer = el("composer");
  if (composer) composer.hidden = true;
}

/**
 * A team was just created, or an invite was just redeemed.
 *
 * @param {string} [preferTeamId] the team to land on — the one just created or
 *   joined, which is the one the person is thinking about.
 */
async function reloadTeams(preferTeamId) {
  let teams;
  try {
    teams = await api.myTeams();
  } catch (e) {
    console.warn("[xbrain] team refresh failed:", e);
    return;
  }
  if (!Array.isArray(teams) || teams.length === 0) return;
  state.teams = teams;

  // THE FIRST TEAM EVER is not a team switch. This session booted with no
  // socket because there was nothing to subscribe to, so the whole boot has to
  // run — otherwise the new team is live for nobody, including its founder.
  if (!state.realtime) {
    await bootChat(lastBootRefs);
    return;
  }

  const wanted =
    (preferTeamId && teams.some((t) => String(t.id) === String(preferTeamId)) && preferTeamId) ||
    (state.activeTeamId &&
      teams.some((t) => String(t.id) === String(state.activeTeamId)) &&
      state.activeTeamId) ||
    teams[0].id;

  revealChat();
  panels.reveal();
  await teamRail.render();
  await switchTeam(wanted);
}

/**
 * The ordinary empty-thread state — ONE owner for that sentence.
 *
 * Two callers need it: the history load that found nothing, and the "+"
 * starter being dismissed over a thread that happens to be empty. A second copy
 * of the string in panels.js is a second copy to forget about.
 */
function showEmptyThreadCopy() {
  const empty = el("chat-empty");
  const list = el("message-list");
  if (!empty) return;
  if (list && list.firstChild) {
    empty.hidden = true;
    return;
  }
  empty.textContent = "No messages in this team yet. Say something.";
  empty.hidden = false;
}

/** Put the chat frame back after the no-teams state hid the composer. */
function revealChat() {
  const composer = el("composer");
  if (composer) composer.hidden = false;
  const empty = el("chat-empty");
  if (empty) empty.hidden = true;
}

// ---------- Teams ----------

/**
 * The header's team rail — the SAME module the extension renders, not a picker
 * of its own (D-27-04). It replaces the <select> outright rather than shadowing
 * it: two controls writing one activeTeamId is exactly the drift this phase
 * exists to avoid, and a dropdown could not show which team has unread messages
 * anyway.
 *
 * Everything is read late, so one instance serves the whole session.
 */
const teamRail = createTeamRail({
  doc: document,
  railEl: el("team-rail"),
  storage: webPlatform.storage,
  getTeams: () => state.teams,
  getActiveTeamId: () => state.activeTeamId,
  onSelectTeam: (id) => switchTeam(id),
  // A badge must never surface an error, so a failed lookup answers "no badge"
  // and the next team is still tried.
  getUnreadCount: async (teamId) => {
    const summary = await api.unreadSummary(teamId);
    return summary ? summary.count : null;
  },
});

/**
 * Swipe the thread sideways to move along the rail — the phone gesture for what
 * the squares above already do with a tap.
 *
 * IT ASKS THE RAIL. `selectAdjacent` reads the order off the rail's own DOM and
 * calls the rail's own `onSelectTeam`, which is `switchTeam` below. So a swipe
 * and a tap are the same operation: the same teardown, the same subscription,
 * the same read cursor. A second order computed here would be right until the
 * first drag-to-reorder and silently wrong afterwards, and a switch that skipped
 * `switchTeam` would leave this screen showing one team while the socket carried
 * another — which looks like it worked, and is the worst thing this could do.
 *
 * Nothing happens at either end of the rail. Wired once at module scope; the
 * listeners live on the scroller, which outlives every team switch.
 */
const swipeTeams = createSwipeNavigator({
  surfaceEl: el("chat-scroll"),
  // Where a swipe is not a swipe: the text somebody is typing into, the rail
  // that already owns tap-and-drag, and the per-message overlay.
  blockSelectors: ["#composer", "#team-rail", ".xb-msg-menu", ".xb-msg-menu-scrim"],
  onSwipe: (direction) => {
    const moved = teamRail.selectAdjacent(direction === "next" ? 1 : -1);
    if (moved) markSwipeDirection(direction);
  },
});

/**
 * Tell the stylesheet which way the thread just moved, for one paint.
 *
 * The animation itself is declared inside a `prefers-reduced-motion:
 * no-preference` block, so a reader who has asked their OS for less motion gets
 * the new team with no movement at all — the attribute is still written and the
 * rule that reads it simply does not exist for them. Doing it that way rather
 * than testing the preference here means there is no second place to forget.
 */
function markSwipeDirection(direction) {
  const scrollEl = el("chat-scroll");
  if (!scrollEl) return;
  scrollEl.dataset.swipe = direction;
  window.setTimeout(() => {
    if (scrollEl.dataset.swipe === direction) delete scrollEl.dataset.swipe;
  }, 240);
}

/**
 * The slug of the team being read right now, or null when there is none.
 *
 * Exported for ONE caller: the settings sheet's picture upload, which has to
 * send `X-Team-Scope` for the team its media item was uploaded under. The SLUG
 * and not the id, for the same reason api.uploadMedia takes a slug —
 * /v1/media/upload is scoped through that header, unlike the chat endpoints
 * which carry the id in the path, and passing an id uploads into a scope that
 * does not exist.
 *
 * It is a function rather than a value because the active team changes under
 * every caller: a slug read once at boot is the wrong team by the first switch.
 */
export function activeTeamSlug() {
  const team = state.teams.find(
    (t) => String(t.id) === String(state.activeTeamId),
  );
  return (team && team.slug) || null;
}

/**
 * The id of the team being read right now, or null when there is none.
 *
 * The ID and not the slug, unlike above: /v1/teams/{id}/api-keys, /members and
 * /agent-provider all carry the team in the PATH, and a slug there is a 404.
 *
 * A function for the same reason activeTeamSlug is one: the active team changes
 * under every caller, and a value read once at boot is the wrong team by the
 * first switch.
 */
export function activeTeamId() {
  return state.activeTeamId || null;
}

/**
 * Who is signed in, as the server's user id.
 *
 * Exported for ONE caller: the team-key section, which decides whether to draw
 * a form by finding this person's row in /v1/teams/{id}/members and reading its
 * role — the same membership row the server checks on the write, so the control
 * is absent exactly when pressing it would 403.
 */
export function selfUserId() {
  return (state.me && state.me.id) || null;
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
  // Moderation is per-team and the answer arrives with this team's history. Held
  // false across the switch so a moment of the previous team's permission can
  // never draw a control in the new one.
  state.viewerCanModerate = false;
  messageMenu.close();
  renderer.clear();
  clearComposerError();
  const empty = el("chat-empty");
  if (empty) empty.hidden = true;

  // A null realtime handle means the vendored client did not load: history still
  // renders and sending still works, only live updates are missing.
  if (state.realtime) state.realtime.subscribeTeam(teamId);

  await webPlatform.storage.set({ [TEAM_STORAGE_KEY]: teamId });
  // Repaint the rail so the filled square follows the switch, and so the team
  // just left picks up a badge if anything lands in it.
  await teamRail.render();
  await refreshNameCache();
  await refreshAgentAliases();
  await loadInitialHistory();
  // A team switch lands at the bottom of a different thread. Without this the
  // jump control would survive the switch from a team somebody had scrolled up
  // in, offering to take them somewhere they already are.
  syncJumpLatest();

  // Advance this user's read cursor for the team they are now looking at.
  // Without it the rail's badges would only ever grow: the counts come from the
  // same server cursor, and a badge that never clears is a badge people learn
  // to ignore. Fire-and-forget — a read cursor is never worth a failed switch.
  api.markRead(teamId)
    .then(() => teamRail.refreshBadges())
    .catch(() => {});
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

/**
 * The active team's EFFECTIVE agent aliases, rebuilt into the client regex.
 *
 * A team may rename its agent, and the toggle has to write THAT name — a
 * hardcoded "@agent" would produce a message the server's detector reads as
 * ordinary chat, and the person who pressed the button would be told nothing.
 *
 * Fail-soft: on any error the previous list stands. The base alias is additive
 * server-side, so the default keeps working while a fetch is down.
 */
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
  // What the SERVER says this person may do to other people's messages here.
  // Read before the messages are drawn, so the first right-click after a switch
  // already has the right answer.
  state.viewerCanModerate = Boolean(
    data && data.viewer && data.viewer.can_moderate,
  );
  // The endpoint answers newest-first; the thread reads oldest-first.
  const messages = ((data && data.messages) || []).slice().reverse();
  if (messages.length === 0) {
    showEmptyThreadCopy();
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

// ---------- The keyboard, and where the thread has to be when it opens ------

let viewportWired = false;

/**
 * Point chat-core's viewport anchor at this surface's thread.
 *
 * The two halves are deliberately in different files: viewport.js measures and
 * announces, chat-core decides whether the reader's position means it may
 * re-anchor (createViewportAnchor carries that reasoning, and the test that
 * drives it). This function is the wire between them and nothing else.
 *
 * Wired once per session — the subscription is module-level and bootChat runs
 * again after a re-sign-in.
 */
function wireViewportAnchor() {
  if (viewportWired) return;
  viewportWired = true;
  onViewportChange(
    createViewportAnchor({
      getScrollEl: () => el("chat-scroll"),
      scrollToBottom: (opts) => renderer.scrollToBottom(opts),
    }),
  );
}

/**
 * Show the jump control exactly while the newest message is out of sight.
 *
 * The same `isNearBottom` the auto-scroll and the keyboard anchor ask, for the
 * reason all three must agree: a button offering to take somebody to the bottom
 * of a thread the app already considers itself at the bottom of is a button that
 * does nothing when pressed.
 *
 * Cheap, because it runs on every scroll event: three geometry reads and an
 * attribute written only when the answer actually changed. Assigning `hidden`
 * to the value it already holds is free in every engine, but the comparison
 * makes that a property of this code rather than of theirs.
 */
function syncJumpLatest() {
  const btn = el("btn-jump-latest");
  const scrollEl = el("chat-scroll");
  if (!btn || !scrollEl) return;
  const atBottom = isNearBottom(scrollEl);
  if (btn.hidden !== atBottom) btn.hidden = atBottom;
}

// ---------- Composer ----------

let composerWired = false;

function wireComposer() {
  // bootChat runs again after a re-sign-in; a second set of listeners would
  // send every message twice.
  if (composerWired) return;
  composerWired = true;

  const input = el("composer-input");
  const sendBtn = el("btn-send");
  const clipBtn = el("btn-clip");
  const picker = el("file-picker");
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

  const agentBtn = el("btn-agent");
  if (agentBtn) {
    agentBtn.addEventListener("click", () => setAgentArmed(!state.agentArmed));
  }

  // "+" opens the OS picker; the picker's change event does the upload. The
  // button never touches a file itself, so there is exactly one upload path.
  if (clipBtn && picker) {
    clipBtn.addEventListener("click", () => picker.click());
    picker.addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      // Cleared FIRST, so picking the same file twice in a row still fires.
      event.target.value = "";
      if (file) await uploadFile(file);
    });
  }

  // EVERY control in the pill, and for one reason: pressing any of them used to
  // take focus off the textarea, and on iOS a field that loses focus takes the
  // keyboard with it. Sending closed it; so did arming the agent, which is
  // pressed mid-sentence, before the message it applies to. The keyboard cannot
  // be reopened from script, so the only fix is to never let focus leave —
  // keepFocusOnPress cancels the press's focus move while leaving its click
  // alone. See packages/chat-core/dom.js for what that rests on.
  for (const control of [sendBtn, agentBtn, clipBtn]) keepFocusOnPress(control);

  // Jump to the newest message. Guarded like the pill's controls: somebody who
  // scrolled up mid-sentence and comes back must find their draft AND their
  // keyboard, not just the draft.
  const jumpBtn = el("btn-jump-latest");
  if (jumpBtn) {
    keepFocusOnPress(jumpBtn);
    jumpBtn.addEventListener("click", () => {
      renderer.scrollToBottom({ force: true });
      // Hidden now rather than on the scroll event the jump will produce: the
      // press already said where they want to be, and a control that lingers
      // for a frame after doing its job reads as one that did not work.
      jumpBtn.hidden = true;
    });
  }

  if (scrollEl) {
    scrollEl.addEventListener("scroll", () => {
      if (scrollEl.scrollTop < 80) loadOlderPage();
      syncJumpLatest();
    });
  }
}

/**
 * Grow the textarea with its content, up to the ceiling CSS declares.
 *
 * The height is reset to auto first so scrollHeight reports the true content
 * size rather than the box's current one, and the scrollbar is only allowed to
 * appear once the ceiling is actually reached — at rest the pill is one clean
 * line with the "+" and the "›".
 */
function autoResize(input) {
  input.style.height = "auto";
  const view = document.defaultView;
  const maxHeight =
    (view && parseFloat(view.getComputedStyle(input).maxHeight)) || 200;
  input.style.height = `${Math.min(input.scrollHeight, maxHeight)}px`;
  if (input.scrollHeight > maxHeight) input.classList.add("is-overflowing");
  else input.classList.remove("is-overflowing");
}

/**
 * Upload a file into the active team, then post it as a chat message.
 *
 * The transport is chat-core's api.uploadMedia — the same call the extension
 * makes — so there is one multipart request in the product, not two. The size
 * check is client-side courtesy only; the server holds the real ceiling.
 */
async function uploadFile(file) {
  const team = state.teams.find((t) => t.id === state.activeTeamId);
  if (!team) return;

  if (file.size > MAX_MEDIA_BYTES) {
    showUploadError(`File too large (max 25 MB): ${file.name}`);
    return;
  }

  const clipBtn = el("btn-clip");
  if (clipBtn) clipBtn.disabled = true;
  try {
    const item = await api.uploadMedia(team.slug, file, "pwa");
    const sent = await api.postMessage(state.activeTeamId, {
      content: file.name,
      media: {
        item_id: item.item_id,
        mime: item.mime,
        size: item.size,
        filename: file.name,
      },
    });
    // Optimistic render, same as a text send: the websocket echo carries the
    // same id and renderMessage de-dupes it.
    if (sent && sent.id) {
      renderer.renderMessage(sent, { prepend: false });
      renderer.syncDaySeparators();
      renderer.scrollToBottom();
      const empty = el("chat-empty");
      if (empty) empty.hidden = true;
    }
  } catch (e) {
    showUploadError(`Upload failed: ${e.message}`);
  } finally {
    if (clipBtn) clipBtn.disabled = false;
  }
}

/** A transient line above the pill. Same place a failed send reports. */
function showUploadError(message) {
  const composer = el("composer");
  if (!composer) return;
  const node = document.createElement("p");
  node.className = "xb-upload-error";
  node.textContent = message;
  composer.insertBefore(node, composer.firstChild);
  window.setTimeout(() => node.remove(), 4000);
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
  const btn = el("btn-agent");
  if (!btn) return;
  btn.dataset.state = state.agentArmed ? "on" : "off";
  btn.setAttribute("aria-pressed", state.agentArmed ? "true" : "false");
}

async function sendMessage() {
  const input = el("composer-input");
  const sendBtn = el("btn-send");
  if (!input || !state.activeTeamId) return;
  const typed = input.value.trim();
  if (!typed) return;

  // ONE summon mechanism: the toggle writes the mention a person would type, and
  // the server's detector decides from the text either way. withAgentMention
  // leaves an already-mentioned draft alone, so toggling AND typing "@agent"
  // summons once — as it would anyway, since the server acts on the first
  // mention only, but "@agent @agent ..." is a message nobody wrote.
  const content = state.agentArmed
    ? withAgentMention(typed, { aliases: state.agentAliases, regex: state.mentionRe })
    : typed;

  // Wake the bridge, if there is one in THIS browser, before the server routes
  // the turn. Not awaited and not checked: a nudge that fails must not delay or
  // block a message, and on a phone there is nothing to nudge and never will be.
  ensureBridge();

  if (sendBtn) sendBtn.disabled = true;
  try {
    const sent = await api.postMessage(state.activeTeamId, { content });
    // Cleared only AFTER the server took it: clearing first would throw away
    // what they wrote whenever the send fails.
    input.value = "";
    autoResize(input);
    clearComposerError();
    // Disarmed once the message is away — and only on success, so a failed send
    // leaves the draft AND its intent intact. This is a shared team chat: an
    // armed toggle nobody noticed would send the next line meant for teammates
    // to the agent as well, in front of everyone.
    setAgentArmed(false);
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
    // An agent turn is the moment the routing matters, so this is the poll
    // worth piggybacking on rather than tightening the interval for everyone.
    // Not awaited: the message is already away, and a status must never delay
    // it. Fires after the send so a bridge that died is noticed on the turn it
    // affected, rather than up to a minute later.
    refreshAgentRoute();
  } catch (e) {
    showComposerError(`Message not sent: ${e.message}`);
  } finally {
    // Re-enabled before focus is touched: disabling an element that holds focus
    // moves focus to the body, and this one has been disabled since the send
    // began. It never held focus — keepFocusOnPress saw to that — but the order
    // costs nothing and the failure it prevents is invisible.
    if (sendBtn) sendBtn.disabled = false;
    // The DESKTOP path, and only that. A click there does move focus, and this
    // puts it back. On iOS it is a no-op wearing a useful name: a .focus() after
    // an await is outside the user gesture, so the caret returns and the
    // keyboard does not — which is why the keyboard is kept rather than
    // recovered.
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
 * @param {{onSignedOut?: () => void, onOpenTeamKeys?: () => void}} [refs]
 *   onSignedOut — shown when the stored token turns out to be dead. The sign-in
 *   card belongs to app.js, so this surface reports the fact instead of owning
 *   the markup for it.
 *   onOpenTeamKeys — where the bridge notice's control leads. The settings
 *   sheet belongs to app.js too; this file knows only that the notice needs
 *   somewhere to send people, which is the whole reason the notice was a dead
 *   end before.
 */
export async function bootChat(refs = {}) {
  const { onSignedOut } = refs;
  // Remembered so the first-team-ever path can re-run this boot: creating a
  // team when the session came up with no socket needs the whole sequence, and
  // the sign-out hook belongs to app.js, not to this file.
  lastBootRefs = refs;

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

  // 4. The rail, defaulting to the team they read last. Painted before the
  //    socket so the header is never empty while a network call is in flight.
  state.activeTeamId = (await preferredTeamId()) || state.teams[0].id;
  revealChat();
  await teamRail.render();
  // There is a team now, so the controls that act on one may appear.
  panels.reveal();
  // The per-message overlay listens on the list, not on the rows, so it can be
  // attached once here and never touched again — a message that arrives over the
  // websocket is reachable on exactly the same terms as one from the history.
  messageMenu.attach();

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
      //
      // The banner is driven by the connection's STATE and never by an error.
      // It used to be wired to `error`, and Centrifuge emits that for transient
      // things on a socket that is fine — so one hiccup pinned "Reconnecting..."
      // on screen for the rest of the session while everything worked.
      onConnected: () => connectionStatus.connected(),
      onConnecting: () => connectionStatus.connecting(),
      onDisconnected: () => connectionStatus.disconnected(),
      onError: (e) => console.warn("[xbrain] realtime incident:", e),
    });
  } catch (e) {
    console.warn("[xbrain] realtime unavailable:", e);
    state.realtime = null;
  }
  if (!state.realtime) {
    connectionStatus.offline();
  }

  // 6. Claim the channel and load the thread.
  wireComposer();
  wireViewportAnchor();
  // Idempotent by construction: the same four listener functions are added to
  // the same element, and addEventListener de-duplicates an identical triple —
  // so a re-boot after a re-sign-in cannot end up switching two teams per swipe.
  swipeTeams.attach();
  await switchTeam(state.activeTeamId);

  // 7. What the agent would run on. Asked once now, then only while somebody is
  //    looking at the page.
  //
  //    The nudge goes first and is not awaited: if the extension lives in this
  //    browser, waking it before the status call means the answer describes the
  //    bridge as it is about to be rather than as it was. On a phone this is a
  //    resolved promise and nothing else — no extension, no error, no noise.
  ensureBridge();
  refreshAgentRoute();
  startRoutePolling();

  // 8. Drop the caret in the composer so they can just type.
  focusComposer();
}

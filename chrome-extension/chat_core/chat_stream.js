/**
 * Pure helpers for the team chat streaming + rendering layer.
 *
 * Quick task 260512-tcr Wave 3.3. Kept dependency-free (no DOM, no browser
 * extension APIs, no Centrifuge) so node tests can import and exercise them
 * directly.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_stream.mjs
 *
 * Concepts:
 *   - StreamBuffer: accumulates `agent_stream_chunk` deltas keyed by
 *     message_id. Returns the running text at any time. Caller flushes
 *     when `agent_stream_end` arrives.
 *   - mention regex BUILT FROM the server's effective alias list
 *     (GET /v1/teams/{id}/agent-aliases) via buildMentionRegex() — one source
 *     of truth with memory-api, never a hardcoded vocabulary. The composer no
 *     longer narrates a pending mention, so its only client-side use is the
 *     agent toggle's de-dupe: a draft that already names the agent must not have
 *     a second mention prepended to it.
 *   - formatRelative(iso) — "12s ago" / "5m ago" / "Mar 5" strings for
 *     message timestamps in bubble headers.
 *   - hostnameFromUrl, hashSourceId — small helpers reused by the clip
 *     overlay path.
 */

// ---------- Mention regex (built from the server's effective alias list) ----------
//
// ONE source of truth: the client does NOT hardcode a mention vocabulary.
// The surface fetches GET /v1/teams/{id}/agent-aliases and builds the regex from
// that list via buildMentionRegex() — JS-escaping each alias (mirror of the
// server's re.escape) and sorting longest-first — so the client and server can
// never diverge again. The server still makes the final summon decision; the
// client's copy exists so the agent toggle can tell an already-mentioned draft
// from a bare one and avoid summoning twice.

// JS-escape one alias, mirroring Python re.escape for the metacharacters that
// matter inside a JS regex. Defense in depth: the server already restricts
// stored aliases to [A-Za-z0-9_-], so a metachar should never reach here — but
// we escape anyway so a hostile or garbled list can never inject regex
// behaviour (e.g. a stray ".*" matches only the literal "@.*").
function escapeAlias(a) {
  return a.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&");
}

// Build the SAME boundary pattern the server uses (mention_detector
// _build_mention_regex): a leading @, a word/@ boundary before it, a
// longest-first alternation of the escaped aliases, and a
// punctuation/whitespace/end boundary after. "claude" is filtered out
// (reserved — never a client trigger). An empty list falls back to ["agent"].
export function buildMentionRegex(aliases) {
  const cleaned = (Array.isArray(aliases) ? aliases : [])
    .filter((a) => typeof a === "string" && a.trim().length > 0)
    .map((a) => a.trim())
    .filter((a) => a.toLowerCase() !== "claude");
  // De-dupe case-insensitively, keeping the first-seen spelling.
  const seen = new Set();
  const uniq = [];
  for (const a of cleaned) {
    const key = a.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      uniq.push(a);
    }
  }
  const effective = uniq.length ? uniq : ["agent"];
  // Longest-first so a longer alias wins over a shorter overlapping one
  // (mirror of the server's aliases.sort(key=len, reverse=True)).
  const ordered = [...effective].sort((x, y) => y.length - x.length);
  const joined = ordered.map(escapeAlias).join("|");
  return new RegExp(
    "(?:^|(?<=[^\\w@]))@(" + joined + ")(?=$|[\\s.,!?;:()\\[\\]{}'\"])",
    "i",
  );
}

// Detect an agent mention for the optimistic composer hint. `aliasesOrRegex`
// may be a prebuilt RegExp (from buildMentionRegex) or an array of aliases
// (built on the fly). If omitted, it defaults to buildMentionRegex(["agent"]) —
// never the stale vocabulary.
export function detectMentionClient(text, aliasesOrRegex) {
  if (!text) return null;
  const re =
    aliasesOrRegex instanceof RegExp
      ? aliasesOrRegex
      : buildMentionRegex(aliasesOrRegex || ["agent"]);
  const m = text.match(re);
  if (!m) return null;
  return { agent_name: "claude-sonnet-4-6", trigger: m[1].toLowerCase() };
}

// ---------- Agent failure ----------
//
// WHY THE WORDS LIVE HERE AND NOT IN THE FRAME.
//
// A failure frame is precisely the place an upstream bug dumps raw text: it
// carries whatever went wrong, and what went wrong is written by a provider, for
// the person holding the account. One shipped into a team chat naming the vendor,
// the account's credit balance and a request id.
//
// The server has been fixed, and it is the wrong place to rely on. So the client
// renders from `code` — a closed vocabulary of OUR own — and never from a text
// field the frame carries. That is a structural guarantee rather than a filter:
// there is no input to this function that can produce words it does not already
// contain, whatever an older or newer server sends.
//
// The server keeps its own copy of these sentences for the PERSISTED row, which
// is stored text that recall and non-xbrain consumers read. It is not a second
// display path: a bubble whose stored content is that sentence renders the
// failure line below instead of the content, so a reader never sees both.

/** Failure code -> what a person is told. The only vocabulary the UI can print. */
export const AGENT_FAILURE_TEXT = {
  timeout:
    "The agent took too long to answer and the attempt was stopped. Worth trying again.",
  unavailable: "The agent could not answer just now. Worth trying again.",
  configuration:
    "The agent could not answer. Trying again will not help — this needs an " +
    "administrator to look at the server.",
  // ---- Unavailability, not failure ----
  //
  // These two describe an ABSENCE. Nothing was attempted, so nothing went
  // wrong, and a team whose agent has simply not been configured must not be
  // shown a malfunction — that is how a product that works reads as broken.
  //
  // Neither sentence mentions this device. The bridge is keyed by user, not by
  // device: a phone with no extension answers perfectly whenever that person
  // has a browser open somewhere. "No live bridge for this user anywhere" is
  // the only condition worth naming, and it is the same sentence everywhere.
  no_route:
    "The agent has no model to answer with. It runs on a Claude subscription " +
    "through the xbrain extension — open the browser where that extension is " +
    "signed in, or set a team API key, which is billed to the team.",
  subscription_lost:
    "The Claude subscription is no longer connected. Open the browser where " +
    "the xbrain extension is signed in, then send this again.",
  // A real failure, not an absence: an attempt was made and refused. It gets
  // its own sentence because whose key failed is the useful part — somebody who
  // pasted a key into team settings needs to know it was theirs, not the
  // product. Still none of the provider's own words.
  team_key_rejected:
    "The team's own API key was refused. It needs to be replaced in team " +
    "settings — trying again with the same key will not help.",
};

/**
 * Codes that mean "not available", as opposed to "tried and failed".
 *
 * The distinction is rendered, not just worded: a failure line and an
 * unavailability line get different classes, so a state nobody can fix by
 * retrying does not carry the visual weight of a malfunction.
 */
export const AGENT_UNAVAILABLE_CODES = new Set(["no_route", "subscription_lost"]);

/**
 * Is this outcome an absence rather than a failure?
 *
 * Total, like agentFailureText: any input at all resolves to a boolean, and an
 * unknown code is treated as a failure — the conservative direction, since
 * calling a real malfunction "unavailable" would understate it.
 *
 * @param {{code?: string}|null|undefined} info
 * @returns {boolean}
 */
export function isAgentUnavailable(info) {
  const code = info && typeof info.code === "string" ? info.code : "";
  return AGENT_UNAVAILABLE_CODES.has(code);
}

/** What every other code resolves to. Vague on purpose — see above. */
export const AGENT_FAILURE_FALLBACK = "The agent could not answer.";

/**
 * The line to render for a failed agent turn.
 *
 * Total, and closed: any input at all — an old frame, a new code, a hostile
 * payload, nothing — resolves to one of the strings declared above.
 *
 * @param {{code?: string}|null|undefined} info a stream_error frame, or
 *   `metadata.agent_failure` off a persisted row. Both carry `code`.
 * @returns {string}
 */
export function agentFailureText(info) {
  const code = info && typeof info.code === "string" ? info.code : "";
  return Object.prototype.hasOwnProperty.call(AGENT_FAILURE_TEXT, code)
    ? AGENT_FAILURE_TEXT[code]
    : AGENT_FAILURE_FALLBACK;
}

// ---------- Agent toggle ----------
//
// The composer's agent button is not a second way to summon the agent. It writes
// the SAME mention a person would type, and the server's detector — the only
// thing that decides — sees one kind of message either way. A parallel "this one
// is for the agent" flag on the request would be a second authority that can
// disagree with the first, and the two would drift on the first schema change.

/**
 * Which alias to write. The server's effective list, in the server's order.
 *
 * "claude" is skipped for the same reason `buildMentionRegex` drops it: it is
 * reserved and never a client trigger, so writing "@claude" would produce a
 * message that looks summoned and is not. Falls back to "agent" when the list
 * offers nothing usable — never a hardcoded vocabulary in the ordinary path.
 *
 * @param {string[]|null|undefined} aliases
 * @returns {string}
 */
export function agentMentionAlias(aliases) {
  for (const alias of Array.isArray(aliases) ? aliases : []) {
    if (typeof alias !== "string") continue;
    const trimmed = alias.trim();
    if (!trimmed || trimmed.toLowerCase() === "claude") continue;
    return trimmed;
  }
  return "agent";
}

/**
 * The text to send when the agent toggle is on.
 *
 * Returns the draft UNCHANGED when it already carries a live mention. The server
 * acts on the first mention only, so a doubled one would still summon once — but
 * "@agent @agent what is this" is a message nobody wrote, and the person who
 * typed the mention and then pressed the button would see the product arguing
 * with itself.
 *
 * Pure: no DOM, no state, no clock.
 *
 * @param {string} text the draft as typed
 * @param {{aliases?: string[], regex?: RegExp}} [opts]
 *   regex   — the surface's compiled alias regex (preferred: it is the server's)
 *   aliases — the raw effective list, used to pick the alias to write
 * @returns {string}
 */
export function withAgentMention(text, { aliases, regex } = {}) {
  const body = typeof text === "string" ? text : "";
  if (detectMentionClient(body, regex || aliases)) return body;
  const mention = `@${agentMentionAlias(aliases)}`;
  const rest = body.trimStart();
  return rest ? `${mention} ${rest}` : mention;
}

// ---------- StreamBuffer ----------
//
// Lifecycle per agent message:
//   buf.start(messageId)
//   buf.append(messageId, delta)        × N
//   buf.finalize(messageId, finalText?) → buf.get(messageId) returns text
//
// Buffers persist across the surface's lifetime so a chunk that races
// against the start frame doesn't get dropped.

export class StreamBuffer {
  constructor() {
    /** @type {Map<string, {parts: string[], done: boolean}>} */
    this._b = new Map();
  }
  start(messageId) {
    if (!this._b.has(messageId)) {
      this._b.set(messageId, { parts: [], done: false });
    }
  }
  append(messageId, delta) {
    if (!delta) return;
    let entry = this._b.get(messageId);
    if (!entry) {
      // Race: chunk arrived before start. Implicitly create.
      entry = { parts: [], done: false };
      this._b.set(messageId, entry);
    }
    entry.parts.push(delta);
  }
  finalize(messageId, finalText) {
    const entry = this._b.get(messageId);
    if (!entry) {
      this._b.set(messageId, {
        parts: finalText ? [finalText] : [],
        done: true,
      });
      return;
    }
    if (finalText) entry.parts = [finalText];
    entry.done = true;
  }
  get(messageId) {
    const entry = this._b.get(messageId);
    if (!entry) return "";
    return entry.parts.join("");
  }
  isDone(messageId) {
    const entry = this._b.get(messageId);
    return entry ? entry.done : false;
  }
  drop(messageId) {
    this._b.delete(messageId);
  }
}

// ---------- formatRelative ----------

export function formatRelative(isoStamp, now = Date.now()) {
  if (!isoStamp) return "—";
  const then = new Date(isoStamp).getTime();
  if (isNaN(then)) return "—";
  const diffSec = Math.max(0, (now - then) / 1000);
  if (diffSec < 60) return `${Math.floor(diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  // Older — show locale date "Mar 5"
  try {
    return new Date(then).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return new Date(then).toISOString().slice(0, 10);
  }
}

// ---------- hostnameFromUrl ----------

export function hostnameFromUrl(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "unknown";
  }
}

// ---------- Author display name lookup ----------

/**
 * The name to print above someone's message.
 *
 * Precedence, and why it is that way round:
 *
 *   1. `msg.author_label` — the label the SERVER resolved for this message.
 *      This is the only source that is correct unconditionally. It arrives with
 *      the message itself, so it is already there on first paint, and it still
 *      names an author who has since left the team.
 *   2. `nameCache[author_user_id]` — the roster lookup the surface builds from
 *      GET /v1/teams/{id}/members. It is a FALLBACK, not the source of truth:
 *      it is populated after an await, so a thread rendered before that call
 *      returned would label every message "Teammate" and never revisit it, and
 *      a former member is missing from it permanently. It stays because an
 *      older cached message carries no label, and because a client may be
 *      talking to an API that has not been redeployed yet.
 *   3. "Teammate" — genuinely last resort. With a current server it should be
 *      unreachable for any real account.
 *
 * Pure: same inputs, same output, no lookups of its own.
 *
 * @param {{msg: Object, selfUserId?: string, nameCache?: Object}} args
 * @returns {string}
 */
export function authorLabel({ msg, selfUserId, nameCache }) {
  if (msg.kind === "agent") {
    return `🤖 ${msg.agent_name || "agent"}`;
  }
  if (msg.author_user_id === selfUserId) return "You";
  const carried = typeof msg.author_label === "string" ? msg.author_label.trim() : "";
  if (carried) return carried;
  const name = nameCache && nameCache[msg.author_user_id];
  return name || "Teammate";
}

// ---------- Bubble role classifier (CSS class assignment) ----------

export function bubbleClass(msg, selfUserId) {
  if (msg.kind === "agent") return "is-agent";
  if (msg.author_user_id === selfUserId) return "is-self";
  return "is-user";
}

// ---------- Provenance label (Claude Pro/Max vs API) ----------

export function provenanceLabel(routed_via) {
  if (routed_via === "user_promax") return { text: "via Pro/Max", cls: "via-promax" };
  // Two different people get the bill for these two, so they are two different
  // badges. `team_api` predates the distinction and still means the
  // deployment-wide key, which is why the new tier got a new name rather than
  // reusing it — rows already on disk would otherwise change meaning.
  if (routed_via === "team_key") return { text: "via team key", cls: "via-api" };
  if (routed_via === "team_api") return { text: "via team API", cls: "via-api" };
  return null;
}

// ---------- Which model is answering, and who pays ----------
//
// "Your subscription is answering" and "an API key is being billed" are very
// different facts to the person paying, and nothing distinguished them.
//
// The route reported here is the one the SERVER resolved — never inferred from
// whether an extension answered a ping. A client that decides this for itself
// eventually disagrees with the thing that actually routes the message, and
// then the status is wrong in the one direction that destroys trust.

/** route -> the quiet line a person sees. Null means: say nothing. */
export const AGENT_ROUTE_STATUS = {
  user_promax: "Running on your Claude subscription",
  team_key: "Running on the team's API key",
  team_api: "Running on the platform API key",
  // Not a status line. This one is the agent's own failure vocabulary's job —
  // saying it twice, in two places, would let the two disagree.
  unavailable: null,
};

/**
 * The status line for a route, or null when there is nothing worth saying.
 *
 * @param {{route?: string}|null|undefined} status a /v1/me/agent-route body
 * @returns {string|null}
 */
export function agentRouteStatusText(status) {
  const route = status && typeof status.route === "string" ? status.route : "";
  return Object.prototype.hasOwnProperty.call(AGENT_ROUTE_STATUS, route)
    ? AGENT_ROUTE_STATUS[route]
    : null;
}

/**
 * What a person is told when the bridge goes away underneath them.
 *
 * Honest about both remedies AND their cost, and it does not present the key as
 * the default fix: reopening the browser costs nothing and uses the
 * subscription they already pay for; a key is what you reach for when no
 * browser can be open.
 *
 * "a team admin can set" rather than "or set": the PUT is admin-only, and the
 * person most likely to read this is on a phone with no way to check which they
 * are. Naming the role means the sentence is true for both, and the member who
 * cannot act at least learns who can.
 *
 * Not about this device, for the same reason nothing else is: the bridge is
 * keyed by user. A phone is connected whenever that person has a browser
 * holding the socket anywhere.
 */
export const SUBSCRIPTION_LOST_NOTICE =
  "Your Claude subscription is no longer connected. Reopen the browser where " +
  "the xbrain extension is signed in to keep using it, or a team admin can set " +
  "a team API key, which the team is billed for.";

/**
 * The label on the control that takes somebody from that notice to the place
 * the key is actually set.
 *
 * A DESTINATION, NOT AN ACT. "Set a team API key" would promise a capability
 * the reader may not have — the write is admin-only — and a button that reads
 * as an action and then shows a sentence about asking somebody else is a
 * button that lied. This one names where it goes; the section it opens is where
 * an admin finds a form and a member finds the ask-an-admin line.
 *
 * The notice used to name the remedy with no route to it at all, which on a
 * standalone PWA — no address bar, no way to reach the desktop admin page — was
 * advice that could not be followed.
 */
export const SUBSCRIPTION_NOTICE_ACTION = "Team API key";

/**
 * Notice the moment a bridge that WAS live stops being live.
 *
 * A transition, never a state. Somebody who has never had a bridge — a
 * colleague with no extension at all — is losing nothing and must not be
 * nagged about it, so nothing fires until a live one has been seen.
 *
 * Dismissal sticks until the bridge genuinely comes back and goes again. A
 * warning that reappears the moment it is dismissed is a warning people learn
 * to ignore, and then it is worth less than nothing.
 *
 * Pure: no DOM, no clock, no network. The surface polls and hands observations
 * in.
 *
 * @returns {{observe: Function, dismiss: Function, isShowing: Function,
 *            hasEverConnected: Function}}
 */
export function createSubscriptionWatcher() {
  let everConnected = false;
  let dismissed = false;
  let showing = false;

  return {
    /**
     * Feed one observation.
     * @param {{subscription_connected?: boolean}|null} status
     * @returns {boolean} whether the notice should be on screen now
     */
    observe(status) {
      // An unreadable observation changes nothing. A failed poll is not
      // evidence the bridge died, and treating it as such would fire the
      // notice every time the network hiccuped.
      if (!status || typeof status.subscription_connected !== "boolean") {
        return showing;
      }
      if (status.subscription_connected) {
        everConnected = true;
        // Re-armed: the next genuine loss is worth mentioning again.
        dismissed = false;
        showing = false;
        return showing;
      }
      if (everConnected && !dismissed) showing = true;
      return showing;
    },
    dismiss() {
      dismissed = true;
      showing = false;
    },
    isShowing() {
      return showing;
    },
    hasEverConnected() {
      return everConnected;
    },
  };
}

// ---------- Brain-aware labels (Plan 20-03) ----------
//
// HARD RULE: these read ONLY fields the backend already sends. They never
// invent provenance. If the server said nothing, they return null and the UI
// renders nothing — an absent badge is correct, a fake badge is a spoof.

/**
 * Summary line for the agent's `<details>` sources disclosure.
 *
 * Source of truth: `metadata.memory_items` — the real count of brain items the
 * agent pipeline retrieved for this reply (team_chat_agent.py bundle
 * item_count). Returns null when nothing was retrieved so we don't render an
 * empty disclosure.
 *
 * @param {{memory_items?: number}|null|undefined} agentMsgMeta
 * @returns {string|null}
 */
export function brainSummaryLabel(agentMsgMeta) {
  if (!agentMsgMeta) return null;
  const n = agentMsgMeta.memory_items;
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return null;
  return `${n} source${n === 1 ? "" : "s"} from the brain`;
}

/**
 * The attachment behind a message, when one genuinely landed in the brain.
 *
 * Source of truth: `metadata.media` — the backend only writes it once the
 * attachment has been ingested as a memory item, so its presence IS the indexed
 * signal. Plain text messages return null and the UI renders no marker at all;
 * the backend emits no per-message saved flag and we will not invent one.
 *
 * It answers with the ITEM, not with a sentence, because what the reader wants
 * is the text that was indexed — and that lives behind
 * `GET /v1/media/{item_id}/indexed-text`, keyed by exactly this id. A label
 * describing the mechanism ("saved to brain · image indexed") told them only
 * that something happened.
 *
 * @param {{metadata?: {media?: {mime?: string, item_id?: string}}}|null|undefined} msg
 * @returns {{itemId: string, kind: string}|null}
 */
export function indexedAttachment(msg) {
  const media = msg && msg.metadata && msg.metadata.media;
  if (!media || !media.item_id) return null;
  const isImage = typeof media.mime === "string" && media.mime.startsWith("image/");
  return { itemId: media.item_id, kind: isImage ? "image" : "document" };
}

/**
 * The line to show for one `/indexed-text` answer.
 *
 * Every branch returns a NON-EMPTY string. An attachment whose text is still
 * being written, one that was deliberately skipped, and one whose indexing broke
 * are three different facts, and an empty tooltip renders all three — plus a
 * request that never came back — as the same blank box.
 *
 * `payload` null means the request itself did not produce an answer. That is
 * reported as its own thing rather than folded into "not indexed", which would
 * claim the server said something it never said.
 *
 * Pure: no DOM, no fetch, no clock.
 *
 * @param {{state?: string, text?: string, detail?: string}|null|undefined} payload
 * @returns {string}
 */
export function indexedTooltipText(payload) {
  if (!payload || typeof payload !== "object") {
    return "The indexed text could not be loaded.";
  }
  const detail = typeof payload.detail === "string" ? payload.detail.trim() : "";
  const text = typeof payload.text === "string" ? payload.text.trim() : "";

  if (payload.state === "indexed" && text) {
    return detail ? `${text}\n\n${detail}` : text;
  }
  if (payload.state === "pending") return detail || "Indexing…";
  if (payload.state === "failed") return detail || "Indexing failed.";
  // not_indexed, an unrecognised state, or "indexed" that carried no text after
  // all — in every one of those the honest thing to say is the same.
  return detail || "Not indexed.";
}

/**
 * True when two ISO timestamps fall on the same UTC calendar day.
 *
 * UTC (not local) so the result is deterministic regardless of the machine's
 * timezone; the day-separator label is rendered from UTC too, so the grouping
 * and the label always agree. Invalid/missing input returns false — the caller
 * then emits a separator, which is the safe, non-throwing default.
 *
 * @param {string} isoA
 * @param {string} isoB
 * @returns {boolean}
 */
export function sameDay(isoA, isoB) {
  if (!isoA || !isoB) return false;
  const a = new Date(isoA);
  const b = new Date(isoB);
  if (isNaN(a.getTime()) || isNaN(b.getTime())) return false;
  return (
    a.getUTCFullYear() === b.getUTCFullYear() &&
    a.getUTCMonth() === b.getUTCMonth() &&
    a.getUTCDate() === b.getUTCDate()
  );
}

/**
 * Label for a day separator ("TODAY" / "JUL 18, 2026"), formatted in UTC to
 * stay consistent with sameDay's grouping.
 *
 * @param {string} iso
 * @param {number} [now]
 * @returns {string}
 */
export function dayLabel(iso, now = Date.now()) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  if (sameDay(iso, new Date(now).toISOString())) return "Today";
  try {
    return d.toLocaleDateString(undefined, {
      timeZone: "UTC",
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
}

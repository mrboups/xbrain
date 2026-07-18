/**
 * Pure helpers for the team chat streaming + rendering layer.
 *
 * Quick task 260512-tcr Wave 3.3. Kept dependency-free (no DOM, no chrome.*,
 * no Centrifuge) so node tests can import and exercise them directly.
 *
 * Concepts:
 *   - StreamBuffer: accumulates `agent_stream_chunk` deltas keyed by
 *     message_id. Returns the running text at any time. Caller flushes
 *     when `agent_stream_end` arrives.
 *   - mention regex (mirror of memory-api/app/services/mention_detector.py)
 *     so the composer can do client-side hints ("Will trigger Groove").
 *   - formatRelative(iso) — "12s ago" / "5m ago" / "Mar 5" strings for
 *     message timestamps in bubble headers.
 *   - hostnameFromUrl, hashSourceId — small helpers reused by the clip
 *     overlay path.
 */

// ---------- Mention regex ----------
// Server-authoritative — this is the SAME pattern as memory-api.
// Used only for UX hints; the server makes the final decision.
const MENTION_RE = /(?:^|(?<=[^\w@]))@(grooveos|groove|gr|g)(?=$|[\s.,!?;:()[\]{}'"])/i;

export function detectMentionClient(text) {
  if (!text) return null;
  const m = text.match(MENTION_RE);
  if (!m) return null;
  return { agent_name: "claude-sonnet-4-6", trigger: m[1].toLowerCase() };
}

// ---------- StreamBuffer ----------
//
// Lifecycle per agent message:
//   buf.start(messageId)
//   buf.append(messageId, delta)        × N
//   buf.finalize(messageId, finalText?) → buf.get(messageId) returns text
//
// Buffers persist across the popup's lifetime so a chunk that races
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
//
// Server only sends author_user_id; the popup keeps a name cache populated
// from team members + the caller's own profile.

export function authorLabel({ msg, selfUserId, nameCache }) {
  if (msg.kind === "agent") {
    return `🤖 ${msg.agent_name || "agent"}`;
  }
  if (msg.author_user_id === selfUserId) return "You";
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
  if (routed_via === "team_api") return { text: "via team API", cls: "via-api" };
  return null;
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
 * Badge text for a message whose attachment genuinely landed in the brain.
 *
 * Source of truth: `metadata.media` — the backend only writes it once the
 * attachment has been ingested as a memory item, so its presence IS the
 * indexed signal. Plain text messages get no badge (the backend emits no
 * per-message saved flag, and we will not invent one).
 *
 * @param {{metadata?: {media?: {mime?: string}}}|null|undefined} msg
 * @returns {string|null}
 */
export function savedToBrainLabel(msg) {
  const media = msg && msg.metadata && msg.metadata.media;
  if (!media) return null;
  const isImage = typeof media.mime === "string" && media.mime.startsWith("image/");
  return `saved to brain · ${isImage ? "image" : "document"} indexed`;
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

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
const MENTION_RE = /(?:^|(?<=[^\w@]))@(groove|gr|g)(?=$|[\s.,!?;:()[\]{}'"])/i;

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

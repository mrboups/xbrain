/**
 * xbrain — claude.ai SSE translator + OpenAI ChatCompletion body translator
 *
 * Pure functions only (no DOM, no chrome.*). Importable from:
 *   1. Node tests (chrome-extension/tests/*.mjs) — plain ES module import.
 *   2. MV3 service worker — declared "type": "module" in manifest.json (plan 09-03).
 *   3. Fallback for importScripts-style MV3 — attaches to globalThis.xbrainTranslateSSE.
 *
 * Source of truth for the claude.ai contract:
 *   .planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md
 *
 * Bump CLAUDE_AI_API_VERSION on every observed format change.
 */

// Decision locked in 09-CAPTURE.md ## Decisions for plan 09-02.
// Bumped 2026-05-12 after live capture validated A1/A3/A10 and surfaced two diffs:
//   - A1 DIVERGED: completion endpoint is claude.ai/api/... NOT api.claude.ai/api/...
//     (patched in claude_ai_client.js COMPLETION_URL).
//   - A3 PARTIALLY DIVERGED: only Messages-style observed (no legacy `event: completion`),
//     and a new `event: message_limit` event appears — silently dropped by the
//     `if (!text && !finish) return null` guard below. Translator works as-is.
export const CLAUDE_AI_API_VERSION = "2026-05-12-capture-v2";

/**
 * Parse a single SSE "block" (everything between two "\n\n" separators).
 * Block format:
 *   :keepalive            ← SSE comment, ignored
 *   event: completion
 *   data: {"completion":"..."}
 *
 * Returns { event: string|null, data: object|string|null }.
 */
export function parseSSE(block) {
  const out = { event: null, data: null };
  if (!block) return out;
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // SSE comment (e.g., keepalive)
    if (line.startsWith("event:")) {
      out.event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      const raw = line.slice(5).trim();
      try {
        out.data = JSON.parse(raw);
      } catch {
        out.data = raw;
      }
    }
  }
  return out;
}

/**
 * Translate one parsed claude.ai SSE event into an OpenAI ChatCompletion
 * streaming chunk. Returns null when the event contributes no delta (e.g.,
 * message_start, content_block_start, keepalive comment).
 *
 * Handles BOTH (per 09-CAPTURE.md ## Decisions, A3):
 *   - Legacy: data: {"completion":"text", "stop_reason": null|"end_turn"}
 *   - Messages-style: data: {"type":"content_block_delta","delta":{"text":"..."}}
 *                     data: {"type":"message_stop"}
 *                     data: {"type":"message_delta","delta":{"stop_reason":"..."}}
 */
export function translateClaudeAiSSE(evt, id, model, created) {
  if (!evt || !evt.data) return null;

  let text = "";
  let finish = null;

  if (typeof evt.data === "object") {
    if ("completion" in evt.data && typeof evt.data.completion === "string") {
      // Legacy branch
      text = evt.data.completion;
      if (evt.data.stop_reason) finish = "stop";
    } else if (
      evt.data.type === "content_block_delta" &&
      evt.data.delta &&
      typeof evt.data.delta.text === "string"
    ) {
      // Messages-style text delta
      text = evt.data.delta.text;
    } else if (evt.data.type === "message_stop") {
      // End of message
      finish = "stop";
    } else if (
      evt.data.type === "message_delta" &&
      evt.data.delta &&
      evt.data.delta.stop_reason
    ) {
      // Messages-style end-of-message via message_delta
      finish = "stop";
    }
  }

  if (!text && !finish) return null;

  return {
    id,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [
      {
        index: 0,
        delta: text ? { content: text } : {},
        finish_reason: finish,
      },
    ],
  };
}

/**
 * Translate an OpenAI ChatCompletions request body to the claude.ai
 * internal "completion" endpoint payload shape.
 *
 * Body keys produced (sorted, per 09-CAPTURE.md ## Decisions):
 *   attachments, files, locale, model, parent_message_uuid,
 *   personalized_styles, prompt, rendering_mode, sync_sources,
 *   timezone, tools
 *
 * @param openaiBody    OpenAI ChatCompletion request (messages, model, stream, ...)
 * @param convUuid      conversation UUID — only used for URL building, not body
 * @param parentMsgUuid parent message UUID or null (defaults to nil UUID — A10)
 */
// Content can be a plain string OR an array of content blocks
// ({type:"text", text:"..."} — the Anthropic prompt-cache shape memory-api
// uses for the system message). Coercing an array with `"" + content` yields
// "[object Object]", so flatten block arrays to their joined text.
function flattenContent(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        typeof part === "string" ? part : (part && part.text) || ""
      )
      .filter(Boolean)
      .join("\n\n");
  }
  return "";
}

export function openaiToClaudeAi(openaiBody, convUuid, parentMsgUuid) {
  const messages = (openaiBody && openaiBody.messages) || [];
  const turns = messages.map((m) => {
    const text = flattenContent(m.content);
    if (m.role === "system") return "[System]\n" + text;
    if (m.role === "user") return "[Human]\n" + text;
    if (m.role === "assistant") return "[Assistant]\n" + text;
    return text;
  });

  const prompt = turns.join("\n\n");

  let tz = "UTC";
  try {
    if (typeof Intl !== "undefined" && Intl.DateTimeFormat) {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    }
  } catch {
    tz = "UTC";
  }

  return {
    prompt,
    parent_message_uuid:
      parentMsgUuid || "00000000-0000-4000-8000-000000000000",
    timezone: tz,
    personalized_styles: [],
    locale: "en-US",
    tools: [],
    attachments: [],
    files: [],
    sync_sources: [],
    rendering_mode: "messages",
    model: mapModel(openaiBody && openaiBody.model),
  };
}

/**
 * Map xbrain canonical model IDs to claude.ai web UI slugs.
 *
 * Unknown models fall back to claude-sonnet-4-6 (cheapest stable on Pro/Max).
 * If the live capture shows different slug strings, update this map and bump
 * CLAUDE_AI_API_VERSION.
 */
export function mapModel(openaiModel) {
  const map = {
    "claude-opus-4-7": "claude-opus-4-7",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
  };
  return map[openaiModel] || "claude-sonnet-4-6";
}

// MV3 SW fallback: when manifest declares background.service_worker without
// "type": "module", importScripts() loads this file synchronously and ES
// exports are not visible — attach to globalThis as a namespace instead.
if (typeof globalThis !== "undefined") {
  globalThis.xbrainTranslateSSE = {
    CLAUDE_AI_API_VERSION,
    parseSSE,
    translateClaudeAiSSE,
    openaiToClaudeAi,
    mapModel,
  };
}

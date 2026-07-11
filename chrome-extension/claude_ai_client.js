// === xbrain claude.ai client — single point of fragility ===
//
// CLAUDE_AI_API_VERSION is re-exported from translate_sse.js (currently
// "2026-05-capture"). Bump it there on every observed claude.ai format change.
//
// Source of truth for endpoint URLs, headers, body keys and SSE shape:
//   .planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md
//
// This module exposes:
//   - getOrgId()                          → string org UUID
//   - createConversation(orgId)           → string conv UUID
//   - handleClaude(msg, sendFrame)        → drives the full streaming response
//
// `sendFrame(envelope)` is injected by the caller (the service worker WS layer
// shipped in plan 09-03) so this module has NO chrome.* dependency — keeps it
// node-testable later if we ever want integration tests with a mock fetch.

import {
  CLAUDE_AI_API_VERSION,
  parseSSE,
  translateClaudeAiSSE,
  openaiToClaudeAi,
  mapModel,
} from "./translate_sse.js";

export { CLAUDE_AI_API_VERSION };

// --- Endpoint URL templates (locked in 09-CAPTURE.md ## Decisions) ---
const ORGS_URL = "https://claude.ai/api/organizations";
const CONV_CREATE_URL = (orgId) =>
  `https://claude.ai/api/organizations/${encodeURIComponent(orgId)}/chat_conversations`;
const COMPLETION_URL = (orgId, convUuid) =>
  `https://claude.ai/api/organizations/${encodeURIComponent(orgId)}/chat_conversations/${encodeURIComponent(convUuid)}/completion`;
const DELETE_URL = (orgId, convUuid) =>
  `https://claude.ai/api/organizations/${encodeURIComponent(orgId)}/chat_conversations/${encodeURIComponent(convUuid)}`;

// --- Static lint hint — observed-needed host_permissions for plan 09-03's manifest ---
// host_permissions:
//   "https://claude.ai/*"
// (api.claude.ai is NOT used — A1 DIVERGED 2026-05-12 capture: completion endpoint
// lives on claude.ai, not api.claude.ai. See 09-CAPTURE.md Divergence Patches.)

/**
 * GET /api/organizations on claude.ai. Returns the first org's UUID.
 * Field name is `uuid` per A8; falls back to `id` if shape changes.
 *
 * Throws Error on non-2xx response, empty array, or missing field.
 */
export async function getOrgId() {
  const r = await fetch(ORGS_URL, {
    method: "GET",
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });
  if (!r.ok) {
    throw new Error(`org_id fetch failed: ${r.status}`);
  }
  const orgs = await r.json();
  if (!Array.isArray(orgs) || orgs.length === 0) {
    throw new Error("no organizations returned");
  }
  const id = orgs[0].uuid || orgs[0].id;
  if (!id) {
    throw new Error("organizations[0] missing uuid/id field");
  }
  return id;
}

/**
 * POST /api/organizations/{orgId}/chat_conversations — creates a fresh
 * conversation and returns its UUID.
 *
 * Body shape from RESEARCH.md §Pattern 4 (also documented in 09-CAPTURE.md).
 */
export async function createConversation(orgId) {
  const body = {
    uuid:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : fallbackUuid(),
    name: "",
  };
  const r = await fetch(CONV_CREATE_URL(orgId), {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Origin: "https://claude.ai",
      Referer: "https://claude.ai/new",
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`conv create failed: ${r.status} ${text.slice(0, 200)}`);
  }
  const data = await r.json();
  if (!data || !data.uuid) {
    throw new Error("conversation create response missing uuid");
  }
  return data.uuid;
}

/**
 * DELETE /api/organizations/{orgId}/chat_conversations/{convUuid} — removes the
 * disposable conversation created for one bridged message. Best-effort: callers
 * MUST wrap in try/catch. Throws on network error or non-2xx.
 *
 * Endpoint is BEST-GUESS-PENDING-CAPTURE (09-CAPTURE.md A11): the standard REST
 * pairing of the create endpoint, expected 204 No Content.
 */
export async function deleteConversation(orgId, convUuid) {
  const r = await fetch(DELETE_URL(orgId, convUuid), {
    method: "DELETE",
    credentials: "include",
    headers: {
      Origin: "https://claude.ai",
      Referer: `https://claude.ai/chat/${convUuid}`,
      "anthropic-client-platform": "web_claude_ai",
    },
  });
  if (!r.ok) {
    throw new Error(`conv delete failed: ${r.status}`);
  }
}

/**
 * Drive a full streaming claude.ai completion in response to a bridge
 * chat_request envelope.
 *
 * @param msg        Bridge WS envelope: { request_id, openai_body }
 * @param sendFrame  function(envelope) injected by background.js WS layer.
 *                   Envelopes:
 *                     { request_id, type: "chunk", openai_chunk: {...} }
 *                     { request_id, type: "end" }
 *                     { request_id, type: "error", detail: {...} }
 */
export async function handleClaude(msg, sendFrame) {
  const { request_id, openai_body } = msg || {};
  if (!request_id || !openai_body) {
    try {
      sendFrame({
        request_id: request_id || null,
        type: "error",
        detail: { message: "missing request_id or openai_body" },
      });
    } catch {
      // sendFrame may itself blow up if WS is dead — nothing more to do.
    }
    return;
  }

  let orgId = null;
  let convUuid = null;

  try {
    orgId = await getOrgId();
    convUuid = await createConversation(orgId);
    const payload = openaiToClaudeAi(openai_body, convUuid, null);

    const r = await fetch(COMPLETION_URL(orgId, convUuid), {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream, text/event-stream",
        "Accept-Language": "en-US,en;q=0.5",
        Origin: "https://claude.ai",
        Referer: `https://claude.ai/chat/${convUuid}`,
        "anthropic-client-platform": "web_claude_ai",
        // anthropic-client-version: paste from live capture when 401/403 emerges
      },
      body: JSON.stringify(payload),
    });

    if (!r.ok) {
      // Surface as error frame. Body capped at 500 chars per threat model
      // T-09-02-01 (avoid logging full SSE).
      let bodyText = "";
      try {
        bodyText = await r.text();
      } catch {
        bodyText = "";
      }
      sendFrame({
        request_id,
        type: "error",
        detail: { status: r.status, body: bodyText.slice(0, 500) },
      });
      return;
    }

    if (!r.body || !r.body.getReader) {
      sendFrame({
        request_id,
        type: "error",
        detail: { message: "response has no readable body stream" },
      });
      return;
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const createdAt = Math.floor(Date.now() / 1000);
    const openaiMessageId = `chatcmpl-${request_id}`;
    const model = mapModel(openai_body.model);

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // claude.ai delimits SSE events with CRLF-CRLF (\r\n\r\n). Normalise to
      // LF so the \n\n block split + parseSSE work. Without this, indexOf("\n\n")
      // never matches (a \r sits between the two \n) → zero blocks → empty reply.
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (!block) continue;
        const evt = parseSSE(block);
        const chunk = translateClaudeAiSSE(
          evt,
          openaiMessageId,
          model,
          createdAt,
        );
        if (chunk) {
          sendFrame({ request_id, type: "chunk", openai_chunk: chunk });
        }
      }
    }

    // Flush any trailing block that didn't end with "\n\n".
    if (buffer.length > 0) {
      const evt = parseSSE(buffer);
      const chunk = translateClaudeAiSSE(
        evt,
        openaiMessageId,
        model,
        createdAt,
      );
      if (chunk) {
        sendFrame({ request_id, type: "chunk", openai_chunk: chunk });
      }
    }

    sendFrame({ request_id, type: "end" });
  } catch (e) {
    sendFrame({
      request_id,
      type: "error",
      detail: { message: String(e && e.message ? e.message : e) },
    });
  } finally {
    // --- Best-effort cleanup: delete the disposable claude.ai conversation ---
    // WHY this is safe: the bridge is STATELESS on claude.ai's side. Every
    // chat_request re-sends the FULL history flattened into `prompt` and always
    // sends parent_message_uuid = nil (see openaiToClaudeAi in translate_sse.js).
    // claude.ai's server-side conversation is never read back for context — it is
    // a throwaway container. Nothing references convUuid after the `end` frame, so
    // deleting it here cannot affect the user's response or any later turn.
    //
    // The user's chunks + end/error frame were ALREADY sent above, before this
    // finally — cleanup never delays or blocks the response. The empty catch makes
    // deletion best-effort: a failed DELETE never surfaces to the user or the
    // bridge (no error frame from cleanup). The `await` keeps the MV3 service
    // worker alive until the DELETE resolves.
    //
    // DEFERRED (separate future track, NOT this task): "native threading" — send
    // only the new user turn and chain parent_message_uuid to the last assistant
    // message so claude.ai holds context server-side. That removes the O(n^2)
    // full-history re-send but is INCOMPATIBLE with delete-per-message. See
    // .planning/quick/260711-45b-extension-session-bridge-auto-delete-cla/260711-45b-deferred-items.md
    if (orgId && convUuid) {
      try {
        await deleteConversation(orgId, convUuid);
      } catch {
        // best-effort — a failed cleanup must never surface to the user/bridge.
      }
    }
  }
}

// Cheap UUID fallback for environments where crypto.randomUUID isn't available
// (older Chrome SW contexts in theory; tests in node also expose crypto).
function fallbackUuid() {
  const hex = (n) => Math.floor(Math.random() * 16 ** n).toString(16).padStart(n, "0");
  return `${hex(8)}-${hex(4)}-4${hex(3)}-${hex(4)}-${hex(12)}`;
}

// MV3 SW fallback — if manifest doesn't declare "type": "module" yet and the
// background script uses importScripts(), expose the public API on globalThis.
if (typeof globalThis !== "undefined") {
  globalThis.xbrainClaudeAiClient = {
    CLAUDE_AI_API_VERSION,
    getOrgId,
    createConversation,
    deleteConversation,
    handleClaude,
  };
}

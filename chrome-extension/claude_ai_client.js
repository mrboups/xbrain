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

  try {
    const orgId = await getOrgId();
    const convUuid = await createConversation(orgId);
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
      buffer += decoder.decode(value, { stream: true });

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
    handleClaude,
  };
}

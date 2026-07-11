/**
 * Tests for chrome-extension/claude_ai_client.js (quick task 260711-45b).
 *
 * Verifies:
 *   (a) A successful stream deletes the created conversation exactly once,
 *       AFTER the `end` frame is sent (cleanup never delays the response).
 *   (b) A failing DELETE (network error or non-2xx) is swallowed by the
 *       `finally` block — handleClaude resolves cleanly, no `error` frame is
 *       emitted for the cleanup failure, and the user still got their
 *       chunks + end frame.
 *
 * `handleClaude` uses the GLOBAL fetch (not injected), so this test stubs
 * `globalThis.fetch` directly and restores the original afterwards so the
 * stub does not leak into other test files run via run_tests.mjs.
 *
 * Pure node test. Picked up by run_tests.mjs (file name starts with test_).
 */

import assert from "node:assert/strict";
import { handleClaude } from "../claude_ai_client.js";

// ---------- shared response helpers ----------

function makeJsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return body;
    },
    async text() {
      return typeof body === "string" ? body : JSON.stringify(body);
    },
  };
}

// Minimal SSE stream: one content_block_delta chunk, then message_stop.
const SSE_BODY = [
  'event: content_block_delta',
  'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
  "",
  "event: message_stop",
  'data: {"type":"message_stop"}',
  "",
  "",
].join("\n");

function makeCompletionResponse() {
  const encoded = new TextEncoder().encode(SSE_BODY);
  let served = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          async read() {
            if (served) return { done: true, value: undefined };
            served = true;
            return { done: false, value: encoded };
          },
        };
      },
    },
  };
}

// ---------- test runner ----------

let passed = 0;
let failed = 0;
async function test(name, body) {
  try {
    await body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

const originalFetch = globalThis.fetch;

function installFetchStub(deleteResponder) {
  const events = [];
  globalThis.fetch = async (url, init) => {
    const method = (init && init.method) || "GET";
    if (method === "GET" && url.endsWith("/organizations")) {
      return makeJsonResponse([{ uuid: "org-1" }], 200);
    }
    if (
      method === "POST" &&
      url.endsWith("/chat_conversations")
    ) {
      return makeJsonResponse({ uuid: "conv-1" }, 200);
    }
    if (method === "POST" && url.endsWith("/completion")) {
      return makeCompletionResponse();
    }
    if (method === "DELETE") {
      return deleteResponder(url, init, events);
    }
    throw new Error("unexpected fetch call: " + method + " " + url);
  };
  return events;
}

function makeSendFrame(events) {
  return (env) => {
    events.push({ kind: "frame", type: env.type });
  };
}

const envelope = {
  request_id: "r1",
  openai_body: {
    model: "claude-sonnet-4-6",
    messages: [{ role: "user", content: "hi" }],
  },
};

try {
  // ---------- test (a) ----------

  await test(
    "successful stream deletes the conversation exactly once, AFTER the end frame",
    async () => {
      const events = installFetchStub((url, init, evts) => {
        evts.push({ kind: "delete", url, method: init.method });
        return makeJsonResponse({}, 204);
      });
      const sendFrame = makeSendFrame(events);

      await handleClaude(envelope, sendFrame);

      const deletes = events.filter((e) => e.kind === "delete");
      assert.equal(deletes.length, 1, "expected exactly one delete event");
      assert.match(deletes[0].url, /conv-1/);
      assert.equal(deletes[0].method, "DELETE");

      const endIdx = events.findIndex(
        (e) => e.kind === "frame" && e.type === "end",
      );
      const deleteIdx = events.findIndex((e) => e.kind === "delete");
      assert.ok(endIdx !== -1, "expected an end frame");
      assert.ok(
        deleteIdx > endIdx,
        "delete must happen AFTER the end frame",
      );

      const chunkFrames = events.filter(
        (e) => e.kind === "frame" && e.type === "chunk",
      );
      assert.ok(chunkFrames.length > 0, "expected at least one chunk frame");
    },
  );

  // ---------- test (b) ----------

  await test(
    "a failing DELETE is swallowed — no error frame, user still served",
    async () => {
      const events = installFetchStub(() => {
        throw new Error("network down");
      });
      const sendFrame = makeSendFrame(events);

      await handleClaude(envelope, sendFrame);

      const endFrames = events.filter(
        (e) => e.kind === "frame" && e.type === "end",
      );
      assert.equal(endFrames.length, 1, "expected exactly one end frame");

      const chunkFrames = events.filter(
        (e) => e.kind === "frame" && e.type === "chunk",
      );
      assert.ok(chunkFrames.length > 0, "expected at least one chunk frame");

      const errorFrames = events.filter(
        (e) => e.kind === "frame" && e.type === "error",
      );
      assert.equal(
        errorFrames.length,
        0,
        "cleanup failure must never surface as an error frame",
      );
    },
  );

  // ---------- test (b2) — DELETE returns non-2xx instead of throwing ----------

  await test(
    "a non-2xx DELETE response is swallowed — no error frame, user still served",
    async () => {
      const events = installFetchStub(() => makeJsonResponse({}, 500));
      const sendFrame = makeSendFrame(events);

      await handleClaude(envelope, sendFrame);

      const endFrames = events.filter(
        (e) => e.kind === "frame" && e.type === "end",
      );
      assert.equal(endFrames.length, 1, "expected exactly one end frame");

      const errorFrames = events.filter(
        (e) => e.kind === "frame" && e.type === "error",
      );
      assert.equal(
        errorFrames.length,
        0,
        "a non-2xx delete must never surface as an error frame",
      );
    },
  );
} finally {
  globalThis.fetch = originalFetch;
}

// ---------- summary ----------

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

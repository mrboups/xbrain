// Plain-node ES module test — no test framework, just `node:assert/strict`.
// Run via `node chrome-extension/tests/run_tests.mjs` (or this file directly).

import assert from "node:assert/strict";
import {
  parseSSE,
  translateClaudeAiSSE,
  CLAUDE_AI_API_VERSION,
} from "../translate_sse.js";

const ID = "chatcmpl-test-1";
const MODEL = "claude-opus-4-7";
const CREATED = 1715472000;

// --- Test 1: legacy completion event with text ---
{
  const ev = parseSSE(
    'event: completion\ndata: {"completion":"Hello","stop_reason":null}',
  );
  assert.equal(ev.event, "completion");
  assert.equal(ev.data.completion, "Hello");
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.ok(chunk, "chunk should not be null");
  assert.equal(chunk.object, "chat.completion.chunk");
  assert.equal(chunk.id, ID);
  assert.equal(chunk.model, MODEL);
  assert.equal(chunk.created, CREATED);
  assert.equal(chunk.choices[0].delta.content, "Hello");
  assert.equal(chunk.choices[0].finish_reason, null);
  console.log("PASS: legacy completion event yields chunk");
}

// --- Test 2: Messages-style content_block_delta ---
{
  const ev = parseSSE(
    'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}',
  );
  assert.equal(ev.event, "content_block_delta");
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.ok(chunk);
  assert.equal(chunk.choices[0].delta.content, " world");
  assert.equal(chunk.choices[0].finish_reason, null);
  console.log("PASS: content_block_delta yields chunk");
}

// --- Test 3: message_stop event ---
{
  const ev = parseSSE(
    'event: message_stop\ndata: {"type":"message_stop"}',
  );
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.ok(chunk);
  assert.equal(chunk.choices[0].finish_reason, "stop");
  assert.deepEqual(chunk.choices[0].delta, {});
  console.log("PASS: message_stop yields finish_reason=stop with empty delta");
}

// --- Test 4: comment line ignored, event still parsed ---
{
  const ev = parseSSE(
    ':keepalive\nevent: completion\ndata: {"completion":"X","stop_reason":null}',
  );
  assert.equal(ev.event, "completion");
  assert.equal(ev.data.completion, "X");
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.ok(chunk);
  assert.equal(chunk.choices[0].delta.content, "X");
  console.log("PASS: SSE comment line ignored, event still parsed");
}

// --- Test 5: empty/unknown event returns null ---
{
  // message_start carries no text and no finish — should return null
  const ev = parseSSE(
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1"}}',
  );
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.equal(chunk, null);

  // missing data → null
  const ev2 = parseSSE("event: completion");
  assert.equal(translateClaudeAiSSE(ev2, ID, MODEL, CREATED), null);

  console.log("PASS: empty/unknown events return null");
}

// --- Test 6: legacy completion with stop_reason yields finish=stop ---
{
  const ev = parseSSE(
    'event: completion\ndata: {"completion":"","stop_reason":"end_turn"}',
  );
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.ok(chunk);
  assert.equal(chunk.choices[0].finish_reason, "stop");
  console.log("PASS: legacy completion with stop_reason yields finish=stop");
}

// --- Test 7: message_delta with stop_reason yields finish=stop ---
{
  const ev = parseSSE(
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
  );
  const chunk = translateClaudeAiSSE(ev, ID, MODEL, CREATED);
  assert.ok(chunk);
  assert.equal(chunk.choices[0].finish_reason, "stop");
  console.log("PASS: message_delta with stop_reason yields finish=stop");
}

// --- Test 8: CLAUDE_AI_API_VERSION constant exported and non-empty ---
{
  assert.equal(typeof CLAUDE_AI_API_VERSION, "string");
  assert.ok(CLAUDE_AI_API_VERSION.length > 0);
  console.log("PASS: CLAUDE_AI_API_VERSION =", CLAUDE_AI_API_VERSION);
}

console.log("\nAll translate_sse.js tests passed.");

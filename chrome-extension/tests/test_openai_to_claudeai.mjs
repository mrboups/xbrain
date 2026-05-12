// Plain-node ES module test for openaiToClaudeAi() + mapModel().
// Run via `node chrome-extension/tests/run_tests.mjs`.

import assert from "node:assert/strict";
import { openaiToClaudeAi, mapModel } from "../translate_sse.js";

// --- Test 1: full system + user message produces correct shape ---
{
  const body = {
    messages: [
      { role: "system", content: "Be brief" },
      { role: "user", content: "Hi" },
    ],
    model: "claude-opus-4-7",
  };
  const out = openaiToClaudeAi(body, "conv-1", null);

  // Prompt collapses turns with [System]/[Human]/[Assistant] labels
  assert.ok(
    out.prompt.includes("[System]\nBe brief"),
    `prompt should include [System] block, got: ${out.prompt}`,
  );
  assert.ok(
    out.prompt.includes("[Human]\nHi"),
    `prompt should include [Human] block, got: ${out.prompt}`,
  );
  // Turns separated by blank line
  assert.ok(
    out.prompt.includes("\n\n[Human]"),
    "turns should be separated by blank line",
  );

  // Default parent_message_uuid = nil UUID (A10)
  assert.equal(
    out.parent_message_uuid,
    "00000000-0000-4000-8000-000000000000",
    "first message uses nil UUID parent",
  );

  // Model passed through unchanged
  assert.equal(out.model, "claude-opus-4-7");

  // Locked claude.ai-specific keys
  assert.equal(out.rendering_mode, "messages");
  assert.equal(out.locale, "en-US");
  assert.deepEqual(out.personalized_styles, []);
  assert.deepEqual(out.tools, []);
  assert.deepEqual(out.attachments, []);
  assert.deepEqual(out.files, []);
  assert.deepEqual(out.sync_sources, []);

  // timezone is a non-empty string (Intl-resolved or "UTC" fallback)
  assert.equal(typeof out.timezone, "string");
  assert.ok(out.timezone.length > 0);

  // No extra unknown keys — exactly 11 keys per 09-CAPTURE.md ## Decisions
  const expectedKeys = [
    "attachments",
    "files",
    "locale",
    "model",
    "parent_message_uuid",
    "personalized_styles",
    "prompt",
    "rendering_mode",
    "sync_sources",
    "timezone",
    "tools",
  ];
  assert.deepEqual(Object.keys(out).sort(), expectedKeys);

  console.log("PASS: openaiToClaudeAi produces full canonical body shape");
}

// --- Test 2: unknown model maps to claude-sonnet-4-6 ---
{
  assert.equal(mapModel("gpt-4o"), "claude-sonnet-4-6");
  assert.equal(mapModel(undefined), "claude-sonnet-4-6");
  assert.equal(mapModel("claude-opus-4-7"), "claude-opus-4-7");
  assert.equal(mapModel("claude-sonnet-4-6"), "claude-sonnet-4-6");
  console.log("PASS: mapModel handles known + unknown models");
}

// --- Test 3: explicit parent_message_uuid overrides nil default ---
{
  const out = openaiToClaudeAi(
    { messages: [{ role: "user", content: "Hi" }], model: "claude-opus-4-7" },
    "conv-2",
    "11111111-2222-3333-4444-555555555555",
  );
  assert.equal(out.parent_message_uuid, "11111111-2222-3333-4444-555555555555");
  console.log("PASS: explicit parent_message_uuid respected");
}

// --- Test 4: assistant role labelled correctly in multi-turn ---
{
  const body = {
    messages: [
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello!" },
      { role: "user", content: "Tell me more" },
    ],
    model: "claude-opus-4-7",
  };
  const out = openaiToClaudeAi(body, "conv-3", null);
  assert.ok(out.prompt.includes("[Human]\nHi"));
  assert.ok(out.prompt.includes("[Assistant]\nHello!"));
  assert.ok(out.prompt.includes("[Human]\nTell me more"));
  console.log("PASS: multi-turn assistant labelled correctly");
}

console.log("\nAll openaiToClaudeAi tests passed.");

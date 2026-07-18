/**
 * Tests for chrome-extension/chat_stream.js (quick task 260512-tcr Wave 3.6).
 *
 * Pure helpers — no DOM, no chrome.*, no Centrifuge. Coverage:
 *   detectMentionClient — mirror of server-side regex
 *   StreamBuffer        — race conditions, finalize, drop
 *   formatRelative      — buckets, fallbacks
 *   hostnameFromUrl     — happy + invalid input
 *   authorLabel         — agent / self / teammate
 *   bubbleClass         — CSS class assignment
 *   provenanceLabel     — Pro/Max vs API vs none
 *   brainSummaryLabel   — agent "N sources from the brain" (Plan 20-03)
 *   savedToBrainLabel   — saved-to-brain badge text  (Plan 20-03)
 *   sameDay             — day-separator boundary     (Plan 20-03)
 */

import assert from "node:assert/strict";
import {
  StreamBuffer,
  detectMentionClient,
  formatRelative,
  hostnameFromUrl,
  authorLabel,
  bubbleClass,
  provenanceLabel,
  brainSummaryLabel,
  savedToBrainLabel,
  sameDay,
} from "../chat_stream.js";

let passed = 0;
let failed = 0;

function test(name, body) {
  try {
    body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

// ---------- detectMentionClient ----------

test("detectMentionClient: @claude matched", () => {
  const r = detectMentionClient("hey @claude what is up");
  assert.equal(r?.trigger, "claude");
  assert.equal(r?.agent_name, "claude-sonnet-4-6");
});

test("detectMentionClient: @c and @cl short aliases", () => {
  assert.equal(detectMentionClient("@c hi")?.trigger, "c");
  assert.equal(detectMentionClient("@cl ?")?.trigger, "cl");
});

test("detectMentionClient: case insensitive", () => {
  assert.equal(detectMentionClient("@CLAUDE pls")?.trigger, "claude");
  assert.equal(detectMentionClient("@Cl pls")?.trigger, "cl");
});

test("detectMentionClient: email-like rejected", () => {
  assert.equal(detectMentionClient("alice@claude.com"), null);
});

test("detectMentionClient: longer-word false positives rejected", () => {
  assert.equal(detectMentionClient("@cloud is up"), null);
  assert.equal(detectMentionClient("@claire said yes"), null);
  assert.equal(detectMentionClient("@claudes here"), null);
});

test("detectMentionClient: empty / null input", () => {
  assert.equal(detectMentionClient(""), null);
  assert.equal(detectMentionClient(null), null);
});

// ---------- StreamBuffer ----------

test("StreamBuffer: append in order", () => {
  const buf = new StreamBuffer();
  buf.start("m1");
  buf.append("m1", "hel");
  buf.append("m1", "lo ");
  buf.append("m1", "world");
  assert.equal(buf.get("m1"), "hello world");
  assert.equal(buf.isDone("m1"), false);
});

test("StreamBuffer: chunk-before-start race handled", () => {
  const buf = new StreamBuffer();
  // chunk arrives before start frame
  buf.append("m2", "first");
  buf.start("m2");
  buf.append("m2", "-second");
  assert.equal(buf.get("m2"), "first-second");
});

test("StreamBuffer: finalize sets done", () => {
  const buf = new StreamBuffer();
  buf.start("m3");
  buf.append("m3", "part");
  buf.finalize("m3");
  assert.equal(buf.isDone("m3"), true);
  assert.equal(buf.get("m3"), "part");
});

test("StreamBuffer: finalize with override replaces parts", () => {
  const buf = new StreamBuffer();
  buf.start("m4");
  buf.append("m4", "stale");
  buf.finalize("m4", "FINAL");
  assert.equal(buf.get("m4"), "FINAL");
});

test("StreamBuffer: drop removes entry", () => {
  const buf = new StreamBuffer();
  buf.start("m5");
  buf.append("m5", "x");
  buf.drop("m5");
  assert.equal(buf.get("m5"), "");
});

test("StreamBuffer: get on unknown id returns empty string", () => {
  const buf = new StreamBuffer();
  assert.equal(buf.get("ghost"), "");
  assert.equal(buf.isDone("ghost"), false);
});

// ---------- formatRelative ----------

test("formatRelative: <60s shows seconds", () => {
  const now = Date.now();
  const tenSecAgo = new Date(now - 10_000).toISOString();
  assert.equal(formatRelative(tenSecAgo, now), "10s ago");
});

test("formatRelative: minutes bucket", () => {
  const now = Date.now();
  const fiveMinAgo = new Date(now - 5 * 60_000).toISOString();
  assert.equal(formatRelative(fiveMinAgo, now), "5m ago");
});

test("formatRelative: hours bucket", () => {
  const now = Date.now();
  const threeHoursAgo = new Date(now - 3 * 3600_000).toISOString();
  assert.equal(formatRelative(threeHoursAgo, now), "3h ago");
});

test("formatRelative: older falls back to date string", () => {
  const now = Date.parse("2026-05-12T12:00:00Z");
  const tenDaysAgo = new Date(now - 10 * 86400_000).toISOString();
  const result = formatRelative(tenDaysAgo, now);
  // Older bucket returns a locale-dependent date (not "Xh ago" / "Xm ago" / "Xs ago").
  assert.ok(!/\d+[smh] ago$/.test(result), `unexpected relative format: ${result}`);
  assert.ok(result && result !== "—");
});

test("formatRelative: invalid input returns —", () => {
  assert.equal(formatRelative(null), "—");
  assert.equal(formatRelative(""), "—");
  assert.equal(formatRelative("not-a-date"), "—");
});

// ---------- hostnameFromUrl ----------

test("hostnameFromUrl: extracts host", () => {
  assert.equal(hostnameFromUrl("https://example.com/page"), "example.com");
  assert.equal(hostnameFromUrl("http://sub.example.com:8080"), "sub.example.com");
});

test("hostnameFromUrl: invalid returns 'unknown'", () => {
  assert.equal(hostnameFromUrl("not a url"), "unknown");
  assert.equal(hostnameFromUrl(""), "unknown");
});

// ---------- authorLabel ----------

test("authorLabel: agent uses 🤖 + agent_name", () => {
  const lbl = authorLabel({
    msg: { kind: "agent", agent_name: "claude-sonnet-4-6" },
    selfUserId: "u1",
    nameCache: {},
  });
  assert.match(lbl, /^🤖 claude-sonnet-4-6/);
});

test("authorLabel: self gets 'You'", () => {
  const lbl = authorLabel({
    msg: { kind: "user", author_user_id: "u1" },
    selfUserId: "u1",
    nameCache: { u1: "Alice" },
  });
  assert.equal(lbl, "You");
});

test("authorLabel: other from cache, fallback Teammate", () => {
  const lbl1 = authorLabel({
    msg: { kind: "user", author_user_id: "u2" },
    selfUserId: "u1",
    nameCache: { u2: "Bob" },
  });
  assert.equal(lbl1, "Bob");
  const lbl2 = authorLabel({
    msg: { kind: "user", author_user_id: "u3" },
    selfUserId: "u1",
    nameCache: {},
  });
  assert.equal(lbl2, "Teammate");
});

// ---------- bubbleClass ----------

test("bubbleClass: agent → is-agent; self → is-self; other → is-user", () => {
  const selfId = "u1";
  assert.equal(
    bubbleClass({ kind: "agent" }, selfId),
    "is-agent",
  );
  assert.equal(
    bubbleClass({ kind: "user", author_user_id: "u1" }, selfId),
    "is-self",
  );
  assert.equal(
    bubbleClass({ kind: "user", author_user_id: "u2" }, selfId),
    "is-user",
  );
});

// ---------- provenanceLabel ----------

test("provenanceLabel: Pro/Max vs API vs null", () => {
  const p1 = provenanceLabel("user_promax");
  assert.equal(p1.text, "via Pro/Max");
  assert.equal(p1.cls, "via-promax");
  const p2 = provenanceLabel("team_api");
  assert.equal(p2.text, "via team API");
  assert.equal(p2.cls, "via-api");
  assert.equal(provenanceLabel(null), null);
  assert.equal(provenanceLabel(undefined), null);
});

// ---------- brainSummaryLabel (Plan 20-03) ----------
//
// Sourced ONLY from the real `memory_items` count the agent pipeline already
// persists (team_chat_agent.py). Never fabricated.

test("brainSummaryLabel: pluralizes a real memory_items count", () => {
  assert.equal(brainSummaryLabel({ memory_items: 2 }), "2 sources from the brain");
  assert.equal(brainSummaryLabel({ memory_items: 1 }), "1 source from the brain");
  assert.equal(brainSummaryLabel({ memory_items: 7 }), "7 sources from the brain");
});

test("brainSummaryLabel: zero / missing / malformed → null (no empty details)", () => {
  assert.equal(brainSummaryLabel({ memory_items: 0 }), null);
  assert.equal(brainSummaryLabel({}), null);
  assert.equal(brainSummaryLabel(null), null);
  assert.equal(brainSummaryLabel(undefined), null);
  assert.equal(brainSummaryLabel({ memory_items: "lots" }), null);
  assert.equal(brainSummaryLabel({ memory_items: -3 }), null);
});

// ---------- savedToBrainLabel (Plan 20-03) ----------
//
// Derived from the real metadata.media attachment signal — an attachment IS the
// indexed-into-the-brain event. Plain text messages get NO badge (no spoofing).

test("savedToBrainLabel: document attachment → document indexed", () => {
  assert.equal(
    savedToBrainLabel({ metadata: { media: { mime: "application/pdf" } } }),
    "saved to brain · document indexed",
  );
});

test("savedToBrainLabel: image attachment → image indexed", () => {
  assert.equal(
    savedToBrainLabel({ metadata: { media: { mime: "image/png" } } }),
    "saved to brain · image indexed",
  );
});

test("savedToBrainLabel: no media → null (never fabricate provenance)", () => {
  assert.equal(savedToBrainLabel({ metadata: {} }), null);
  assert.equal(savedToBrainLabel({}), null);
  assert.equal(savedToBrainLabel(null), null);
});

test("savedToBrainLabel: media without mime still counts as a document", () => {
  assert.equal(
    savedToBrainLabel({ metadata: { media: { item_id: "abc" } } }),
    "saved to brain · document indexed",
  );
});

// ---------- sameDay (Plan 20-03) ----------
//
// UTC-calendar comparison so the result is deterministic regardless of the
// machine's timezone (the separator label is rendered from UTC too, so the
// grouping and the label always agree).

test("sameDay: same UTC calendar day → true", () => {
  assert.equal(sameDay("2026-07-18T09:00:00Z", "2026-07-18T23:00:00Z"), true);
  assert.equal(sameDay("2026-07-18T00:00:00Z", "2026-07-18T23:59:59Z"), true);
});

test("sameDay: different UTC calendar day → false", () => {
  assert.equal(sameDay("2026-07-18T09:00:00Z", "2026-07-19T09:00:00Z"), false);
  assert.equal(sameDay("2026-07-18T23:59:59Z", "2026-07-19T00:00:01Z"), false);
  assert.equal(sameDay("2025-07-18T09:00:00Z", "2026-07-18T09:00:00Z"), false);
});

test("sameDay: missing / invalid input → false (forces a separator, never throws)", () => {
  assert.equal(sameDay(null, "2026-07-18T09:00:00Z"), false);
  assert.equal(sameDay("2026-07-18T09:00:00Z", undefined), false);
  assert.equal(sameDay("not-a-date", "2026-07-18T09:00:00Z"), false);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

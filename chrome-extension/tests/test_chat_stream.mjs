/**
 * Tests for chrome-extension/chat_stream.js (quick task 260512-tcr Wave 3.6).
 *
 * Pure helpers — no DOM, no chrome.*, no Centrifuge. Coverage:
 *   buildMentionRegex   — regex built from the server's effective alias list
 *   detectMentionClient — mention detection over that server-derived regex
 *   StreamBuffer        — race conditions, finalize, drop
 *   formatRelative      — buckets, fallbacks
 *   hostnameFromUrl     — happy + invalid input
 *   authorLabel         — agent / self / teammate
 *   bubbleClass         — CSS class assignment
 *   provenanceLabel     — Pro/Max vs API vs none
 *   brainSummaryLabel   — agent "N sources from the brain" (Plan 20-03)
 *   indexedAttachment   — the item behind an indexed-attachment marker
 *   indexedTooltipText  — the sentence for one /indexed-text answer
 *   agentMentionAlias   — which alias the agent toggle writes
 *   withAgentMention    — the toggle's outgoing text, de-duped against a typed one
 *   agentFailureText    — the closed vocabulary a failed agent turn may print
 *   sameDay             — day-separator boundary     (Plan 20-03)
 */

import assert from "node:assert/strict";
import {
  StreamBuffer,
  detectMentionClient,
  buildMentionRegex,
  formatRelative,
  hostnameFromUrl,
  authorLabel,
  bubbleClass,
  provenanceLabel,
  brainSummaryLabel,
  indexedAttachment,
  indexedTooltipText,
  agentMentionAlias,
  withAgentMention,
  agentFailureText,
  isAgentUnavailable,
  AGENT_FAILURE_TEXT,
  AGENT_UNAVAILABLE_CODES,
  agentRouteStatusText,
  createSubscriptionWatcher,
  AGENT_ROUTE_STATUS,
  SUBSCRIPTION_LOST_NOTICE,
  SUBSCRIPTION_NOTICE_ACTION,
  AGENT_FAILURE_FALLBACK,
  sameDay,
} from "../../packages/chat-core/chat_stream.js";

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

// ---------- buildMentionRegex + detectMentionClient (built from server list) ----------
//
// The client no longer hardcodes a mention vocabulary. It builds its regex from
// the server's effective alias list (GET /v1/teams/{id}/agent-aliases) and must
// agree with what the server would summon for that same list — and reject
// @claude. This is the CLIENT half of the gate lesson (the server half is the
// real-Postgres detector test in memory-api).

// 1. Build-from-list positive: the trigger is whatever the server list contains.
test("detectMentionClient: builds from a server list (@agent / @chad / @a)", () => {
  const list = ["agent", "chad", "a"];
  assert.equal(detectMentionClient("@chad hi", list)?.trigger, "chad");
  assert.equal(detectMentionClient("@a hi", list)?.trigger, "a");
  assert.equal(detectMentionClient("@agent hi", list)?.trigger, "agent");
  // No model claim in the OPTIMISTIC bubble: a team may fall back to OpenAI or
  // xAI, and only the server's start frame knows which model actually answers.
  // Naming Claude here flashed the wrong model at every team not using it.
  assert.equal(
    detectMentionClient("@agent hi", list)?.agent_name,
    null,
    "the optimistic bubble must not claim a model the server has not named yet",
  );
});

// 2. Custom alias: a team's admin-set name summons.
test("detectMentionClient: a team's custom alias summons", () => {
  assert.equal(
    detectMentionClient("@wizard go", ["agent", "wizard"])?.trigger,
    "wizard",
  );
});

// 3. @claude rejected — reserved, filtered out of the alternation entirely.
test("detectMentionClient: @claude is never a client trigger", () => {
  const list = ["agent", "chad", "a", "wizard"];
  assert.equal(detectMentionClient("@claude hi", list), null);
  // Even smuggled into the list, buildMentionRegex filters "claude" out.
  const re = buildMentionRegex(["agent", "claude"]);
  assert.equal(re.test("@claude x"), false);
  assert.equal(re.test("@agent x"), true);
});

// 4. Boundary parity with the server: longest-first + trailing boundary + email
//    rejection + case-insensitivity.
test("detectMentionClient: boundary parity (trailing char, email, case)", () => {
  // "@apple" must NOT match ["agent","a"] — "a" needs a trailing boundary.
  assert.equal(detectMentionClient("@apple", ["agent", "a"]), null);
  // Email local@domain must never be read as a mention.
  assert.equal(detectMentionClient("alice@agent.com", ["agent"]), null);
  // Case-insensitive, like the server (flags "i").
  assert.equal(detectMentionClient("@AGENT", ["agent"])?.trigger, "agent");
});

// 5. Escape parity + longest-first — the security-relevant half (mirror of the
//    server's re.escape; a hostile alias cannot become a wildcard).
test("buildMentionRegex: JS-escapes each alias (mirror of server re.escape)", () => {
  const re = buildMentionRegex(["a.b"]);
  assert.equal(re.test("@a.b"), true); // the dot is a literal
  assert.equal(re.test("@axb"), false); // ...not a wildcard
});

test("buildMentionRegex: a hostile '.*' alias matches only the literal @.*", () => {
  const re = buildMentionRegex([".*"]);
  assert.equal(re.test("@.*"), true); // literal ".*"
  assert.equal(re.test("@anything"), false); // NOT a catch-all — escaped
});

test("buildMentionRegex: longest-first alternation wins (no truncation)", () => {
  assert.equal(
    detectMentionClient("@grove x", ["gr", "grove"])?.trigger,
    "grove",
  );
});

// 6. Empty / degenerate input.
test("detectMentionClient: empty / null input → null", () => {
  assert.equal(detectMentionClient("", ["agent"]), null);
  assert.equal(detectMentionClient(null, ["agent"]), null);
  assert.equal(detectMentionClient(null), null);
});

test("buildMentionRegex: empty list falls back to @agent (mirror server)", () => {
  const re = buildMentionRegex([]);
  assert.equal(re.test("@agent x"), true);
  assert.equal(detectMentionClient("@agent hi", [])?.trigger, "agent");
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

// The precedence below is the fix for a real bug: a person with a perfectly good
// Google name rendered as "Teammate" because the label depended ENTIRELY on a
// roster fetch that resolves after first paint — and on an author who has since
// left the team, that fetch never contains them at all. The server now sends the
// label with the message; the cache stays only as a fallback for older cached
// messages and for a client talking to an API that has not been redeployed.

test("authorLabel: the label the MESSAGE carries wins over the roster cache", () => {
  const lbl = authorLabel({
    msg: { kind: "user", author_user_id: "u2", author_label: "Nico Boups" },
    selfUserId: "u1",
    nameCache: { u2: "Stale Cached Name" },
  });
  assert.equal(
    lbl,
    "Nico Boups",
    "the message-carried label is correct on first paint and for former members; the cache is neither",
  );
});

test("authorLabel: a message with no label falls back to the cache", () => {
  const lbl = authorLabel({
    msg: { kind: "user", author_user_id: "u2" },
    selfUserId: "u1",
    nameCache: { u2: "Bob" },
  });
  assert.equal(lbl, "Bob", "older messages carry no label and must still render a name");
});

test("authorLabel: an empty or blank carried label does not win", () => {
  for (const carried of ["", "   ", null, 42]) {
    const lbl = authorLabel({
      msg: { kind: "user", author_user_id: "u2", author_label: carried },
      selfUserId: "u1",
      nameCache: { u2: "Bob" },
    });
    assert.equal(
      lbl,
      "Bob",
      `author_label=${JSON.stringify(carried)} is not a name — it must fall through, not blank the row`,
    );
  }
});

test("authorLabel: self still gets 'You', even with a carried label", () => {
  const lbl = authorLabel({
    msg: { kind: "user", author_user_id: "u1", author_label: "Nico Boups" },
    selfUserId: "u1",
    nameCache: {},
  });
  assert.equal(lbl, "You");
});

test("authorLabel: the agent branch ignores the carried label", () => {
  const lbl = authorLabel({
    msg: { kind: "agent", agent_name: "claude", author_label: "Not The Agent" },
    selfUserId: "u1",
    nameCache: {},
  });
  assert.match(lbl, /^🤖 claude/);
});

test("authorLabel: 'Teammate' only when there is genuinely nothing", () => {
  const lbl = authorLabel({
    msg: { kind: "user", author_user_id: "u9" },
    selfUserId: "u1",
    nameCache: {},
  });
  assert.equal(lbl, "Teammate");
});

test("authorLabel: pure — the same inputs give the same answer, and nothing is mutated", () => {
  const msg = { kind: "user", author_user_id: "u2", author_label: "Nico" };
  const cache = { u2: "Bob" };
  const first = authorLabel({ msg, selfUserId: "u1", nameCache: cache });
  const second = authorLabel({ msg, selfUserId: "u1", nameCache: cache });
  assert.equal(first, second);
  assert.deepEqual(msg, {
    kind: "user",
    author_user_id: "u2",
    author_label: "Nico",
  });
  assert.deepEqual(cache, { u2: "Bob" });
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
  // The team's OWN key is a different bill from the deployment's, so it is a
  // different badge. `team_api` keeps its meaning because rows already carry it.
  const p3 = provenanceLabel("team_key");
  assert.equal(p3.text, "via team key");
  assert.notEqual(p3.text, p2.text, "the two payers must not share a label");
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

// ---------- indexedAttachment ----------
//
// Derived from the real metadata.media attachment signal — an attachment IS the
// indexed-into-the-brain event. Plain text messages get NO marker (no spoofing).
// It answers with the ITEM, because the id is what the reveal fetches; the badge
// text it replaced ("saved to brain · image indexed") said only that something
// had happened.

test("indexedAttachment: document attachment → the item id and kind", () => {
  assert.deepEqual(
    indexedAttachment({
      metadata: { media: { mime: "application/pdf", item_id: "i-1" } },
    }),
    { itemId: "i-1", kind: "document" },
  );
});

test("indexedAttachment: image attachment → kind image", () => {
  assert.deepEqual(
    indexedAttachment({ metadata: { media: { mime: "image/png", item_id: "i-2" } } }),
    { itemId: "i-2", kind: "image" },
  );
});

test("indexedAttachment: no media → null (never fabricate provenance)", () => {
  assert.equal(indexedAttachment({ metadata: {} }), null);
  assert.equal(indexedAttachment({}), null);
  assert.equal(indexedAttachment(null), null);
});

test("indexedAttachment: media without item_id → null (nothing to fetch)", () => {
  // There is no id to ask about, so a marker would be an affordance with no
  // answer behind it.
  assert.equal(indexedAttachment({ metadata: { media: { mime: "image/png" } } }), null);
});

test("indexedAttachment: media without mime still counts as a document", () => {
  assert.deepEqual(indexedAttachment({ metadata: { media: { item_id: "abc" } } }), {
    itemId: "abc",
    kind: "document",
  });
});

// ---------- indexedTooltipText ----------
//
// The point of this helper is that it is TOTAL: every input produces a sentence.
// Before it, an image still being described, one deliberately skipped, one whose
// indexing broke, and a request that never came back all rendered as the same
// empty box.

test("indexedTooltipText: indexed → the text itself", () => {
  assert.equal(
    indexedTooltipText({ state: "indexed", text: "A deploy pipeline diagram." }),
    "A deploy pipeline diagram.",
  );
});

test("indexedTooltipText: indexed + detail → the text, then the caveat", () => {
  const out = indexedTooltipText({
    state: "indexed",
    text: "Page one.",
    detail: "Showing the first of 4 indexed parts.",
  });
  assert.equal(out, "Page one.\n\nShowing the first of 4 indexed parts.");
});

test("indexedTooltipText: pending → an in-flight sentence, never blank", () => {
  assert.equal(indexedTooltipText({ state: "pending", text: "" }), "Indexing…");
});

test("indexedTooltipText: failed → a failure sentence, never blank", () => {
  assert.equal(indexedTooltipText({ state: "failed", text: "" }), "Indexing failed.");
  assert.equal(
    indexedTooltipText({ state: "failed", detail: "Indexing failed. Try again." }),
    "Indexing failed. Try again.",
  );
});

test("indexedTooltipText: not_indexed → the server's sentence, else a default", () => {
  assert.equal(
    indexedTooltipText({ state: "not_indexed", detail: "This image is too large to index." }),
    "This image is too large to index.",
  );
  assert.equal(indexedTooltipText({ state: "not_indexed" }), "Not indexed.");
});

test("indexedTooltipText: a load failure is its OWN sentence, not 'not indexed'", () => {
  // The server never said anything, so claiming it said "not indexed" would be
  // reporting an answer that does not exist.
  assert.equal(
    indexedTooltipText(null),
    "The indexed text could not be loaded.",
  );
  assert.equal(
    indexedTooltipText(undefined),
    "The indexed text could not be loaded.",
  );
});

test("indexedTooltipText: 'indexed' with no text falls back rather than going blank", () => {
  assert.equal(indexedTooltipText({ state: "indexed", text: "   " }), "Not indexed.");
});

test("indexedTooltipText: every state produces a non-empty string", () => {
  const inputs = [
    null,
    undefined,
    {},
    { state: "indexed" },
    { state: "pending" },
    { state: "failed" },
    { state: "not_indexed" },
    { state: "something_new_from_a_newer_server" },
    { state: "indexed", text: "" , detail: "" },
  ];
  for (const input of inputs) {
    const out = indexedTooltipText(input);
    assert.equal(typeof out, "string");
    assert.ok(out.trim().length > 0, `blank tooltip for ${JSON.stringify(input)}`);
  }
});

// ---------- agentFailureText ----------
//
// A failure payload is exactly where a provider's error text ends up when
// something upstream regresses — one shipped into a team chat naming the vendor
// and the account's credit balance. So this function reads a CODE and nothing
// else: there is no input that can make it produce words it does not already
// contain. That is the guarantee, and it holds whatever the server sends.

test("agentFailureText: a known code gets its own sentence", () => {
  assert.equal(agentFailureText({ code: "timeout" }), AGENT_FAILURE_TEXT.timeout);
  assert.equal(agentFailureText({ code: "unavailable" }), AGENT_FAILURE_TEXT.unavailable);
  assert.equal(
    agentFailureText({ code: "configuration" }),
    AGENT_FAILURE_TEXT.configuration,
  );
});

test("agentFailureText: the output is ALWAYS from the closed vocabulary", () => {
  const allowed = new Set([...Object.values(AGENT_FAILURE_TEXT), AGENT_FAILURE_FALLBACK]);
  const hostile = [
    null,
    undefined,
    {},
    { code: "" },
    { code: 42 },
    { code: "a_code_from_a_newer_server" },
    { code: "toString" }, // a prototype key must not resolve to a function
    { code: "constructor" },
    { error: "Error code: 400 - your credit balance is too low" },
    { message: "sk-ant-api03-XXXX" },
    { code: "timeout", error: "raw provider text riding along" },
  ];
  for (const input of hostile) {
    const out = agentFailureText(input);
    assert.ok(
      allowed.has(out),
      `${JSON.stringify(input)} produced words outside the vocabulary: ${JSON.stringify(out)}`,
    );
  }
});

test("agentFailureText: no sentence invents a cause the client cannot know", () => {
  for (const sentence of [...Object.values(AGENT_FAILURE_TEXT), AGENT_FAILURE_FALLBACK]) {
    const lowered = sentence.toLowerCase();
    for (const guess of ["because", "due to", "caused by", "billing", "credit", "quota"]) {
      assert.ok(!lowered.includes(guess), `"${sentence}" guesses a cause`);
    }
    assert.ok(sentence[0] === sentence[0].toUpperCase() && sentence.endsWith("."));
  }
});

// ---------- unavailability vs failure ----------
//
// A team whose agent has nothing to run on — no live bridge for that person
// anywhere, and no key — used to be shown the same "could not answer" as a
// crashed provider. A configuration nobody had finished setting up therefore
// read as a product that does not work.

test("the unavailability codes are part of the closed vocabulary", () => {
  for (const code of AGENT_UNAVAILABLE_CODES) {
    assert.ok(
      Object.prototype.hasOwnProperty.call(AGENT_FAILURE_TEXT, code),
      `${code} has no sentence — it would render as the vague fallback`,
    );
  }
});

test("isAgentUnavailable is total, and defaults to treating things as failures", () => {
  assert.equal(isAgentUnavailable({ code: "no_route" }), true);
  assert.equal(isAgentUnavailable({ code: "subscription_lost" }), true);
  for (const input of [
    { code: "timeout" },
    { code: "unavailable" }, // a transient outage, NOT an unavailability state
    { code: "configuration" },
    { code: "made_up" },
    { code: 7 },
    {},
    null,
    undefined,
    "no_route",
  ]) {
    assert.equal(
      isAgentUnavailable(input),
      false,
      `${JSON.stringify(input)} must not be treated as unavailability — calling a real malfunction "not available" understates it`,
    );
  }
});

test("an unavailability sentence never reads as a failed attempt", () => {
  for (const code of AGENT_UNAVAILABLE_CODES) {
    const lowered = AGENT_FAILURE_TEXT[code].toLowerCase();
    for (const verb of ["failed", "error", "went wrong", "could not answer"]) {
      assert.ok(
        !lowered.includes(verb),
        `"${AGENT_FAILURE_TEXT[code]}" says ${verb} — nothing was attempted`,
      );
    }
  }
});

test("an unavailability sentence says what would make it work", () => {
  // The INTENT is that an absence always names something the reader can do.
  // The original spelling of that intent was "mentions the extension", which was
  // true while both codes had the extension as their remedy — and wrong the
  // moment provider_key_missing arrived, whose remedy is team settings. A test
  // that encodes one remedy rejects every other correct sentence, so it now
  // asserts the intent instead: at least one real destination is named.
  const REMEDIES = ["extension", "team settings", "choose a provider"];
  for (const code of AGENT_UNAVAILABLE_CODES) {
    const text = AGENT_FAILURE_TEXT[code].toLowerCase();
    assert.ok(
      REMEDIES.some((r) => text.includes(r)),
      `${code} names no remedy — an absence with no remedy is just bad news`,
    );
  }
});

test("no unavailability sentence mentions this device", () => {
  // The bridge is keyed by USER, not by device: a phone with no extension
  // routes through whatever browser that person has open somewhere and answers
  // perfectly. "Not available on mobile" would be false about a working feature.
  for (const code of AGENT_UNAVAILABLE_CODES) {
    const lowered = AGENT_FAILURE_TEXT[code].toLowerCase();
    for (const word of [
      "this device",
      "phone",
      "mobile",
      "desktop",
      "laptop",
      "your browser",
    ]) {
      assert.ok(
        !lowered.includes(word),
        `${code} says "${word}" — that makes a user-keyed condition sound device-specific`,
      );
    }
  }
});

test("a refused team key is a failure, not an unavailability", () => {
  // An attempt WAS made and refused, so dressing it as "not available" would
  // understate it — and the fix belongs to the team, not to an administrator.
  assert.equal(isAgentUnavailable({ code: "team_key_rejected" }), false);
  const sentence = AGENT_FAILURE_TEXT.team_key_rejected;
  assert.ok(sentence, "team_key_rejected must have its own sentence");
  assert.ok(
    /team/i.test(sentence),
    "whose key failed is the useful part — say it was the team's",
  );
  for (const leak of ["anthropic", "401", "403", "x-api-key", "sk-"]) {
    assert.ok(
      !sentence.toLowerCase().includes(leak),
      `"${sentence}" carries the provider's own words`,
    );
  }
});

test("the client's vocabulary still cannot print anything the frame carries", () => {
  // The new codes must not have opened a text path. Same total-function claim as
  // above, re-asserted against payloads shaped like the new states.
  const allowed = new Set([...Object.values(AGENT_FAILURE_TEXT), AGENT_FAILURE_FALLBACK]);
  for (const input of [
    { code: "no_route", message: "ANTHROPIC_API_KEY sk-ant-0123 is unset" },
    { code: "subscription_lost", error: "socket 4401 for github:someone" },
    { code: "no_route", detail: { raw: "<html>502</html>" } },
  ]) {
    assert.ok(allowed.has(agentFailureText(input)), "a frame's own words must never render");
  }
});

// ---------- which model answers, and losing the bridge mid-session ----------

test("the route status names who is answering, and who pays", () => {
  assert.equal(
    agentRouteStatusText({ route: "user_promax" }),
    AGENT_ROUTE_STATUS.user_promax,
  );
  // The two paying tiers must not read the same. "Your subscription is
  // answering" and "the team is being billed" are very different facts to the
  // person paying.
  assert.notEqual(
    agentRouteStatusText({ route: "team_key" }),
    agentRouteStatusText({ route: "team_api" }),
  );
});

test("the route status says nothing it was not told", () => {
  // Unavailability is the agent failure vocabulary's job. Saying it in two
  // places would let the two disagree about what is happening.
  assert.equal(agentRouteStatusText({ route: "unavailable" }), null);
  for (const input of [null, undefined, {}, { route: "invented" }, { route: 7 }, "user_promax"]) {
    assert.equal(
      agentRouteStatusText(input),
      null,
      `${JSON.stringify(input)} must produce no claim`,
    );
  }
});

test("no route status mentions this device", () => {
  for (const text of Object.values(AGENT_ROUTE_STATUS)) {
    if (!text) continue;
    for (const word of ["this device", "phone", "mobile", "desktop", "laptop"]) {
      assert.ok(!text.toLowerCase().includes(word), `"${text}" says "${word}"`);
    }
  }
});

test("losing the bridge is a transition, not a state", () => {
  // Somebody who never had a bridge — a colleague with no extension at all —
  // is losing nothing and must not be nagged.
  const never = createSubscriptionWatcher();
  for (let i = 0; i < 5; i++) {
    assert.equal(
      never.observe({ subscription_connected: false }),
      false,
      "a person who never had a bridge is never warned about losing one",
    );
  }

  const had = createSubscriptionWatcher();
  assert.equal(had.observe({ subscription_connected: true }), false, "nothing to say yet");
  assert.equal(had.observe({ subscription_connected: false }), true, "the loss is news");
});

test("a dismissed warning does not come straight back", () => {
  const w = createSubscriptionWatcher();
  w.observe({ subscription_connected: true });
  assert.equal(w.observe({ subscription_connected: false }), true);
  w.dismiss();
  assert.equal(w.isShowing(), false);
  for (let i = 0; i < 10; i++) {
    assert.equal(
      w.observe({ subscription_connected: false }),
      false,
      "a warning that reappears is one people learn to ignore",
    );
  }
});

test("a genuine reconnect re-arms the warning", () => {
  const w = createSubscriptionWatcher();
  w.observe({ subscription_connected: true });
  w.observe({ subscription_connected: false });
  w.dismiss();
  // The bridge comes back...
  assert.equal(w.observe({ subscription_connected: true }), false);
  // ...and goes again. That is new news, so it is said again.
  assert.equal(w.observe({ subscription_connected: false }), true);
});

test("a reconnect clears a warning nobody dismissed", () => {
  const w = createSubscriptionWatcher();
  w.observe({ subscription_connected: true });
  assert.equal(w.observe({ subscription_connected: false }), true);
  assert.equal(w.observe({ subscription_connected: true }), false);
  assert.equal(w.isShowing(), false);
});

test("an unreadable observation changes nothing", () => {
  // A failed poll is not evidence the bridge died. Treating it as one would
  // fire the notice every time a phone changed cell.
  const w = createSubscriptionWatcher();
  w.observe({ subscription_connected: true });
  for (const junk of [null, undefined, {}, { subscription_connected: "yes" }, "nope"]) {
    assert.equal(w.observe(junk), false, `${JSON.stringify(junk)} must not raise the notice`);
  }
  assert.equal(w.hasEverConnected(), true, "a bad poll must not erase what we knew");
  assert.equal(w.observe({ subscription_connected: false }), true);
});

test("the notice offers both remedies and is honest about their cost", () => {
  const lowered = SUBSCRIPTION_LOST_NOTICE.toLowerCase();
  assert.ok(lowered.includes("extension"), "reopening the browser is the free remedy");
  assert.ok(lowered.includes("team api key"), "the fallback must be named");
  assert.ok(
    lowered.includes("billed"),
    "a key costs money and the sentence has to say so",
  );
  assert.ok(
    lowered.indexOf("extension") < lowered.indexOf("team api key"),
    "the free remedy comes first — a key is the fallback, not the default fix",
  );
  for (const word of ["this device", "phone", "mobile", "desktop", "laptop"]) {
    assert.ok(!lowered.includes(word), `the notice says "${word}"`);
  }
});

test("the notice names WHO can set the key, because most readers cannot", () => {
  // The PUT is admin-only. The person most likely to be reading this is on a
  // phone with no way to check which they are, and "or set a team API key"
  // reads as an instruction to everybody. Naming the role makes the sentence
  // true for both, and tells the member who to ask.
  assert.match(
    SUBSCRIPTION_LOST_NOTICE,
    /team admin/i,
    "the notice tells every reader to do something only an admin can",
  );
});

test("the remedy it names now has a control to reach it", () => {
  // The whole defect this closes: the notice named a key that could only be set
  // on a desktop admin page, and a standalone PWA has no address bar to get
  // there with. A label with no destination is worse than no label.
  assert.equal(typeof SUBSCRIPTION_NOTICE_ACTION, "string");
  assert.ok(SUBSCRIPTION_NOTICE_ACTION.trim().length > 0, "the control has no label");
  // A DESTINATION, not an act. "Set a team API key" on a control half the
  // readers cannot use is a button that lied to them.
  assert.ok(
    !/^set\b/i.test(SUBSCRIPTION_NOTICE_ACTION),
    `"${SUBSCRIPTION_NOTICE_ACTION}" promises a capability a member does not have`,
  );
  assert.match(SUBSCRIPTION_NOTICE_ACTION, /key/i, "the label does not say where it goes");
});

// ---------- agentMentionAlias / withAgentMention ----------
//
// The agent toggle is not a second way to summon. It writes the SAME mention a
// person would type and the server's detector decides from the text, so there is
// one authority and nothing to disagree with. These tests are mostly about the
// place that would break: a message that is BOTH toggled and typed must summon
// exactly once, and it must not read as "@agent @agent ...".

test("agentMentionAlias: the server's list, in the server's order", () => {
  assert.equal(agentMentionAlias(["chad", "agent"]), "chad");
  assert.equal(agentMentionAlias(["agent"]), "agent");
});

test("agentMentionAlias: 'claude' is skipped — it is never a client trigger", () => {
  // buildMentionRegex drops it, so writing "@claude" would produce a message
  // that looks summoned and is not.
  assert.equal(agentMentionAlias(["claude", "chad"]), "chad");
  assert.equal(agentMentionAlias(["claude"]), "agent");
});

test("agentMentionAlias: an empty / junk list falls back to the base alias", () => {
  assert.equal(agentMentionAlias([]), "agent");
  assert.equal(agentMentionAlias(null), "agent");
  assert.equal(agentMentionAlias([" ", 7, null]), "agent");
});

test("withAgentMention: a bare draft gets the team's mention prepended", () => {
  assert.equal(
    withAgentMention("what is in the deck?", { aliases: ["agent"] }),
    "@agent what is in the deck?",
  );
  assert.equal(
    withAgentMention("status?", { aliases: ["chad", "agent"] }),
    "@chad status?",
  );
});

test("withAgentMention: a draft that ALREADY mentions the agent is untouched", () => {
  // Toggled and typed must summon once, and must not read "@agent @agent ...".
  const aliases = ["agent"];
  const regex = buildMentionRegex(aliases);
  for (const draft of [
    "@agent what is in the deck?",
    "hey @agent what is in the deck?",
    "@AGENT case does not matter",
  ]) {
    assert.equal(withAgentMention(draft, { aliases, regex }), draft);
  }
});

test("withAgentMention: the de-dupe uses the SERVER's alias list, not a guess", () => {
  // A team whose agent is @chad: a draft naming @chad is already a summon, so
  // prepending "@chad" again would double it.
  const aliases = ["chad", "agent"];
  const regex = buildMentionRegex(aliases);
  assert.equal(withAgentMention("@chad ping", { aliases, regex }), "@chad ping");
  // ...and a draft naming something that is NOT an alias is not a summon.
  assert.equal(
    withAgentMention("@marketing ping", { aliases, regex }),
    "@chad @marketing ping",
  );
});

test("withAgentMention: exactly ONE mention lands, however it is combined", () => {
  const aliases = ["agent", "chad"];
  const regex = buildMentionRegex(aliases);
  // Counted with the SAME boundary pattern the detector uses, made global — so
  // this asserts what the server would see, not what a substring search finds.
  const countRe = new RegExp(regex.source, "gi");
  const combos = [
    "plain question",
    "@agent typed question",
    "hey @chad look at this",
    "  leading space question",
    "an email like alice@agent.com is not a mention",
    "@marketing is not the agent",
  ];
  for (const draft of combos) {
    const out = withAgentMention(draft, { aliases, regex });
    const mentions = out.match(countRe) || [];
    assert.equal(
      mentions.length,
      1,
      `"${draft}" produced ${mentions.length} mentions: ${JSON.stringify(out)}`,
    );
  }
});

test("withAgentMention: an email address is not a mention, so it still gets one", () => {
  // The boundary rules are the server's; this asserts the toggle inherits them
  // rather than doing its own "does the text contain @agent" check.
  assert.equal(
    withAgentMention("mail alice@agent.com", { aliases: ["agent"] }),
    "@agent mail alice@agent.com",
  );
});

test("withAgentMention: leading whitespace is not preserved into a double space", () => {
  assert.equal(withAgentMention("   hi", { aliases: ["agent"] }), "@agent hi");
});

test("withAgentMention: a non-string draft never throws", () => {
  assert.equal(withAgentMention(null, { aliases: ["agent"] }), "@agent");
  assert.equal(withAgentMention(undefined), "@agent");
  assert.equal(withAgentMention(""), "@agent");
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

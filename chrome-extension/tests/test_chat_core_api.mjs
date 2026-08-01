/**
 * Tests for the shared API client + the extension's platform shim (Phase 27,
 * Plan 27-01, D-27-04).
 *
 * Subject: packages/chat-core/api.js — the SOURCE, not a generated copy, so a
 * behaviour is proven where it is edited (test_chat_core_sync.mjs separately
 * proves the copies match).
 *
 * What is locked here:
 *   (a) request() builds `Authorization: Bearer <token>` from the INJECTED
 *       getToken — the client is the only place that header is assembled;
 *   (b) a non-2xx throws `Error("HTTP <status>: <first 200 chars>")` — the exact
 *       message shape popup.js already surfaced via fetchJson, so every existing
 *       catch/alert path keeps working unchanged;
 *   (c) a 204 (and any empty body) resolves to null instead of throwing on a
 *       JSON parse of "";
 *   (d) listMessages(teamId, {before}) percent-encodes `before` in the query;
 *   (e) chromePlatform satisfies assertPlatform — storage.get/set/remove,
 *       openUrl, notify (T-27-01-04: a half-implemented shim must be loud).
 *
 * Pure node test — globalThis.fetch is stubbed and restored in a finally; no
 * network, no browser. Picked up by run_tests.mjs (file name starts with test_).
 */

import assert from "node:assert/strict";
import { createApi } from "../../packages/chat-core/api.js";
import { assertPlatform } from "../../packages/chat-core/platform.js";

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

/** Minimal Response stand-in — api.js reads only ok / status / text(). */
function fakeResponse({ status = 200, body = "" } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  };
}

/**
 * Run `body` with globalThis.fetch replaced by a recording stub. Restores the
 * original in a finally so one failing test cannot leak a stub into the next.
 */
async function withFetch(handler, body) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return handler({ url, init });
  };
  try {
    return await body(calls);
  } finally {
    globalThis.fetch = original;
  }
}

const BASE = "https://api.example.test";

// ---- (a) Authorization header comes from the injected getToken ----

await test("request() builds Authorization: Bearer from the injected getToken", async () => {
  await withFetch(
    () => fakeResponse({ status: 200, body: '{"id":"u1"}' }),
    async (calls) => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "xbt_abc123" });
      const out = await api.me();
      assert.deepEqual(out, { id: "u1" });
      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, `${BASE}/v1/me`);
      assert.equal(calls[0].init.headers.Authorization, "Bearer xbt_abc123");
    },
  );
});

await test("a null token sends NO Authorization header (never 'Bearer null')", async () => {
  await withFetch(
    () => fakeResponse({ status: 200, body: "{}" }),
    async (calls) => {
      const api = createApi({ baseUrl: BASE, getToken: async () => null });
      await api.me();
      assert.equal(
        "Authorization" in calls[0].init.headers,
        false,
        "a signed-out caller must send no credential at all — 'Bearer null' looks like one",
      );
    },
  );
});

await test("getToken is re-read per request (a refreshed token is picked up)", async () => {
  await withFetch(
    () => fakeResponse({ status: 200, body: "{}" }),
    async (calls) => {
      let token = "first";
      const api = createApi({ baseUrl: BASE, getToken: async () => token });
      await api.me();
      token = "second";
      await api.me();
      assert.equal(calls[0].init.headers.Authorization, "Bearer first");
      assert.equal(calls[1].init.headers.Authorization, "Bearer second");
    },
  );
});

// ---- (b) Error shape is byte-for-byte the one popup.js already surfaced ----

await test("non-2xx throws Error('HTTP <status>: <body>') — the fetchJson shape", async () => {
  await withFetch(
    () => fakeResponse({ status: 403, body: "forbidden: not a member" }),
    async () => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      await assert.rejects(
        () => api.me(),
        (err) => {
          assert.ok(err instanceof Error);
          assert.equal(err.message, "HTTP 403: forbidden: not a member");
          return true;
        },
      );
    },
  );
});

await test("the error body is truncated to the first 200 chars", async () => {
  const long = "z".repeat(500);
  await withFetch(
    () => fakeResponse({ status: 500, body: long }),
    async () => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      await assert.rejects(
        () => api.me(),
        (err) => {
          assert.equal(err.message, `HTTP 500: ${"z".repeat(200)}`);
          return true;
        },
      );
    },
  );
});

// ---- (c) 204 / empty body resolves to null ----

await test("a 204 resolves to null instead of throwing on an empty body", async () => {
  await withFetch(
    () => fakeResponse({ status: 204, body: "" }),
    async () => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      assert.equal(await api.markRead("team-1"), null);
    },
  );
});

await test("a 200 with an empty body resolves to null (no JSON.parse('') crash)", async () => {
  await withFetch(
    () => fakeResponse({ status: 200, body: "" }),
    async () => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      assert.equal(await api.markRead("team-1"), null);
    },
  );
});

// ---- (d) listMessages percent-encodes `before` ----

await test("listMessages({before}) percent-encodes the cursor in the query", async () => {
  await withFetch(
    () => fakeResponse({ status: 200, body: '{"messages":[]}' }),
    async (calls) => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      const before = "2026-08-01T10:00:00+00:00";
      await api.listMessages("team-1", { before });
      assert.equal(
        calls[0].url,
        `${BASE}/v1/teams/team-1/messages?before=${encodeURIComponent(before)}&limit=50`,
      );
      assert.ok(
        calls[0].url.includes("%3A"),
        "the ISO cursor's colons must be percent-encoded — a raw '+' or ':' changes the parsed timestamp",
      );
    },
  );
});

await test("listMessages() with no cursor asks for the newest page only", async () => {
  await withFetch(
    () => fakeResponse({ status: 200, body: '{"messages":[]}' }),
    async (calls) => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      await api.listMessages("team-1");
      assert.equal(calls[0].url, `${BASE}/v1/teams/team-1/messages?limit=50`);
    },
  );
});

await test("postMessage sends JSON with the right method and content type", async () => {
  await withFetch(
    () => fakeResponse({ status: 201, body: '{"id":"m1"}' }),
    async (calls) => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      const sent = await api.postMessage("team-1", { content: "hello" });
      assert.deepEqual(sent, { id: "m1" });
      assert.equal(calls[0].init.method, "POST");
      assert.equal(calls[0].init.headers["Content-Type"], "application/json");
      assert.equal(calls[0].init.body, JSON.stringify({ content: "hello" }));
    },
  );
});

await test("rawFetch returns the Response unthrown so status-driven callers survive", async () => {
  await withFetch(
    () => fakeResponse({ status: 429, body: "slow down" }),
    async (calls) => {
      const api = createApi({ baseUrl: BASE, getToken: async () => "t" });
      const res = await api.rawFetch("/v1/teams/team-1/nudge-open", {
        method: "POST",
        body: { url: "https://example.test/" },
      });
      assert.equal(res.status, 429, "rawFetch must not throw — 429 is a value the caller maps to copy");
      assert.equal(calls[0].init.headers.Authorization, "Bearer t");
    },
  );
});

// ---- createApi guards ----

await test("createApi refuses a missing baseUrl or getToken", () => {
  assert.throws(() => createApi({ getToken: async () => "t" }), TypeError);
  assert.throws(() => createApi({ baseUrl: BASE }), TypeError);
  assert.throws(() => createApi(), TypeError);
});

// ---- (e) The extension's shim satisfies the contract ----

await test("chromePlatform satisfies assertPlatform (storage/openUrl/notify)", async () => {
  // Dynamic import: platform_chrome.js runs assertPlatform at module load, so a
  // regression surfaces here as an import failure rather than a silent no-op.
  const { chromePlatform } = await import("../platform_chrome.js");
  assert.ok(chromePlatform, "platform_chrome.js must export chromePlatform");
  assert.doesNotThrow(
    () => assertPlatform(chromePlatform),
    "chromePlatform must satisfy the platform contract — a missing member would silently drop a token read or a notification",
  );
  for (const fn of ["get", "set", "remove"]) {
    assert.equal(
      typeof chromePlatform.storage[fn],
      "function",
      `chromePlatform.storage.${fn} must be a function`,
    );
  }
  assert.equal(typeof chromePlatform.openUrl, "function");
  assert.equal(typeof chromePlatform.notify, "function");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

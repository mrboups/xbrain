/**
 * Tests for chrome-extension/onboarding.js (quick task 260512-eo1).
 *
 * Verifies:
 *   1. readStoredAuth returns nulls when both tiers are empty.
 *   2. readStoredAuth reads from `session` when `local` is empty (legacy bootstrap).
 *   3. readStoredAuth prefers `local` over `session` when both are set.
 *   4. mintAndConnect writes {xbt_token, user_sub, api_token_id} to storage on success.
 *   5. mintAndConnect returns {ok: false} WITHOUT writing partial state when /v1/me fails.
 *   6. mintAndConnect returns {ok: false} when source_user_id is missing.
 *   7. disconnectAuth clears local + session and calls DELETE /v1/me/api-token/{id}.
 *   8. disconnectAuth still clears storage even if the DELETE network call fails.
 *
 * Pure node test — no chrome.* polyfill needed because onboarding.js takes
 * all deps via injection. Picked up by run_tests.mjs (file name starts with test_).
 */

import assert from "node:assert/strict";
import {
  readStoredAuth,
  mintAndConnect,
  disconnectAuth,
} from "../onboarding.js";

// ---------- minimal in-memory mocks ----------

function makeStorageArea() {
  const store = new Map();
  return {
    _store: store,
    async get(keys) {
      const out = {};
      const arr = Array.isArray(keys) ? keys : [keys];
      for (const k of arr) if (store.has(k)) out[k] = store.get(k);
      return out;
    },
    async set(obj) {
      for (const [k, v] of Object.entries(obj)) store.set(k, v);
    },
    async remove(keys) {
      const arr = Array.isArray(keys) ? keys : [keys];
      for (const k of arr) store.delete(k);
    },
  };
}

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

// Tracks every fetch call so assertions can inspect what was sent.
function makeFetchSpy(responder) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    return responder(url, init);
  };
  fn.calls = calls;
  return fn;
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

// ---------- 1. readStoredAuth: empty ----------

await test("readStoredAuth returns nulls when both tiers empty", async () => {
  const storage = { local: makeStorageArea(), session: makeStorageArea() };
  const out = await readStoredAuth(storage);
  assert.deepEqual(out, {
    xbt_token: null,
    user_sub: null,
    api_token_id: null,
  });
});

// ---------- 2. readStoredAuth: session fallback ----------

await test("readStoredAuth falls back to session when local empty", async () => {
  const storage = { local: makeStorageArea(), session: makeStorageArea() };
  await storage.session.set({
    xbt_token: "xbt_legacy",
    user_sub: "1234567890",
  });
  const out = await readStoredAuth(storage);
  assert.equal(out.xbt_token, "xbt_legacy");
  assert.equal(out.user_sub, "1234567890");
  assert.equal(out.api_token_id, null);
});

// ---------- 3. readStoredAuth: local wins ----------

await test("readStoredAuth prefers local over session", async () => {
  const storage = { local: makeStorageArea(), session: makeStorageArea() };
  await storage.session.set({
    xbt_token: "xbt_old",
    user_sub: "old-sub",
  });
  await storage.local.set({
    xbt_token: "xbt_new",
    user_sub: "new-sub",
    api_token_id: "abc-123",
  });
  const out = await readStoredAuth(storage);
  assert.equal(out.xbt_token, "xbt_new");
  assert.equal(out.user_sub, "new-sub");
  assert.equal(out.api_token_id, "abc-123");
});

// ---------- 4. mintAndConnect: happy path ----------

await test("mintAndConnect persists all 3 keys to storage on success", async () => {
  const storage = makeStorageArea();
  const fetchSpy = makeFetchSpy((url) => {
    if (url.endsWith("/v1/me/api-token")) {
      return makeJsonResponse({ id: "tok-7", token: "xbt_abc123" }, 201);
    }
    if (url.endsWith("/v1/me")) {
      return makeJsonResponse(
        {
          kind: "user",
          id: "user-42",
          source_user_id: "1234567890",
          email: "alice@example.com",
        },
        200,
      );
    }
    throw new Error("unexpected url " + url);
  });
  const out = await mintAndConnect({
    fetch: fetchSpy,
    getIdToken: async () => "google_id_token_xxx",
    storage,
  });
  assert.equal(out.ok, true);
  assert.equal(out.email, "alice@example.com");
  assert.equal(out.source_user_id, "1234567890");
  const stored = await storage.get(["xbt_token", "user_sub", "api_token_id"]);
  assert.equal(stored.xbt_token, "xbt_abc123");
  assert.equal(stored.user_sub, "1234567890");
  assert.equal(stored.api_token_id, "tok-7");
  // Two API calls expected: mint + /v1/me.
  assert.equal(fetchSpy.calls.length, 2);
  assert.match(fetchSpy.calls[0].url, /\/v1\/me\/api-token$/);
  assert.match(fetchSpy.calls[1].url, /\/v1\/me$/);
});

// ---------- 4b. mintAndConnect: idempotent on cached token ----------

await test(
  "mintAndConnect short-circuits when a valid token is already stored",
  async () => {
    const storage = makeStorageArea();
    await storage.set({
      xbt_token: "xbt_existing",
      user_sub: "1234567890",
      api_token_id: "tok-existing",
    });
    let mintCalled = false;
    const fetchSpy = makeFetchSpy((url) => {
      if (url.endsWith("/v1/me")) {
        return makeJsonResponse(
          {
            kind: "user_api_token",
            id: "user-42",
            source_user_id: "1234567890",
            email: "alice@example.com",
          },
          200,
        );
      }
      if (url.endsWith("/v1/me/api-token")) {
        mintCalled = true;
        throw new Error("mint should NOT be called when token is cached");
      }
      throw new Error("unexpected url " + url);
    });
    const out = await mintAndConnect({
      fetch: fetchSpy,
      getIdToken: async () => {
        throw new Error("getIdToken should NOT be called when token is cached");
      },
      storage,
    });
    assert.equal(out.ok, true);
    assert.equal(out.cached, true);
    assert.equal(out.email, "alice@example.com");
    assert.equal(out.source_user_id, "1234567890");
    assert.equal(mintCalled, false);
    assert.equal(fetchSpy.calls.length, 1); // only the /v1/me probe
  },
);

await test(
  "mintAndConnect re-mints when stored token is rejected by /v1/me",
  async () => {
    const storage = makeStorageArea();
    await storage.set({
      xbt_token: "xbt_stale",
      user_sub: "old-sub",
      api_token_id: "tok-stale",
    });
    let mintCalled = false;
    const fetchSpy = makeFetchSpy((url, init) => {
      if (url.endsWith("/v1/me")) {
        const auth = init && init.headers && init.headers.Authorization;
        if (auth === "Bearer xbt_stale") {
          // Stale token rejected
          return makeJsonResponse({ detail: "Unauthorized" }, 401);
        }
        // Fresh idToken accepted
        return makeJsonResponse(
          {
            kind: "user",
            id: "user-99",
            source_user_id: "new-sub",
            email: "bob@example.com",
          },
          200,
        );
      }
      if (url.endsWith("/v1/me/api-token")) {
        mintCalled = true;
        return makeJsonResponse({ id: "tok-fresh", token: "xbt_fresh" }, 201);
      }
      throw new Error("unexpected url " + url);
    });
    const out = await mintAndConnect({
      fetch: fetchSpy,
      getIdToken: async () => "id_token_fresh",
      storage,
    });
    assert.equal(out.ok, true);
    assert.equal(out.cached, undefined);
    assert.equal(out.email, "bob@example.com");
    assert.equal(out.source_user_id, "new-sub");
    assert.equal(mintCalled, true);
    const stored = await storage.get(["xbt_token", "user_sub", "api_token_id"]);
    assert.equal(stored.xbt_token, "xbt_fresh");
    assert.equal(stored.user_sub, "new-sub");
    assert.equal(stored.api_token_id, "tok-fresh");
  },
);

// ---------- 5. mintAndConnect: /v1/me failure → no partial write ----------

await test(
  "mintAndConnect returns ok:false without writing when /v1/me fails",
  async () => {
    const storage = makeStorageArea();
    const fetchSpy = makeFetchSpy((url) => {
      if (url.endsWith("/v1/me/api-token")) {
        return makeJsonResponse({ id: "tok-9", token: "xbt_xyz" }, 201);
      }
      if (url.endsWith("/v1/me")) {
        return makeJsonResponse({ detail: "internal" }, 500);
      }
      throw new Error("unexpected url " + url);
    });
    const out = await mintAndConnect({
      fetch: fetchSpy,
      getIdToken: async () => "id_token",
      storage,
    });
    assert.equal(out.ok, false);
    assert.match(out.error, /\/v1\/me failed: 500/);
    const stored = await storage.get([
      "xbt_token",
      "user_sub",
      "api_token_id",
    ]);
    // No partial state — the storage must be untouched.
    assert.equal(stored.xbt_token, undefined);
    assert.equal(stored.user_sub, undefined);
    assert.equal(stored.api_token_id, undefined);
  },
);

// ---------- 6. mintAndConnect: missing source_user_id ----------

await test(
  "mintAndConnect returns ok:false when source_user_id missing in /v1/me",
  async () => {
    const storage = makeStorageArea();
    const fetchSpy = makeFetchSpy((url) => {
      if (url.endsWith("/v1/me/api-token")) {
        return makeJsonResponse({ id: "tok-1", token: "xbt_aaa" }, 201);
      }
      if (url.endsWith("/v1/me")) {
        // Older /v1/me shape — no source_user_id.
        return makeJsonResponse({ kind: "user", id: "u1" }, 200);
      }
      throw new Error("unexpected url " + url);
    });
    const out = await mintAndConnect({
      fetch: fetchSpy,
      getIdToken: async () => "id_token",
      storage,
    });
    assert.equal(out.ok, false);
    assert.match(out.error, /source_user_id missing/);
    const stored = await storage.get(["xbt_token"]);
    assert.equal(stored.xbt_token, undefined);
  },
);

// ---------- 7. disconnectAuth: happy path ----------

await test(
  "disconnectAuth revokes API token and clears both storage tiers",
  async () => {
    const local = makeStorageArea();
    const session = makeStorageArea();
    await local.set({
      xbt_token: "xbt_abc123",
      user_sub: "1234567890",
      api_token_id: "tok-7",
    });
    await session.set({
      xbt_token: "xbt_legacy",
      user_sub: "old-sub",
    });
    const fetchSpy = makeFetchSpy(() => makeJsonResponse({}, 204));
    let closeWsCalled = false;
    const out = await disconnectAuth({
      fetch: fetchSpy,
      storage: local,
      sessionStorage: session,
      closeWs: () => {
        closeWsCalled = true;
      },
    });
    assert.equal(out.ok, true);
    assert.equal(closeWsCalled, true);
    // Exactly one DELETE call to /v1/me/api-token/{id}.
    assert.equal(fetchSpy.calls.length, 1);
    assert.equal(fetchSpy.calls[0].init.method, "DELETE");
    assert.match(fetchSpy.calls[0].url, /\/v1\/me\/api-token\/tok-7$/);
    // Both tiers cleared.
    const after = await local.get(["xbt_token", "user_sub", "api_token_id"]);
    assert.equal(after.xbt_token, undefined);
    assert.equal(after.user_sub, undefined);
    assert.equal(after.api_token_id, undefined);
    const afterSession = await session.get(["xbt_token", "user_sub"]);
    assert.equal(afterSession.xbt_token, undefined);
    assert.equal(afterSession.user_sub, undefined);
  },
);

// ---------- 8. disconnectAuth: DELETE failure still clears storage ----------

await test(
  "disconnectAuth still clears storage when DELETE network call throws",
  async () => {
    const local = makeStorageArea();
    const session = makeStorageArea();
    await local.set({
      xbt_token: "xbt_abc",
      user_sub: "sub-1",
      api_token_id: "tok-1",
    });
    const fetchSpy = makeFetchSpy(() => {
      throw new Error("network down");
    });
    const out = await disconnectAuth({
      fetch: fetchSpy,
      storage: local,
      sessionStorage: session,
    });
    assert.equal(out.ok, true);
    const after = await local.get(["xbt_token", "user_sub", "api_token_id"]);
    assert.equal(after.xbt_token, undefined);
    assert.equal(after.user_sub, undefined);
    assert.equal(after.api_token_id, undefined);
  },
);

// ---------- summary ----------

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

// gate_client.mjs — the LIVE two-client convergence + team-scope rejection driver.
//
// This is Phase 26-07's headline proof, and it is deliberately NOT a `node:test` file:
// verify-phase26.sh runs it inside a container on the compose network and reads its exit
// code and its line-oriented PASS/FAIL output. It proves, against the REAL running
// Hocuspocus server (never a mock):
//
//   1. two independent Yjs clients converge on IDENTICAL document content (asserted on
//      the content via toJSON() equality, not merely "the socket opened");
//   2. the team-scope boundary holds in BOTH directions — a token minted for team A's
//      board is refused on team B's document and vice versa;
//   3-6. absent / malformed / expired / wrong-signature tokens are all refused;
//   7. a POSITIVE CONTROL authenticates, so the refusals are meaningful (without it,
//      checks 2-6 would also "pass" against a server that rejects everything — including
//      a server that is simply DOWN);
//   8. a fresh client reconnecting after the store debounce still sees the prior edits.
//
// CRITICAL — this driver MINTS NOTHING. Every token arrives through process.env, produced
// by the REAL memory-api Python minting code (mint_board_token). A Node-minted token would
// only prove Node agrees with itself; the CROSS-LANGUAGE agreement is the property under
// test. This file contains no JWT-signing / HMAC / token-library calls of any kind — it
// only reads tokens from the environment and hands them to the provider unchanged.
//
// CRITICAL — a TIMEOUT is scored as a FAILURE, never a pass. In every denial check we
// require the status to be exactly "denied": if the connection instead times out (status
// "timeout"), the server may simply be unreachable, in which case an "everything is
// refused" result would be a false green. The positive control (check 7) plus the
// timeout=FAIL rule together make the rejection matrix honest.
//
// @hocuspocus/provider 4.4.0 carries no BroadcastChannel path (verified), so the only way
// two in-process clients can converge is THROUGH the running server — there is no local
// shortcut that could fake convergence.

import * as Y from "yjs";
import { HocuspocusProvider } from "@hocuspocus/provider";

// ── env inputs (tokens are read, NEVER minted, NEVER printed) ──────────────────
const HOCUSPOCUS_URL = process.env.HOCUSPOCUS_URL || "ws://hocuspocus:8108";
const BOARD_A = process.env.BOARD_A;
const BOARD_B = process.env.BOARD_B;
const TOKEN_A = process.env.TOKEN_A;
const TOKEN_B = process.env.TOKEN_B;
const TOKEN_A_EXPIRED = process.env.TOKEN_A_EXPIRED;
const TOKEN_A_BADSIG = process.env.TOKEN_A_BADSIG;

// full = checks 1-8 (the main gate); verify-persisted = ONLY the persistence-read check,
// used by verify-phase26.sh after `docker compose restart hocuspocus` to prove the board
// content survived a real container restart.
const MODE = process.env.GATE_MODE || "full";

// The Hocuspocus server debounces stores (debounce 2s, maxDebounce 10s — server.mjs).
// A fresh client must wait PAST maxDebounce before reconnecting, or it might read back a
// document the server has not flushed to Postgres yet.
const STORE_DEBOUNCE_MS = 10000; // server.mjs maxDebounce
const PERSIST_WAIT_MS = 12000; // > STORE_DEBOUNCE_MS, so the store has definitely flushed
const CONNECT_TIMEOUT_MS = 10000; // a connection that neither authenticates nor is denied
const CONVERGE_TIMEOUT_MS = 15000; // poll to a deadline; never sleep-once

// The two elements the convergence check writes. Their ids are the stable persistence
// contract: verify-persisted reconnects (after a restart) and asserts BOTH are present.
const EL_1_ID = "e1";
const EL_2_ID = "e2";

// ── tiny PASS/FAIL harness ─────────────────────────────────────────────────────
let pass = 0;
let fail = 0;

async function check(name, fn) {
  try {
    await fn();
    pass += 1;
    console.log(`PASS ${name}`);
  } catch (err) {
    fail += 1;
    console.log(`FAIL ${name}: ${err && err.message ? err.message : err}`);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Open a provider for (boardId, token) and resolve once its fate is known.
 * @returns {Promise<{provider, ydoc, status}>} status ∈ "authenticated" | "denied" | "timeout".
 *   - "authenticated": the server accepted the token and the initial sync completed.
 *   - "denied":        onAuthenticationFailed fired (the server refused the token).
 *   - "timeout":       neither happened within CONNECT_TIMEOUT_MS — scored as a FAILURE by
 *                      every caller (a hung/unreachable server must never look like success).
 * The caller ALWAYS destroys the returned provider + ydoc in a finally.
 */
function connect(boardId, token) {
  const ydoc = new Y.Doc();
  let settled = false;
  let timer;
  const resolveRef = { fn: null };
  const promise = new Promise((resolve) => {
    resolveRef.fn = resolve;
  });

  const provider = new HocuspocusProvider({
    url: HOCUSPOCUS_URL,
    name: boardId,
    document: ydoc,
    token, // string or null — the provider forwards it verbatim; it is NOT minted here
    onAuthenticationFailed() {
      // Stop the reconnect loop so a rejected credential is not hammered at the socket.
      try {
        provider.disconnect();
      } catch {
        /* provider may already be tearing down */
      }
      finish("denied");
    },
    onSynced() {
      finish("authenticated");
    },
  });

  function finish(status) {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    resolveRef.fn({ provider, ydoc, status });
  }

  timer = setTimeout(() => finish("timeout"), CONNECT_TIMEOUT_MS);
  return promise;
}

function destroy(conn) {
  try {
    conn.provider.destroy();
  } catch {
    /* ignore */
  }
  try {
    conn.ydoc.destroy();
  } catch {
    /* ignore */
  }
}

// A denial check: the status MUST be exactly "denied". A "timeout" is a FAIL (the server
// might be unreachable) — that distinction is what keeps the rejection matrix honest.
async function expectDenied(name, boardId, token) {
  await check(name, async () => {
    const conn = await connect(boardId, token);
    try {
      if (conn.status !== "denied") {
        const extra =
          conn.status === "timeout"
            ? " (a timeout is a FAILURE, not a pass — the server may be unreachable)"
            : "";
        throw new Error(`expected "denied", got "${conn.status}"${extra}`);
      }
    } finally {
      destroy(conn);
    }
  });
}

function elementIds(ydoc) {
  return ydoc
    .getArray("elements")
    .toJSON()
    .map((e) => (e && typeof e === "object" ? e.id : e));
}

// Connect one fresh client and assert the two persisted elements have arrived. Used by
// check 8 (persistence handoff) and by GATE_MODE=verify-persisted (post-restart proof).
async function assertBothElementsPresent(name, token) {
  await check(name, async () => {
    const conn = await connect(BOARD_A, token);
    try {
      if (conn.status !== "authenticated") {
        throw new Error(`fresh client could not authenticate (status "${conn.status}")`);
      }
      const deadline = Date.now() + CONVERGE_TIMEOUT_MS;
      let ids = [];
      while (Date.now() < deadline) {
        ids = elementIds(conn.ydoc);
        if (ids.includes(EL_1_ID) && ids.includes(EL_2_ID)) break;
        await sleep(300);
      }
      if (!(ids.includes(EL_1_ID) && ids.includes(EL_2_ID))) {
        throw new Error(
          `fresh client did not receive the persisted elements; saw ids=${JSON.stringify(ids)}`,
        );
      }
    } finally {
      destroy(conn);
    }
  });
}

function requireEnv(names) {
  const missing = names.filter((n) => !process.env[n]);
  if (missing.length) {
    console.log(`FAIL gate-client env: missing required vars: ${missing.join(", ")}`);
    console.log(`GATE-CLIENT SUMMARY pass=0 fail=1`);
    process.exit(1);
  }
}

async function runVerifyPersisted() {
  // Post-restart read path only: a fresh client with a fresh (Python-minted) token must
  // still see BOTH elements written before the restart.
  requireEnv(["BOARD_A", "TOKEN_A"]);
  console.log(`gate-client mode=verify-persisted url=${HOCUSPOCUS_URL} board_a=${BOARD_A}`);
  await assertBothElementsPresent(
    "persistence-after-restart: fresh client still sees e1 and e2",
    TOKEN_A,
  );
}

async function runFull() {
  requireEnv([
    "BOARD_A",
    "BOARD_B",
    "TOKEN_A",
    "TOKEN_B",
    "TOKEN_A_EXPIRED",
    "TOKEN_A_BADSIG",
  ]);
  // Board ids are safe to print; tokens are NEVER printed (T-26-49).
  console.log(
    `gate-client mode=full url=${HOCUSPOCUS_URL} board_a=${BOARD_A} board_b=${BOARD_B}`,
  );

  // ── 1. CONVERGENCE (SC#1) — two clients on BOARD_A converge on identical content ──
  await check(
    "convergence: two clients on board A reach identical document content",
    async () => {
      const c1 = await connect(BOARD_A, TOKEN_A);
      const c2 = await connect(BOARD_A, TOKEN_A);
      try {
        if (c1.status !== "authenticated") {
          throw new Error(`client 1 did not authenticate (status "${c1.status}")`);
        }
        if (c2.status !== "authenticated") {
          throw new Error(`client 2 did not authenticate (status "${c2.status}")`);
        }
        // Same top-level type name the real binding uses (Board.tsx: getArray("elements")).
        c1.ydoc.getArray("elements").push([{ id: EL_1_ID, t: Date.now() }]);
        c2.ydoc.getArray("elements").push([{ id: EL_2_ID, t: Date.now() }]);

        // Poll to a deadline (never a single sleep) until both docs describe the same state.
        const deadline = Date.now() + CONVERGE_TIMEOUT_MS;
        let j1 = "";
        let j2 = "";
        let converged = false;
        while (Date.now() < deadline) {
          j1 = JSON.stringify(c1.ydoc.getArray("elements").toJSON());
          j2 = JSON.stringify(c2.ydoc.getArray("elements").toJSON());
          const ids1 = elementIds(c1.ydoc);
          const ids2 = elementIds(c2.ydoc);
          if (
            j1 === j2 &&
            ids1.includes(EL_1_ID) &&
            ids1.includes(EL_2_ID) &&
            ids2.includes(EL_1_ID) &&
            ids2.includes(EL_2_ID)
          ) {
            converged = true;
            break;
          }
          await sleep(300);
        }
        if (!converged) {
          throw new Error(
            `clients did not converge within ${CONVERGE_TIMEOUT_MS}ms; ` +
              `doc1=${j1} doc2=${j2}`,
          );
        }
        // THE assertion: identical CONTENT, both elements present in BOTH documents.
        if (j1 !== j2) {
          throw new Error(`toJSON mismatch after convergence: doc1=${j1} doc2=${j2}`);
        }
        const ids1 = elementIds(c1.ydoc).sort();
        const ids2 = elementIds(c2.ydoc).sort();
        if (!(ids1.includes(EL_1_ID) && ids1.includes(EL_2_ID))) {
          throw new Error(`doc1 is missing an element: ids=${JSON.stringify(ids1)}`);
        }
        if (JSON.stringify(ids1) !== JSON.stringify(ids2)) {
          throw new Error(
            `the two docs' element ids differ: ${JSON.stringify(ids1)} vs ${JSON.stringify(ids2)}`,
          );
        }
      } finally {
        destroy(c1);
        destroy(c2);
      }
    },
  );

  // ── 2. TEAM-SCOPE REJECTION (SC#2) — both directions ──────────────────────────
  await expectDenied(
    "team-scope: team A token is refused on team B's document (A-token / B-board)",
    BOARD_B,
    TOKEN_A,
  );
  await expectDenied(
    "team-scope: team B token is refused on team A's document (B-token / A-board)",
    BOARD_A,
    TOKEN_B,
  );

  // ── 3-6. absent / malformed / expired / wrong-signature ──────────────────────
  await expectDenied("no token: null credential is refused on board A", BOARD_A, null);
  await expectDenied(
    "malformed token: 'not.a.jwt' is refused on board A",
    BOARD_A,
    "not.a.jwt",
  );
  await expectDenied(
    "expired token: a real expired board-A token is refused",
    BOARD_A,
    TOKEN_A_EXPIRED,
  );
  await expectDenied(
    "wrong signature: a board-A token signed with the wrong secret is refused",
    BOARD_A,
    TOKEN_A_BADSIG,
  );

  // ── 7. POSITIVE CONTROL — a valid token MUST authenticate ────────────────────
  // This is the positive control that makes checks 2-6 meaningful: if the server rejected
  // everything (or were simply down), this check would FAIL and expose the false green.
  await check(
    "positive control: a valid team A token authenticates on board A",
    async () => {
      const conn = await connect(BOARD_A, TOKEN_A);
      try {
        if (conn.status !== "authenticated") {
          throw new Error(
            `the positive control did not authenticate (status "${conn.status}") — ` +
              `the rejection matrix above is therefore NOT trustworthy`,
          );
        }
      } finally {
        destroy(conn);
      }
    },
  );

  // ── 8. PERSISTENCE HANDOFF (SC#3, part 1) — a fresh client sees the prior edits ──
  // The convergence clients were destroyed above (disconnected). Wait past the store
  // debounce so the server has flushed to Postgres via memory-api, then connect a fresh
  // client and prove the fetch/store round-trip end to end.
  await check(
    `persistence handoff: after waiting ${PERSIST_WAIT_MS}ms (> ${STORE_DEBOUNCE_MS}ms debounce), a fresh client sees e1 and e2`,
    async () => {
      await sleep(PERSIST_WAIT_MS);
      const conn = await connect(BOARD_A, TOKEN_A);
      try {
        if (conn.status !== "authenticated") {
          throw new Error(`fresh client could not authenticate (status "${conn.status}")`);
        }
        const deadline = Date.now() + CONVERGE_TIMEOUT_MS;
        let ids = [];
        while (Date.now() < deadline) {
          ids = elementIds(conn.ydoc);
          if (ids.includes(EL_1_ID) && ids.includes(EL_2_ID)) break;
          await sleep(300);
        }
        if (!(ids.includes(EL_1_ID) && ids.includes(EL_2_ID))) {
          throw new Error(
            `fresh client did not receive the persisted elements; saw ids=${JSON.stringify(ids)}`,
          );
        }
      } finally {
        destroy(conn);
      }
    },
  );
}

async function main() {
  if (MODE === "verify-persisted") {
    await runVerifyPersisted();
  } else {
    await runFull();
  }

  console.log(`GATE-CLIENT SUMMARY pass=${pass} fail=${fail}`);
  // Exit non-zero if anything failed OR if nothing was asserted at all: a driver that
  // asserts nothing must NEVER report success (T-26-43).
  if (fail > 0 || pass === 0) {
    process.exit(1);
  }
  process.exit(0);
}

main().catch((err) => {
  console.log(`FAIL gate-client crashed: ${err && err.stack ? err.stack : err}`);
  console.log(`GATE-CLIENT SUMMARY pass=${pass} fail=${fail + 1}`);
  process.exit(1);
});

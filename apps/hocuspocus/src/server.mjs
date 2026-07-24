// xbrain-hocuspocus — the single-process collaborative-board Yjs server.
//
// It is its OWN process on purpose: memory-api runs UVICORN_WORKERS=2, so an in-FastAPI
// Y.Doc would exist twice and two clients would silently never sync. It holds NO database
// credentials: persistence goes through memory-api's bridge-authenticated internal doc
// endpoints (persistence.mjs), so team-scope decisions stay in the one component that knows
// team membership.
//
// onAuthenticate is the phase's load-bearing security boundary — it runs the real
// claim-vs-document verifier (auth.mjs). This is a NEW internet-facing surface, so the DoS
// ceilings below (idle timeout, maxPendingDocuments, maxPayload) are part of the
// deliverable, not tuning.

import { Server } from "@hocuspocus/server";

import { verifyBoardToken, BoardAuthError } from "./auth.mjs";
import { makeDatabaseExtension } from "./persistence.mjs";

// --- Config from env, with the same defaults / knob names memory-api uses ---
const MEMORY_API_URL = process.env.MEMORY_API_URL || "http://memory-api:8000";
const SECRET = process.env.BRIDGE_SHARED_SECRET;
const PORT = Number(process.env.HOCUSPOCUS_PORT || 8108);
// Same knob name + meaning as memory-api's BOARD_MAX_DOC_BYTES (default 16 MiB).
const MAX_DOC_BYTES = Number(process.env.BOARD_MAX_DOC_BYTES || 16777216);

// An empty secret is fatal at boot, never tolerated: a server that started with no secret
// would "verify" nothing and accept every forged token. Fail loud, fail closed.
if (!SECRET) {
  console.error(
    "FATAL: BRIDGE_SHARED_SECRET is empty. The board token cannot be verified without it. Refusing to start.",
  );
  process.exit(1);
}

const server = new Server({
  name: "xbrain-hocuspocus",
  address: "0.0.0.0",
  port: PORT,
  quiet: true, // respect the 100 MB per-container log cap; we log rejections ourselves
  timeout: 30000, // reap idle connections (default 60000)
  maxPendingDocuments: 1, // a legitimate client opens EXACTLY one board; the default 100
  // is a document-name enumeration budget we have no use for
  websocketOptions: { maxPayload: MAX_DOC_BYTES }, // default is UNSET = unbounded, a
  // trivial OOM vector on a 4 GB VM
  debounce: 2000,
  maxDebounce: 10000,
  // maxUnauthenticatedQueueSize (5 MiB) and maxUnauthenticatedQueueMessages (1000) are
  // LEFT AT THEIR DEFAULTS: they already cap what an unauthenticated socket can consume,
  // and restating them here would only create drift against the upstream defaults.
  extensions: [makeDatabaseExtension({ memoryApiUrl: MEMORY_API_URL })],

  // THE TEAM-SCOPE BOUNDARY. verifyBoardToken throws on a bad signature, an expired token,
  // the wrong scope, board_id !== documentName, or a missing team_scope; a throw terminates
  // the connection. team_scope is read only from the verified claim.
  async onAuthenticate({ token, documentName, connectionConfig }) {
    try {
      const claims = await verifyBoardToken(token, documentName, SECRET);
      // Hocuspocus 4.4.0 passes the per-connection config as `connectionConfig`
      // (the onAuthenticate payload has NO `connection` field). Setting
      // connectionConfig.readOnly is how a view-only board connection is issued.
      // CR LOW: fail CLOSED, not open — if a token is read_only but we cannot set the
      // flag (a future upstream rename drops connectionConfig), DENY rather than silently
      // grant a writable connection. A writable token only needs the flag cleared when present.
      if (claims.read_only === true) {
        if (!connectionConfig) throw new Error("readonly-unenforceable");
        connectionConfig.readOnly = true;
      } else if (connectionConfig) {
        connectionConfig.readOnly = false;
      }
      return {
        user: {
          id: claims.sub,
          name: claims.display_name || "",
          team: claims.team_scope,
        },
      };
    } catch (err) {
      // One structured rejection line: documentName + a reason CODE only. NEVER log the
      // token or any part of it — that would defeat the fragment-only handoff (T-26-22).
      const reason = err instanceof BoardAuthError ? "denied" : "error";
      console.error(
        JSON.stringify({ event: "board_auth_rejected", documentName, reason }),
      );
      throw err; // re-throw so Hocuspocus closes the connection
    }
  },
});

// Flush pending debounced stores on shutdown. server.destroy() drains them, which is what
// makes "restart and the board still has its content" a property rather than luck.
let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.error(JSON.stringify({ event: "board_server_shutdown", signal }));
  try {
    await server.destroy();
  } finally {
    process.exit(0);
  }
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

server.listen();
console.error(
  JSON.stringify({ event: "board_server_listening", port: PORT, memoryApiUrl: MEMORY_API_URL }),
);

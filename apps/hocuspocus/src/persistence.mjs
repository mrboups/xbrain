// Postgres persistence for board Y.Docs — routed THROUGH memory-api, never around it.
//
// The board server holds NO database credentials. @hocuspocus/extension-database takes two
// async callbacks; both call memory-api's bridge-authenticated internal doc endpoints
// (26-02) so the one component that resolves a board to a team stays in charge of storage.
//
//   GET  /v1/internal/boards/{id}/doc -> 200 application/octet-stream (raw bytea) | 404
//   PUT  /v1/internal/boards/{id}/doc -> 204 | 400 empty | 404 unknown board | 413 too large
//
// Byte-for-byte discipline (Pitfall 9 / T-26-21): `state` arrives from Hocuspocus ALREADY
// COMPACTED. Store it verbatim and hand `fetch` the exact bytes back as a Uint8Array —
// never accumulate an append log, never JSON/base64-re-encode, and never construct a new
// Y.Doc here. Returning different bytes than were stored silently corrupts history.

import { Database } from "@hocuspocus/extension-database";

import { bridgeAuthHeader } from "./bridge.mjs";

// A canonical lowercase UUIDv4 — exactly what Postgres gen_random_uuid() mints for a board
// id. `documentName` is interpolated into a URL, so it is validated in BOTH callbacks
// BEFORE any request: this closes document-name injection / path traversal at the HTTP
// boundary as well as at the auth boundary (T-26-19). The auth gate already requires
// board_id === documentName, but persistence must not trust that alone.
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function assertUuid(documentName) {
  if (typeof documentName !== "string" || !UUID_V4.test(documentName)) {
    throw new Error("invalid board document name");
  }
}

/**
 * Build the configured @hocuspocus/extension-database instance.
 * @param {{ memoryApiUrl: string }} opts
 * @returns {import("@hocuspocus/extension-database").Database}
 */
export function makeDatabaseExtension({ memoryApiUrl }) {
  const docUrl = (documentName) =>
    `${memoryApiUrl}/v1/internal/boards/${documentName}/doc`;

  return new Database({
    // Load the stored doc on first open. 404 = brand-new board -> return null so Hocuspocus
    // starts an empty Y.Doc. Any other non-OK status throws (the extension retries).
    fetch: async ({ documentName }) => {
      assertUuid(documentName);
      const r = await fetch(docUrl(documentName), {
        headers: await bridgeAuthHeader(),
      });
      if (r.status === 404) return null; // brand-new board
      if (!r.ok) throw new Error(`board doc fetch ${r.status}`);
      return new Uint8Array(await r.arrayBuffer()); // EXACTLY the stored bytes
    },

    // Write the debounced, already-compacted state back verbatim as octet-stream. THROW on
    // any non-OK response: a thrown error makes the extension retry, whereas a swallowed
    // error loses the write silently.
    store: async ({ documentName, state }) => {
      assertUuid(documentName);
      const r = await fetch(docUrl(documentName), {
        method: "PUT",
        body: state,
        headers: {
          ...(await bridgeAuthHeader()),
          "Content-Type": "application/octet-stream",
        },
      });
      if (!r.ok) throw new Error(`board doc store ${r.status}`);
    },
  });
}

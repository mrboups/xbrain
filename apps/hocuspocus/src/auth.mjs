// Board-token verifier — the phase's load-bearing security boundary.
//
// This module mirrors apps/memory-api/app/routes/board_helpers.py:verify_board_token
// (the Python minter/verifier). The two MUST be changed together: the claim key names,
// the algorithm (HS256), and the signing secret (BRIDGE_SHARED_SECRET) are a frozen
// cross-plan contract (see 26-02-SUMMARY.md). A drift on either side is a silent auth
// failure at runtime.
//
// Claim set as minted by mint_board_token (exact keys):
//   { scope: "board", board_id, team_scope, team_id, sub, display_name, read_only,
//     iat, exp }
//
// team_scope is READ FROM THE VERIFIED CLAIM and NEVER from a query parameter, a
// request header, or the document name. It is the only cross-team leak vector, so it
// is closed at construction: the socket learns a team only from a signature it checked.

import { jwtVerify } from "jose";

const enc = new TextEncoder();

/** Thrown for every rejection; carries only the generic DENY message. */
export class BoardAuthError extends Error {}

// ONE message for every failure mode. A verbose error is an oracle: it would tell a
// prober whether a board id exists, whether their token was merely expired, or which
// claim was wrong. Log the detail server-side, return this to the client.
export const DENY = "Not authorized!";

/**
 * Verify a board token for exactly the document it names.
 *
 * @param {unknown} token         The raw JWT string from the Hocuspocus Auth message.
 * @param {unknown} documentName  The board id (UUID) the client is opening.
 * @param {unknown} secret        BRIDGE_SHARED_SECRET — the HS256 signing key.
 * @returns {Promise<object>}     The validated claim set on success.
 * @throws {BoardAuthError}       On any failure, with the single generic DENY message.
 */
export async function verifyBoardToken(token, documentName, secret) {
  if (!token || typeof token !== "string") throw new BoardAuthError(DENY);
  if (!documentName || typeof documentName !== "string") throw new BoardAuthError(DENY);
  if (!secret) throw new BoardAuthError(DENY); // never verify against an empty secret

  let payload;
  try {
    // algorithms is pinned: without it a token with alg "none" or an asymmetric alg
    // could be accepted (algorithm-confusion). jose validates exp/nbf here too, so an
    // expired or not-yet-valid token throws before any claim is trusted.
    ({ payload } = await jwtVerify(token, enc.encode(secret), { algorithms: ["HS256"] }));
  } catch {
    throw new BoardAuthError(DENY);
  }

  if (payload.scope !== "board") throw new BoardAuthError(DENY);

  // *** THE TEAM-SCOPE BOUNDARY ***
  // A token is valid for exactly the document it names. Without this comparison a token
  // minted for team A's board opens team B's board (boards belong to teams). Do not
  // "optimise" it away.
  if (payload.board_id !== documentName) throw new BoardAuthError(DENY);

  if (!payload.team_scope) throw new BoardAuthError(DENY);

  return payload;
}

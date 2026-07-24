// Bridge-principal JWT minter for the memory-api internal doc endpoints.
//
// memory-api's GET/PUT /v1/internal/boards/{id}/doc accept a bridge JWT in
// `Authorization: Bearer <jwt>` — signed HS256 with BRIDGE_SHARED_SECRET and carrying
// `scope: "bridge"` (memory-api's _require_bridge_principal resolves it to a kind=bridge
// principal; it validates only signature + expiry + scope=="bridge"). See 26-02-SUMMARY.md.
//
// This is the SAME secret verifyBoardToken uses, but a DIFFERENT scope: a board token
// (scope=board) authorises a client socket; a bridge token (scope=bridge) authorises this
// service's own calls to memory-api. Do not conflate them.
//
// The token is cached in-process and re-minted only when it is near expiry — minting one
// per request would burn CPU and clock-skew risk for no benefit.

import { SignJWT } from "jose";

const enc = new TextEncoder();

const TTL_S = 120; // short-lived: a leaked bridge token expires within ~2 minutes
const REMINT_SKEW_S = 30; // re-mint once we are within 30 s of expiry

let cachedToken = null;
let cachedExp = 0; // unix seconds — the cached token's `exp`

function secret() {
  const s = process.env.BRIDGE_SHARED_SECRET;
  if (!s) {
    // Defence in depth: server.mjs already exits at boot on an empty secret, but never
    // mint against an empty key even if that guard is ever bypassed.
    throw new Error("BRIDGE_SHARED_SECRET is empty; refusing to mint a bridge token");
  }
  return s;
}

async function mintBridgeToken() {
  const now = Math.floor(Date.now() / 1000);
  const exp = now + TTL_S;
  const token = await new SignJWT({
    iss: "xbrain-hocuspocus",
    sub: "hocuspocus",
    scope: "bridge",
  })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt(now)
    .setExpirationTime(exp)
    .sign(enc.encode(secret()));
  cachedToken = token;
  cachedExp = exp;
  return token;
}

/**
 * Return the current bridge token, re-minting if absent or within REMINT_SKEW_S of expiry.
 * @returns {Promise<string>}
 */
export async function getBridgeToken() {
  const now = Math.floor(Date.now() / 1000);
  if (!cachedToken || now >= cachedExp - REMINT_SKEW_S) {
    await mintBridgeToken();
  }
  return cachedToken;
}

/**
 * The Authorization header the internal doc endpoints expect.
 * @returns {Promise<{ Authorization: string }>}
 */
export async function bridgeAuthHeader() {
  return { Authorization: `Bearer ${await getBridgeToken()}` };
}

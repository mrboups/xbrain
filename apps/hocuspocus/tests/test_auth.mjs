// The rejection matrix for verifyBoardToken, run against the REAL verifier with REAL
// HS256 tokens minted in-test with jose's SignJWT. Nothing here is simulated: the tokens
// are genuine JWTs and the verifier under test is the production one.
//
// The tokens are minted the same way apps/memory-api mints them (HS256 over the shared
// secret, the board-token claim shape). The cross-LANGUAGE live proof (tokens minted by
// the real Python code) is plan 26-07; this file proves the Node verifier's own logic.

import { test } from "node:test";
import assert from "node:assert/strict";
import { SignJWT } from "jose";

import { verifyBoardToken, BoardAuthError, DENY } from "../src/auth.mjs";

const SECRET = "test-bridge-secret-do-not-use-in-prod";
const OTHER_SECRET = "a-completely-different-secret-value";
const enc = new TextEncoder();

const BOARD_X = "11111111-1111-4111-8111-111111111111";
const BOARD_Y = "22222222-2222-4222-8222-222222222222";

// Mint a board token exactly as memory-api's mint_board_token would: HS256, the frozen
// claim set, secret = BRIDGE_SHARED_SECRET. Overrides let each case bend one field.
async function mintBoardToken(overrides = {}, secret = SECRET) {
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    scope: "board",
    board_id: BOARD_X,
    team_scope: "team-alpha",
    team_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    sub: "user-1",
    display_name: "Alice",
    read_only: false,
    ...overrides,
  };
  const iat = overrides.iat ?? now;
  const exp = overrides.exp ?? now + 3600;
  delete claims.iat;
  delete claims.exp;
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt(iat)
    .setExpirationTime(exp)
    .sign(enc.encode(secret));
}

// Every rejection MUST throw BoardAuthError whose message is EXACTLY DENY — so no future
// change can reintroduce an oracle. Returns nothing; asserts on failure.
async function assertDenied(promise) {
  await assert.rejects(promise, (err) => {
    assert.ok(err instanceof BoardAuthError, `expected BoardAuthError, got ${err?.constructor?.name}`);
    assert.equal(err.message, DENY, "rejection message must be the single generic DENY string");
    return true;
  });
}

// a. valid token for board X against documentName X -> resolves with the minted claims.
test("valid board-X token against document X resolves with its claims", async () => {
  const token = await mintBoardToken();
  const payload = await verifyBoardToken(token, BOARD_X, SECRET);
  assert.equal(payload.team_scope, "team-alpha");
  assert.equal(payload.board_id, BOARD_X);
  assert.equal(payload.scope, "board");
});

// b. TEAM-SCOPE: a token minted for team A's board X, used against team B's board Y.
test("TEAM-SCOPE: board-X token used against board Y is rejected", async () => {
  // Minted for team-alpha's board X — a legitimately-signed token.
  const token = await mintBoardToken({ board_id: BOARD_X, team_scope: "team-alpha" });
  // Presented against a DIFFERENT board (team B's document). This is the cross-team leak
  // vector: without board_id === documentName it would succeed.
  await assertDenied(verifyBoardToken(token, BOARD_Y, SECRET));
});

// c. no token.
test("absent token (undefined / empty / null) is rejected", async () => {
  await assertDenied(verifyBoardToken(undefined, BOARD_X, SECRET));
  await assertDenied(verifyBoardToken("", BOARD_X, SECRET));
  await assertDenied(verifyBoardToken(null, BOARD_X, SECRET));
});

// d. malformed token.
test("malformed token is rejected", async () => {
  await assertDenied(verifyBoardToken("not.a.jwt", BOARD_X, SECRET));
  await assertDenied(verifyBoardToken("abc", BOARD_X, SECRET));
});

// e. expired token (exp in the past).
test("expired token is rejected", async () => {
  const past = Math.floor(Date.now() / 1000) - 10;
  const token = await mintBoardToken({ iat: past - 3600, exp: past });
  await assertDenied(verifyBoardToken(token, BOARD_X, SECRET));
});

// f. wrong-signature token (minted with a different secret).
test("wrong-signature token (different secret) is rejected", async () => {
  const token = await mintBoardToken({}, OTHER_SECRET);
  await assertDenied(verifyBoardToken(token, BOARD_X, SECRET));
});

// g. wrong scope.
test("wrong scope (scope=media) is rejected", async () => {
  const token = await mintBoardToken({ scope: "media" });
  await assertDenied(verifyBoardToken(token, BOARD_X, SECRET));
});

// h. missing team_scope claim.
test("missing team_scope claim is rejected", async () => {
  const token = await mintBoardToken({ team_scope: "" });
  await assertDenied(verifyBoardToken(token, BOARD_X, SECRET));
});

// i. alg-confusion attempt: a token whose header alg is "none", crafted by base64url-
// joining a header/payload with an empty signature.
test("alg:none token is rejected (algorithm confusion)", async () => {
  const b64url = (obj) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  const header = b64url({ alg: "none", typ: "JWT" });
  const now = Math.floor(Date.now() / 1000);
  const body = b64url({
    scope: "board",
    board_id: BOARD_X,
    team_scope: "team-alpha",
    team_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    sub: "user-1",
    display_name: "Alice",
    read_only: false,
    iat: now,
    exp: now + 3600,
  });
  const algNoneToken = `${header}.${body}.`; // empty signature
  await assertDenied(verifyBoardToken(algNoneToken, BOARD_X, SECRET));
});

// j. every rejection in b-i produces the IDENTICAL message. assertDenied already asserts
// message === DENY on each case above; this test makes that invariant explicit and would
// fail loudly if a per-case message were ever introduced. Each case is invoked and
// awaited in turn (never as a pre-built array of pending rejections) so no rejection
// escapes its await.
test("every rejection produces the identical DENY message (no oracle)", async () => {
  const past = Math.floor(Date.now() / 1000) - 10;
  // Thunks: nothing is invoked until we await it inside the loop, one at a time.
  const cases = [
    async () => verifyBoardToken(await mintBoardToken({ board_id: BOARD_X }), BOARD_Y, SECRET), // cross-team
    () => verifyBoardToken("", BOARD_X, SECRET), // absent
    () => verifyBoardToken("not.a.jwt", BOARD_X, SECRET), // malformed
    async () => verifyBoardToken(await mintBoardToken({ iat: past - 3600, exp: past }), BOARD_X, SECRET), // expired
    async () => verifyBoardToken(await mintBoardToken({}, OTHER_SECRET), BOARD_X, SECRET), // wrong sig
    async () => verifyBoardToken(await mintBoardToken({ scope: "media" }), BOARD_X, SECRET), // wrong scope
    async () => verifyBoardToken(await mintBoardToken({ team_scope: "" }), BOARD_X, SECRET), // no team_scope
  ];
  const messages = new Set();
  for (const run of cases) {
    try {
      await run();
      assert.fail("expected rejection");
    } catch (err) {
      assert.ok(err instanceof BoardAuthError, `expected BoardAuthError, got ${err?.constructor?.name}`);
      messages.add(err.message);
    }
  }
  assert.deepEqual([...messages], [DENY], "all rejections must share the one DENY message");
});

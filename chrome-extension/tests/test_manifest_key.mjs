/**
 * Phase 12 Plan 12-08 — manifest "key" → chrome.runtime.id derivation test.
 *
 * Re-derives the deterministic Chrome extension ID from the manifest's
 * base64-encoded DER public key and asserts it matches the value registered
 * with the GitHub App's callback URL list
 * (https://anigikcnmldoklcmogffmgcojdhhficb.chromiumapp.org/).
 *
 * Derivation formula (per Chrome docs):
 *   1. Decode the manifest "key" field as base64 → raw DER public key bytes.
 *   2. SHA-256 the DER bytes.
 *   3. Take the first 32 hex chars (16 bytes) of the digest.
 *   4. Map [0-9a-f] → [a-p] (digit-to-letter alphabet shift).
 *
 * If this test ever fails:
 *   - Most likely: the "key" field in manifest.json was rotated.
 *     EITHER restore the previous key (preferred — preserves all
 *     installed users' identities) OR also update the GitHub App's
 *     callback URL list to the new derived ID AND tell every dev
 *     to reload the unpacked extension.
 *   - Less likely: the key value was edited by hand and now has stray
 *     whitespace or line-breaks. Re-paste the single-line base64.
 *
 * See: .planning/KB/chrome-extension-key.md
 */

import assert from "node:assert/strict";
import crypto from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const manifestPath = join(__dirname, "..", "manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

const EXPECTED_ID = "anigikcnmldoklcmogffmgcojdhhficb";

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

test('manifest.json has "key" field', () => {
  assert.ok(
    typeof manifest.key === "string" && manifest.key.length > 0,
    'manifest.key must be a non-empty string',
  );
});

test("key value is base64 DER (392 chars, base64 alphabet only)", () => {
  // Expected RSA 2048-bit DER pub key is 294 raw bytes → 392 base64 chars.
  assert.equal(
    manifest.key.length,
    392,
    `expected 392 chars for RSA 2048 DER pub key, got ${manifest.key.length}`,
  );
  assert.match(
    manifest.key,
    /^[A-Za-z0-9+/=]+$/,
    "key contains non-base64 characters (whitespace? line break? non-ASCII?)",
  );
});

test(`derived chrome.runtime.id == ${EXPECTED_ID}`, () => {
  // Step 1: decode base64 → DER bytes.
  const der = Buffer.from(manifest.key, "base64");
  // Step 2: SHA-256 the DER.
  const hash = crypto.createHash("sha256").update(der).digest("hex");
  // Step 3: first 32 hex chars (16 bytes worth).
  const first32 = hash.slice(0, 32);
  // Step 4: digit-to-letter mapping [0-9a-f] → [a-p].
  // NOTE: a naive charCode shift breaks because '9'(0x39) and 'a'(0x61)
  // aren't adjacent in ASCII. Use an explicit table for safety.
  const MAP = {
    "0": "a", "1": "b", "2": "c", "3": "d",
    "4": "e", "5": "f", "6": "g", "7": "h",
    "8": "i", "9": "j", "a": "k", "b": "l",
    "c": "m", "d": "n", "e": "o", "f": "p",
  };
  const id = first32.split("").map((c) => MAP[c]).join("");
  assert.equal(
    id,
    EXPECTED_ID,
    `derived ID '${id}' does not match registered ID '${EXPECTED_ID}'`,
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);

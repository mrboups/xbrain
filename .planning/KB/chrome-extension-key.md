# Chrome extension stable-ID key

**Phase 12, Plan 12-08** makes the unpacked Chrome extension's runtime ID
deterministic by embedding a `"key"` field in `chrome-extension/manifest.json`.
This is required so the GitHub App's registered callback URL
(`https://<ID>.chromiumapp.org/`) matches every dev/test install across
machines without per-developer App reconfiguration.

## Current values (recorded 2026-05-17 operator prep)

| Output | Value | Where it lives |
|--------|-------|----------------|
| Deterministic `chrome.runtime.id` | `anigikcnmldoklcmogffmgcojdhhficb` | GitHub App "Callback URLs" list (NOT committed) |
| Base64-encoded DER public key (392 chars) | `MIIBIjAN...QIDAQAB` (full value in `chrome-extension/manifest.json` `key` field) | `chrome-extension/manifest.json` (committed) |
| Private RSA PEM (2048-bit) | NEVER COMMIT | `~/.config/xbrain/secrets/chrome-ext-key.pem` (operator's encrypted secret store) |

## Generation procedure (one-off; do NOT re-run unless the key was lost)

Run OUTSIDE the repo so the private PEM never lands in working tree:

```bash
mkdir -p ~/xbrain-secrets

# Generate 2048-bit RSA private key — KEEP THIS PRIVATE
openssl genrsa -out ~/xbrain-secrets/chrome_ext_private.pem 2048

# Step A — extract base64-encoded DER public key (single line, no whitespace)
#         This is the value for manifest.json "key"
openssl rsa -in ~/xbrain-secrets/chrome_ext_private.pem -pubout -outform DER 2>/dev/null \
  | base64 -w 0
# → Outputs ~392 chars. SAVE for manifest.json.

# Step B — compute the resulting chrome.runtime.id (deterministic)
openssl rsa -in ~/xbrain-secrets/chrome_ext_private.pem -pubout -outform DER 2>/dev/null \
  | sha256sum \
  | cut -c1-32 \
  | tr '0-9a-f' 'a-p'
# → Outputs a 32-char a-p string. SAVE for the GitHub App callback URL.
```

## Where the values live

| Output | Where | Committed? |
|--------|-------|------------|
| base64 pub key | `chrome-extension/manifest.json` `"key"` field | YES |
| extension ID | GitHub App "Callback URL" list at https://github.com/settings/apps/xbrain → `https://anigikcnmldoklcmogffmgcojdhhficb.chromiumapp.org/` | NO (GitHub App settings only) |
| private PEM | `~/.config/xbrain/secrets/chrome-ext-key.pem` (operator-only path) | NO (gitignored — `*.pem` at repo root + `chrome-extension/.gitignore`) |

## Verification

Two layers of verification — automated + manual smoke.

### Automated (runs in CI / `node tests/run_tests.mjs`)

```bash
node chrome-extension/tests/test_manifest_key.mjs
# Expected output:
#   PASS: manifest.json has "key" field
#   PASS: key value is base64 DER (392 chars)
#   PASS: derived chrome.runtime.id == anigikcnmldoklcmogffmgcojdhhficb
```

The test re-derives the ID from the committed manifest key using Node's
`crypto.createHash('sha256')` (no shell, no openssl). If the manifest
key is ever rotated, this test fails until the expected ID constant is
updated — intentional belt-and-braces so an accidental rotation can't
silently break sign-in.

### Manual smoke (after loading the unpacked extension in Chrome)

Open the extension's service worker DevTools and run:

```javascript
chrome.runtime.id
// → must equal "anigikcnmldoklcmogffmgcojdhhficb"
```

If they don't match: the base64 in manifest is wrong (whitespace,
line-break, or a newer key was generated). Re-run Step A and paste
exactly the single-line output (no surrounding whitespace).

## When to rotate

- **NEVER** as long as the unpacked extension is in use; rotating
  breaks every installed instance's chromiumapp.org callback and
  invalidates the GitHub App's registered Callback URL.
- **ONLY** when migrating to Chrome Web Store publication, where the
  Store assigns its own key that overrides this one. At that point
  the manifest `key` field becomes redundant; remove it on the
  Store-published version.

## Migration cost when rotating (for the future maintainer)

If rotation is forced (e.g. PEM leaked):

1. Generate new keypair (Steps A + B above).
2. Replace `key` in `manifest.json` with the new base64.
3. Update the expected ID constant in `chrome-extension/tests/test_manifest_key.mjs`.
4. Update this KB doc's "Current values" table.
5. Add the new `https://<NEW_ID>.chromiumapp.org/` URL to the GitHub App's
   Callback URLs list at https://github.com/settings/apps/xbrain.
6. Tell every installed user to reload their unpacked extension. Their
   `chrome.runtime.id` migrates automatically on the next Chrome restart.
7. Remove the OLD chromiumapp.org URL from the GitHub App settings AFTER
   all users have reloaded (otherwise their sign-in breaks).

## Private key role (informational)

The private PEM is needed exclusively for:
- Chrome Web Store publication (uploaded as the "upload key" binding
  the listing to this developer identity).
- Manual signing of CRX bundles for sideloading enterprise distribution
  (not relevant for xbrain v1).

For day-to-day unpacked development the private key is not used at all.

## References

- https://developer.chrome.com/docs/extensions/reference/manifest/key
- https://www.plasmo.com/blog/posts/how-to-create-a-consistent-id-for-your-chrome-extension
- 12-RESEARCH.md §Q6 (Chrome extension key generation formula)
- 12-08-PLAN.md (this plan)

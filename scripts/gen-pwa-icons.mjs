#!/usr/bin/env node
/**
 * Generate the PWA icons for app-site/app/ (Phase 27, Plan 27-05).
 *
 * Dependency-free ON PURPOSE. The dev machine is arm64 and production is amd64
 * (CLAUDE.md), so anything pulling a native image codec — sharp, canvas, an
 * ImageMagick binary — would either need two builds or fail on one of them.
 * Node ships zlib, and a PNG is just a signature plus three length-prefixed,
 * CRC-32'd chunks, so we encode by hand and the script runs anywhere node does.
 *
 * Determinism matters: these PNGs are committed, so re-running the script must
 * produce byte-identical files or every regeneration shows up as a diff. Fixed
 * artwork + a fixed deflate level gives that.
 *
 * Artwork: a #0A0A0A field with a centred #FAFAFA square — the same two brand
 * tokens the extension uses (--primary / --primary-fg in popup.css).
 *
 * Usage: node scripts/gen-pwa-icons.mjs
 */

import { deflateSync, constants as zlibConstants } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = join(REPO_ROOT, "app-site", "app", "icons");

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** Brand tokens, mirrored from chrome-extension/popup.css :root. */
const FIELD = { r: 0x0a, g: 0x0a, b: 0x0a }; // --primary  #0A0A0A
const GLYPH = { r: 0xfa, g: 0xfa, b: 0xfa }; // --primary-fg #FAFAFA

/** Precomputed CRC-32 table (PNG uses the standard IEEE polynomial). */
const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c;
  }
  return table;
})();

function crc32(buf) {
  let c = -1;
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ -1) >>> 0;
}

/** length(4) + type(4) + data + crc(4) over type+data. */
function chunk(type, data) {
  const typeBuf = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([length, typeBuf, data, crc]);
}

/**
 * Encode 8-bit RGBA pixels as a PNG.
 *
 * @param {number} width
 * @param {number} height
 * @param {Buffer} rgba width*height*4 bytes
 * @returns {Buffer}
 */
function encodePng(width, height, rgba) {
  // Each scanline is prefixed with its filter byte; 0 = None. Filtering would
  // shrink the file, but flat artwork compresses to nothing anyway and None
  // keeps the encoder small enough to audit at a glance.
  const stride = width * 4;
  const raw = Buffer.alloc(height * (1 + stride));
  for (let y = 0; y < height; y++) {
    const dst = y * (1 + stride);
    raw[dst] = 0;
    rgba.copy(raw, dst + 1, y * stride, (y + 1) * stride);
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type: truecolour with alpha
  ihdr[10] = 0; // compression: deflate
  ihdr[11] = 0; // filter method: adaptive
  ihdr[12] = 0; // interlace: none

  // Explicit level/strategy so the bytes do not drift with a zlib default change.
  const idat = deflateSync(raw, {
    level: 9,
    strategy: zlibConstants.Z_DEFAULT_STRATEGY,
  });

  return Buffer.concat([
    PNG_SIGNATURE,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/**
 * Paint the icon: a solid field with a centred square glyph.
 *
 * @param {number} size square canvas edge in px
 * @param {number} glyphFraction how much of the edge the glyph occupies (0-1)
 */
function paintIcon(size, glyphFraction) {
  const rgba = Buffer.alloc(size * size * 4);
  const glyph = Math.round(size * glyphFraction);
  const start = Math.round((size - glyph) / 2);
  const end = start + glyph;

  for (let y = 0; y < size; y++) {
    const inRow = y >= start && y < end;
    for (let x = 0; x < size; x++) {
      const c = inRow && x >= start && x < end ? GLYPH : FIELD;
      const i = (y * size + x) * 4;
      rgba[i] = c.r;
      rgba[i + 1] = c.g;
      rgba[i + 2] = c.b;
      rgba[i + 3] = 0xff; // fully opaque — a maskable icon may not be transparent
    }
  }
  return encodePng(size, size, rgba);
}

const ICONS = [
  // purpose "any": the glyph can run close to the edge, nothing crops it.
  { file: "icon-192.png", size: 192, glyphFraction: 0.7 },
  { file: "icon-512.png", size: 512, glyphFraction: 0.7 },
  // purpose "maskable": Android crops to a platform shape (circle, squircle,
  // teardrop). Only the middle ~80% is guaranteed visible, and a square
  // inscribed in that circle is ~57% of the edge — so 60% is the honest
  // ceiling here. A glyph sized like the "any" icons would lose its corners.
  { file: "icon-maskable-512.png", size: 512, glyphFraction: 0.6 },
];

mkdirSync(OUT_DIR, { recursive: true });
for (const icon of ICONS) {
  const png = paintIcon(icon.size, icon.glyphFraction);
  writeFileSync(join(OUT_DIR, icon.file), png);
  console.log(`wrote ${icon.file} (${icon.size}x${icon.size}, ${png.length} bytes)`);
}
console.log(`${ICONS.length} icon(s) written to app-site/app/icons/`);

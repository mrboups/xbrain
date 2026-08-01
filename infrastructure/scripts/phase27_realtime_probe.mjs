#!/usr/bin/env node
/**
 * phase27_realtime_probe.mjs — Phase 27 SC#3: a message ARRIVES at a second client.
 *
 * The claim under test is not "the socket connected" and not "posting a message
 * returned 201". Both of those are true on a system where realtime is completely
 * broken. The claim is: something a DIFFERENT client posted over plain HTTP shows
 * up, by content, on a socket that never asked for it — within seconds, without a
 * reload. That is the only statement worth gating on, so it is the only thing this
 * program can exit 0 on.
 *
 * Two clients, deliberately asymmetric:
 *   RECEIVER — a real Centrifugo connection (the vendored browser client, driven by
 *              node's built-in WebSocket), subscribed to team:<VERIFY_TEAM_ID>.
 *   SENDER   — plain `fetch` against POST /v1/teams/<id>/messages. No socket at all.
 * When VERIFY_XBT_TOKEN_2 is set the two are different accounts; when it is absent
 * they are two independent connections for the same account, which is still two
 * clients and still an arrival proof (it is simply a weaker statement about
 * cross-account fan-out, and the program says so in its output).
 *
 * THE SOCKET URL IS NEVER SUPPLIED BY THIS PROGRAM. It is read from the
 * POST /v1/me/centrifugo-token response and printed, so the log carries a URL the
 * probe could not have invented. There is deliberately no socket-scheme literal
 * anywhere in this file, and the plan's acceptance criteria grep for one.
 *
 * Ordering matters more than it looks: the publish is only issued AFTER the
 * receiver's `subscribed` event has fired. Centrifugo does not replay, so a publish
 * that raced ahead of the subscription would produce a confident false negative and
 * send someone hunting a bug that is not there.
 *
 * Exit codes:
 *   0  the nonce arrived on the receiver  (the only success)
 *   1  timeout, transport failure, or a missing runtime capability
 *   2  a required environment variable is missing (the name is printed)
 *
 * Usage:
 *   API_BASE=https://api.grooveos.app VERIFY_XBT_TOKEN=xbt_... VERIFY_TEAM_ID=<uuid> \
 *     node infrastructure/scripts/phase27_realtime_probe.mjs
 * Driven by infrastructure/scripts/verify-phase27.sh check (f).
 */

import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

// ---- Environment --------------------------------------------------------
// A missing variable is exit 2 with the NAME printed. The gate treats that as a
// FAILURE, never a skip: "we could not run the realtime proof" and "realtime is
// broken" are the same amount of evidence that it works.

const REQUIRED = ["API_BASE", "VERIFY_XBT_TOKEN", "VERIFY_TEAM_ID"];
const missing = REQUIRED.filter((name) => !(process.env[name] || "").trim());
if (missing.length > 0) {
  console.error(`phase27_realtime_probe: missing required environment variable(s): ${missing.join(", ")}`);
  console.error("  export API_BASE (e.g. the deployed API origin), VERIFY_XBT_TOKEN (a real xbt_ token)");
  console.error("  and VERIFY_TEAM_ID (a team UUID that token is a member of), then run again.");
  process.exit(2);
}

const API_BASE = process.env.API_BASE.trim().replace(/\/+$/, "");
const SENDER_TOKEN = process.env.VERIFY_XBT_TOKEN.trim();
const TEAM_ID = process.env.VERIFY_TEAM_ID.trim();
const RECEIVER_TOKEN = (process.env.VERIFY_XBT_TOKEN_2 || "").trim() || SENDER_TOKEN;
const TWO_ACCOUNTS = RECEIVER_TOKEN !== SENDER_TOKEN;

const ARRIVAL_TIMEOUT_MS = 15000;
const SUBSCRIBE_TIMEOUT_MS = 15000;

// ---- Runtime capability -------------------------------------------------
// The vendored client reaches for a global WebSocket and has no fallback. Node 22+
// ships one. On an older runtime this is a FAILURE with the reason named, never a
// skip — a gate that goes green because the tool could not run is the defect class
// this whole phase exists to close.

if (typeof globalThis.WebSocket !== "function") {
  console.error(`FAIL: this node runtime (${process.version}) has no global WebSocket.`);
  console.error("  The vendored Centrifugo client needs one. Run the probe on node 22 or newer.");
  console.error("  This is a FAILURE, not a skip: the arrival proof did not run.");
  process.exit(1);
}

// ---- The vendored Centrifugo client -------------------------------------
// Evaluated in THIS context rather than imported, so the file's module kind (it is a
// plain browser bundle that assigns globalThis.Centrifuge) never has to agree with
// whatever package.json happens to sit above it. No dependency is installed for this.

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const VENDOR = join(REPO_ROOT, "chrome-extension", "vendor", "centrifuge.js");

let Centrifuge = null;
try {
  vm.runInThisContext(readFileSync(VENDOR, "utf8"), { filename: VENDOR });
  Centrifuge = globalThis.Centrifuge;
} catch (e) {
  console.error(`FAIL: could not load the vendored Centrifugo client at ${VENDOR}: ${e.message}`);
  process.exit(1);
}
if (typeof Centrifuge !== "function") {
  console.error(`FAIL: ${VENDOR} did not define a Centrifuge constructor.`);
  process.exit(1);
}

// ---- HTTP ---------------------------------------------------------------

/** One authenticated call against the API. Throws with the status and body on refusal. */
async function apiCall(path, { method = "GET", token, body } = {}) {
  const headers = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`${method} ${path} -> HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${method} ${path} -> 200 but the body was not JSON: ${text.slice(0, 200)}`);
  }
}

// ---- The proof ----------------------------------------------------------

async function main() {
  const nonce = randomUUID();
  const frames = [];
  let handle = null;

  console.log("=== Phase 27 realtime probe (SC#3: arrival at a second client) ===");
  console.log(`  team:            ${TEAM_ID}`);
  console.log(`  nonce:           ${nonce}`);
  console.log(
    TWO_ACCOUNTS
      ? "  clients:         two DIFFERENT accounts (VERIFY_XBT_TOKEN_2 supplied)"
      : "  clients:         two independent connections for the SAME account " +
        "(VERIFY_XBT_TOKEN_2 not supplied) — still two clients, still an arrival proof, " +
        "but it does not exercise cross-account fan-out",
  );

  try {
    // 1. RECEIVER: mint a client token and take the socket URL from the RESPONSE.
    const tokenInfo = await apiCall("/v1/me/centrifugo-token", {
      method: "POST",
      token: RECEIVER_TOKEN,
    });
    if (!tokenInfo || typeof tokenInfo.ws_url !== "string" || tokenInfo.ws_url.length === 0) {
      throw new Error(
        "POST /v1/me/centrifugo-token returned no ws_url — the probe has no URL of its own to fall back on, by design",
      );
    }
    if (typeof tokenInfo.token !== "string" || tokenInfo.token.length === 0) {
      throw new Error("POST /v1/me/centrifugo-token returned no token");
    }
    // Printed so the log shows a socket URL the probe did not supply. The signed
    // client token is NEVER printed — it is a credential.
    console.log(`  socket url:      ${tokenInfo.ws_url}   (read from the API response)`);

    const centrifuge = new Centrifuge(tokenInfo.ws_url, { token: tokenInfo.token });
    handle = centrifuge;
    centrifuge.on("error", (err) => {
      console.warn(`  [receiver] transport error: ${JSON.stringify(err && err.error ? err.error : err)}`);
    });

    const channel = `team:${TEAM_ID}`;
    const subscription = centrifuge.newSubscription(channel);

    // The arrival promise is armed BEFORE the subscribe barrier, so a frame that
    // lands in the same tick as `subscribed` is still counted.
    let resolveArrival = null;
    const arrived = new Promise((resolve) => {
      resolveArrival = resolve;
    });
    subscription.on("publication", (ctx) => {
      const data = ctx && ctx.data;
      frames.push(data);
      const content =
        data && data.message && typeof data.message.content === "string" ? data.message.content : "";
      if (data && data.type === "message" && content.includes(nonce)) {
        resolveArrival(Date.now());
      }
    });

    // A publication can also be routed to the CLIENT rather than to the subscription
    // object, so arrival is watched on both paths. Whichever fires first resolves the
    // same promise; counting a frame twice is harmless, missing it is not.
    centrifuge.on("publication", (ctx) => {
      if (!ctx || ctx.channel !== channel) return;
      const data = ctx.data;
      frames.push(data);
      const content =
        data && data.message && typeof data.message.content === "string" ? data.message.content : "";
      if (data && data.type === "message" && content.includes(nonce)) {
        resolveArrival(Date.now());
      }
    });

    // 2. Subscribe barrier. Centrifugo does not replay: publishing before the
    //    subscription is live would look exactly like a broken realtime layer.
    //
    // The barrier accepts EITHER event, because which one fires depends on how this
    // deployment's connection token is shaped. `POST /v1/me/centrifugo-token` returns a
    // `channels` claim, which makes Centrifugo subscribe the connection SERVER-SIDE at
    // connect time; the channel is then already live when `subscription.subscribe()`
    // runs, so Centrifugo answers error 105 "already subscribed" and the subscription
    // object's own `subscribed` event never fires. Gating on that event alone timed out
    // against production on 2026-08-01 while realtime was in fact working — publications
    // were arriving on the subscription object the whole time. A barrier that reports a
    // healthy system as broken is as much a defect as one that reports the reverse, so
    // the client-level `subscribed` for THIS channel counts too, and error 105 is not
    // treated as a failure.
    const subscribed = new Promise((resolve, reject) => {
      subscription.on("subscribed", () => resolve());
      centrifuge.on("subscribed", (ctx) => {
        if (ctx && ctx.channel === channel) resolve();
      });
      subscription.on("error", (err) => {
        const e = err && err.error ? err.error : err;
        if (e && e.code === 105) {
          // Already subscribed server-side — the channel is live, which is what the
          // barrier is actually asking about.
          resolve();
          return;
        }
        reject(new Error(`subscribe to ${channel} failed: ${JSON.stringify(e)}`));
      });
      setTimeout(
        () => reject(new Error(`the receiver never became subscribed to ${channel} within ${SUBSCRIBE_TIMEOUT_MS}ms`)),
        SUBSCRIBE_TIMEOUT_MS,
      ).unref?.();
    });

    subscription.subscribe();
    centrifuge.connect();
    await subscribed;
    console.log(`  [receiver] subscribed to ${channel}`);

    // 3. SENDER: a genuinely different client — plain HTTP, no socket.
    const sentAt = Date.now();
    const posted = await apiCall(`/v1/teams/${TEAM_ID}/messages`, {
      method: "POST",
      token: SENDER_TOKEN,
      body: { content: `phase27-realtime-probe ${nonce}` },
    });
    console.log(`  [sender]   POST /v1/teams/${TEAM_ID}/messages -> 201 id=${posted && posted.id}`);

    // 4. Arrival, or the full list of what did turn up instead.
    const timeout = new Promise((resolve) => {
      setTimeout(() => resolve(null), ARRIVAL_TIMEOUT_MS).unref?.();
    });
    const receivedAt = await Promise.race([arrived, timeout]);

    if (receivedAt === null) {
      console.error(`FAIL: no frame carrying nonce=${nonce} reached the receiver within ${ARRIVAL_TIMEOUT_MS}ms.`);
      console.error(`  frames that DID arrive on ${channel}: ${frames.length}`);
      frames.forEach((f, i) => {
        console.error(`    [${i}] ${JSON.stringify(f).slice(0, 400)}`);
      });
      return 1;
    }

    console.log(`RECEIVED nonce=${nonce} after ${receivedAt - sentAt}ms`);
    console.log(
      `PASS: a message posted over HTTP by one client arrived, by content, at a different websocket client on ${channel}.`,
    );
    return 0;
  } catch (e) {
    console.error(`FAIL: ${e && e.message ? e.message : e}`);
    if (frames.length > 0) {
      console.error(`  frames received before the failure: ${frames.length}`);
      frames.forEach((f, i) => console.error(`    [${i}] ${JSON.stringify(f).slice(0, 400)}`));
    }
    return 1;
  } finally {
    if (handle) {
      try {
        handle.disconnect();
      } catch {
        /* the process is exiting anyway */
      }
    }
  }
}

process.exit(await main());

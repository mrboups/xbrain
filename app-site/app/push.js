/**
 * The PWA's push opt-in (Phase 27, Plan 27-07).
 *
 * THE INVARIANT THIS FILE EXISTS TO HOLD: this module contains the app's ONLY
 * call asking the browser for notification access, and its ONLY call taking a
 * push subscription. Both sit inside function bodies that are reachable from a
 * click listener and from nowhere else (D-27-05).
 *
 * Why that is a gate and not a convention: a browser gives a site exactly one
 * chance to ask. Ask on load, before the person has any reason to say yes, and
 * a large share of them press Block - and a blocked permission cannot be
 * re-requested by any code, on any later visit, for any reason. It is only
 * reversible through the browser's own site settings, where almost nobody goes.
 * So the single prompt this app will ever get to raise is spent on a deliberate
 * click, and only after the server has confirmed it can actually sign a push
 * for this device.
 *
 * `chrome-extension/tests/test_pwa_push.mjs` enforces both counts against the
 * comment-stripped source, checks that neither API appears at this file's top
 * level, and fails if either turns up in any other file under app-site/app/.
 * The prose above deliberately does not spell either API name: a gate that
 * counts its own documentation measures nothing.
 *
 * The other half of the job is keeping two independent states from drifting -
 * what this browser holds, and what memory-api has stored. A browser
 * subscription the server does not know about is a device that silently
 * receives nothing while the UI says it is on; a server row whose subscription
 * is gone is pruned on its first 404/410 (27-04). Every path below is ordered
 * so the recoverable failure is the one that happens.
 */

/** The message sw.js posts when the browser rotates this device's subscription. */
const PUSH_ROTATED_MESSAGE = "xbrain-push-rotated";

/**
 * How long to wait for an active service worker before giving up.
 *
 * `navigator.serviceWorker.ready` never settles when no worker ever activates -
 * a failed registration, a non-secure context, a browser with the feature
 * disabled. Awaiting it bare would leave the button frozen at whatever the
 * markup said, forever, with nothing in the console to explain it.
 */
const REGISTRATION_TIMEOUT_MS = 5000;

/** Button copy per state. English only, and short enough to fit the header. */
const BUTTON_LABELS = {
  on: "Notifications on",
  off: "Notifications off",
  blocked: "Notifications blocked in browser settings",
  install_required: "Notifications need the installed app",
  unsupported: "Notifications unavailable",
};

/**
 * Every state says what it means and, where there is one, what to do about it.
 * A control that is off with no explanation is indistinguishable from a broken
 * one.
 */
const STATE_HINTS = {
  on: "This device gets a notification when someone mentions you or nudges you.",
  off: "Turn this on to get a notification when someone mentions you or nudges you.",
  blocked:
    "This browser is blocking notifications for this site. Allow them in the browser's site settings, then reload.",
  install_required:
    "Add this app to your Home Screen first. iOS only delivers notifications to an installed app.",
  unsupported: "This browser cannot receive push notifications.",
};

/** What to say when an action failed, rather than leaving the state unexplained. */
const FAILURE_HINTS = {
  server_disabled: "Notifications are not configured on the server yet.",
  server_error: "Could not reach the server. Nothing was changed - try again in a moment.",
  denied: "You declined notifications. You can still allow them from the browser's site settings.",
  browser_error: "This browser would not release its subscription. Reload and try again.",
};

/** The two states a person can actually act on; everything else is read-only. */
const ACTIONABLE = new Set(["on", "off"]);

/**
 * Decode a base64url VAPID public key into the byte array the browser wants.
 *
 * `applicationServerKey` must be a Uint8Array: Safari rejects the base64 string
 * outright, and Chrome accepts it only in some versions, so passing the string
 * produces a bug that reproduces on one device and not another. The input is
 * base64URL (`-`/`_`, unpadded), which `atob` does not accept as-is.
 *
 * @param {string} base64String
 * @returns {Uint8Array}
 */
export function urlBase64ToUint8Array(base64String) {
  const input = String(base64String || "");
  const padding = "=".repeat((4 - (input.length % 4)) % 4);
  const base64 = (input + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/**
 * Can this browser receive a push at all?
 *
 * Ordered so the capability question is settled before `Notification` is
 * touched: on a browser without push there is nothing to ask about, and reading
 * the permission of an API that cannot deliver is how a dead control ends up
 * looking alive.
 */
function isPushCapable() {
  if (typeof navigator === "undefined" || !navigator) return false;
  if (!("serviceWorker" in navigator)) return false;
  if (typeof window === "undefined" || !window) return false;
  if (!("PushManager" in window)) return false;
  if (typeof Notification === "undefined") return false;
  return true;
}

/** iPhone/iPad, including an iPad reporting itself as a Mac with a touchscreen. */
function isIosLike() {
  const ua = (typeof navigator !== "undefined" && navigator && navigator.userAgent) || "";
  if (/iPad|iPhone|iPod/.test(ua)) return true;
  const touchPoints =
    (typeof navigator !== "undefined" && navigator && navigator.maxTouchPoints) || 0;
  return /Macintosh/.test(ua) && touchPoints > 1;
}

/** Running from the Home Screen rather than inside a Safari tab. */
function isStandalone() {
  if (typeof navigator !== "undefined" && navigator && navigator.standalone === true) return true;
  if (typeof window === "undefined" || !window || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(display-mode: standalone)").matches === true;
  } catch (e) {
    return false;
  }
}

/**
 * iOS exposes the whole push API only to an installed app, so "unsupported" on
 * an iPhone is a lie the person cannot act on. Name the actual next step.
 */
function unavailableReason() {
  return isIosLike() && !isStandalone() ? "install_required" : "unsupported";
}

/** The active service worker registration, or null - never a hang. */
async function getRegistration() {
  if (typeof navigator === "undefined" || !navigator || !navigator.serviceWorker) return null;
  let timer = null;
  const expiry = new Promise((resolve) => {
    timer = setTimeout(() => resolve(null), REGISTRATION_TIMEOUT_MS);
  });
  try {
    const ready = Promise.resolve(navigator.serviceWorker.ready).catch(() => null);
    return await Promise.race([ready, expiry]);
  } catch (e) {
    return null;
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

/** This device's current subscription, read-only. Reading never prompts. */
async function currentSubscription() {
  const reg = await getRegistration();
  if (!reg || !reg.pushManager) return null;
  try {
    return await reg.pushManager.getSubscription();
  } catch (e) {
    return null;
  }
}

/**
 * Ask memory-api whether push is configured, and for the PUBLIC key.
 *
 * Fetched at click time rather than baked in at build time, so rotating the
 * server keypair needs no client release (27-CONTEXT, Claude's discretion). The
 * private half never leaves the server - `enabled` is derived from it there
 * (27-03) so this client can tell "not configured" from "configured" without
 * ever seeing it.
 */
async function loadPushConfig(api) {
  let cfg = null;
  try {
    cfg = await api.request("/v1/push/config");
  } catch (e) {
    return { ok: false, reason: "server_error" };
  }
  if (!cfg || cfg.enabled !== true) return { ok: false, reason: "server_disabled" };
  if (typeof cfg.vapid_public_key !== "string" || !cfg.vapid_public_key) {
    return { ok: false, reason: "server_disabled" };
  }
  return { ok: true, cfg };
}

/** Hand the browser's subscription to memory-api, bound to the signed-in caller. */
async function registerSubscription(api, sub) {
  const json = typeof sub.toJSON === "function" ? sub.toJSON() : sub;
  const keys = json.keys || {};
  const ua =
    (typeof navigator !== "undefined" && navigator && navigator.userAgent) || "";
  await api.request("/v1/push/subscribe", {
    method: "POST",
    body: {
      endpoint: json.endpoint,
      keys: { p256dh: keys.p256dh, auth: keys.auth },
      // Labels the device in a future device list. Capped because it is
      // attacker-influenced only in the sense that it is the user's own string,
      // and an unbounded one is still a row nobody wants.
      user_agent: ua ? String(ua).slice(0, 256) : null,
    },
  });
}

/**
 * Tell memory-api to forget an endpoint. Best effort by design.
 *
 * A failure here leaves a row the send path prunes on its first 404/410, which
 * is recoverable. Letting the error escape would abort the caller mid-way and
 * leave BOTH sides wrong, which is not.
 */
async function dropSubscription(api, endpoint) {
  if (!endpoint) return;
  try {
    await api.request("/v1/push/unsubscribe", { method: "POST", body: { endpoint } });
  } catch (e) {
    // Intentionally swallowed - see the docstring above.
  }
}

/**
 * Was this subscription taken with the key the server signs with today?
 *
 * A rotated server keypair does not break anything visibly: the browser keeps a
 * perfectly valid subscription, the UI keeps saying notifications are on, and
 * every send is rejected because the signature no longer matches the key the
 * push service was given. Nothing reports it. Comparing the two is the only way
 * to notice.
 *
 * When the browser does not expose the key it subscribed with, this answers
 * "matches". Every browser that ships web push does expose it (Chrome 54+,
 * Firefox 46+, Safari 16.4+, and iOS web push requires 16.4), so that branch is
 * defensive rather than live - and treating unknown as a mismatch would churn a
 * healthy device's endpoint on every single app open, which is the worse bug.
 */
function keyMatches(sub, publicKey) {
  const declared = sub && sub.options ? sub.options.applicationServerKey : null;
  if (!declared) return true;
  let have;
  try {
    have = declared instanceof Uint8Array ? declared : new Uint8Array(declared);
  } catch (e) {
    return true;
  }
  const want = urlBase64ToUint8Array(publicKey);
  if (have.length !== want.length) return false;
  for (let i = 0; i < want.length; i += 1) {
    if (have[i] !== want[i]) return false;
  }
  return true;
}

/**
 * Bring this device to "subscribed with the current key, and the server knows".
 *
 * PRECONDITION: notification access is already granted. The subscribe call
 * below does not raise a prompt in that state - which is what lets the rotation
 * repair run without a click, on a permission the person already gave.
 *
 * Three cases, in the order they are cheapest to handle:
 *   - a subscription matching the current key: register it and stop, because
 *     re-subscribing a healthy device changes its endpoint for no reason and
 *     loses anything already in flight to the old one;
 *   - a subscription taken with a superseded key: drop it on both sides first,
 *     then take a fresh one;
 *   - none: take one.
 */
async function ensureSubscription(api, cfg) {
  const reg = await getRegistration();
  if (!reg || !reg.pushManager) return { ok: false, reason: "unsupported" };

  let sub = null;
  try {
    sub = await reg.pushManager.getSubscription();
  } catch (e) {
    sub = null;
  }

  if (sub && !keyMatches(sub, cfg.vapid_public_key)) {
    const stale = sub.endpoint;
    try {
      await sub.unsubscribe();
    } catch (e) {
      // The browser kept it; the server row goes anyway, so nothing is sent to
      // a subscription that could never be signed for.
    }
    await dropSubscription(api, stale);
    sub = null;
  }

  if (!sub) {
    try {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public_key),
      });
    } catch (e) {
      return { ok: false, reason: "browser_error" };
    }
  }

  try {
    await registerSubscription(api, sub);
  } catch (e) {
    // Roll the browser back. A subscription the server has no row for is a
    // device that receives nothing while the button claims it is on, and
    // nothing in the app would ever surface that.
    try {
      await sub.unsubscribe();
    } catch (e2) {
      // Nothing further to try; the next enable click re-registers it.
    }
    return { ok: false, reason: "server_error" };
  }

  return { ok: true };
}

/**
 * Turn notifications on for this device. THE ONE PLACE THE BROWSER IS ASKED.
 *
 * Every refusal below returns BEFORE the ask, in increasing order of cost, so a
 * browser that cannot deliver or a deployment that cannot sign never spends the
 * person's single, irreversible permission decision.
 *
 * Reachable only from the click listener in `wirePushButton`.
 *
 * @param {{request: (path: string, opts?: Object) => Promise<any>}} api
 * @returns {Promise<{ok: boolean, reason?: string}>}
 */
export async function enablePush(api) {
  if (!isPushCapable()) return { ok: false, reason: unavailableReason() };
  if (isIosLike() && !isStandalone()) return { ok: false, reason: "install_required" };

  if (Notification.permission === "denied") {
    // Asking again is a silent no-op that would look like the button doing
    // nothing. Say where the real fix is instead.
    return { ok: false, reason: "blocked" };
  }

  const config = await loadPushConfig(api);
  if (!config.ok) return { ok: false, reason: config.reason };

  if (Notification.permission !== "granted") {
    const decision = await Notification.requestPermission();
    if (decision !== "granted") return { ok: false, reason: "denied" };
  }

  return ensureSubscription(api, config.cfg);
}

/**
 * Turn notifications off for this device.
 *
 * The server is told FIRST, deliberately. A row whose subscription is gone gets
 * pruned on the first 404/410 (27-04); a live browser subscription with no row
 * is invisible from the app and cannot be cleaned up from here at all. So if
 * only one of the two can succeed, it must be the server side.
 *
 * @param {{request: (path: string, opts?: Object) => Promise<any>}} api
 * @returns {Promise<{ok: boolean, reason?: string}>}
 */
export async function disablePush(api) {
  if (!isPushCapable()) return { ok: true };
  const sub = await currentSubscription();
  if (!sub) return { ok: true };

  await dropSubscription(api, sub.endpoint);

  try {
    await sub.unsubscribe();
  } catch (e) {
    return { ok: false, reason: "browser_error" };
  }
  return { ok: true };
}

/**
 * Repair a device that is already opted in, without asking anything.
 *
 * Two things this fixes, both of which are otherwise silent:
 *   - the browser rotated this device's subscription (it may do that on its
 *     own, and sw.js reports it);
 *   - the server no longer has the row - a different account signed in on this
 *     browser, or the row was pruned. Re-posting transfers the mailbox to
 *     whoever is signed in NOW, which is exactly what UNIQUE(endpoint) is for
 *     (27-03): without it, this device's notifications would keep going to the
 *     previous occupant.
 *
 * It does nothing at all unless access is already granted AND this device
 * already holds a subscription, so it can never be the thing that raises a
 * prompt.
 *
 * @param {{request: (path: string, opts?: Object) => Promise<any>}} api
 * @returns {Promise<{ok: boolean, reason?: string}>}
 */
export async function resyncPush(api) {
  if (!isPushCapable()) return { ok: false, reason: unavailableReason() };
  if (Notification.permission !== "granted") return { ok: false, reason: "not_granted" };
  const sub = await currentSubscription();
  if (!sub) return { ok: false, reason: "not_subscribed" };

  const config = await loadPushConfig(api);
  if (!config.ok) return { ok: false, reason: config.reason };

  return ensureSubscription(api, config.cfg);
}

/** Read the true state. No network, no prompt, no writes. */
async function readPushState() {
  if (!isPushCapable()) return unavailableReason();
  if (isIosLike() && !isStandalone()) return "install_required";
  if (Notification.permission === "denied") return "blocked";
  const sub = await currentSubscription();
  return sub ? "on" : "off";
}

function paintButton(btnEl, hintEl, state) {
  btnEl.setAttribute("data-state", state);
  btnEl.textContent = BUTTON_LABELS[state] || BUTTON_LABELS.off;
  btnEl.disabled = !ACTIONABLE.has(state);
  btnEl.setAttribute("aria-pressed", state === "on" ? "true" : "false");
  const hint = STATE_HINTS[state] || "";
  btnEl.title = hint;
  if (hintEl) {
    hintEl.textContent = hint;
    hintEl.hidden = hint === "";
  }
}

/**
 * Show what is actually true: unsupported, needs installing, blocked, off, on.
 *
 * Safe to call on load, and called there, precisely because it only READS -
 * `Notification.permission` and the existing subscription. That is also why the
 * person can always see the state they granted and revoke it (D-27-05): a
 * button that lies about being on is a permission they cannot audit.
 *
 * @param {{request: (path: string, opts?: Object) => Promise<any>}} api
 *   Accepted so all four exports take the same first argument and no call site
 *   has to remember which one needs it. This function deliberately makes no
 *   request: a read-only refresh is what makes "never prompts, never writes"
 *   true by construction rather than by review.
 * @param {HTMLElement} btnEl
 * @param {HTMLElement} [hintEl]
 */
export async function refreshPushButton(api, btnEl, hintEl) {
  if (!btnEl) return;
  let state = "off";
  try {
    state = await readPushState();
  } catch (e) {
    // Reporting "off" on an unreadable state is the honest fallback: the click
    // is still available and it re-checks everything from scratch.
    state = "off";
  }
  paintButton(btnEl, hintEl, state);
}

/**
 * Attach the toggle. Attaching asks for nothing - the click does.
 *
 * This is the ONLY place `enablePush` is called, which is what makes the
 * permission prompt structurally unreachable without a user gesture (D-27-05).
 *
 * It also listens for sw.js's rotation message. That path does NOT prompt:
 * access is already granted at that point and only the endpoint changed, so it
 * goes through `resyncPush`, which refuses to do anything otherwise.
 *
 * Wiring twice is a no-op. `startChat()` runs again after a re-sign-in, and a
 * second click listener would enable and then immediately disable on one click.
 *
 * @param {{request: (path: string, opts?: Object) => Promise<any>}} api
 * @param {HTMLElement} btnEl
 * @param {HTMLElement} [hintEl]
 */
export function wirePushButton(api, btnEl, hintEl) {
  if (!btnEl) return;
  if (btnEl.dataset && btnEl.dataset.pushWired === "1") return;
  if (btnEl.dataset) btnEl.dataset.pushWired = "1";

  btnEl.addEventListener("click", async () => {
    btnEl.disabled = true;
    const wasOn = btnEl.getAttribute("data-state") === "on";
    let result = { ok: false, reason: "server_error" };
    try {
      result = wasOn ? await disablePush(api) : await enablePush(api);
    } catch (e) {
      result = { ok: false, reason: "server_error" };
    }
    // Repaint from the real state first, then say why it did not change - the
    // repaint owns the hint, so an explanation written before it would vanish.
    await refreshPushButton(api, btnEl, hintEl);
    if (!result.ok && hintEl) {
      hintEl.textContent = FAILURE_HINTS[result.reason] || FAILURE_HINTS.server_error;
      hintEl.hidden = false;
    }
  });

  if (
    typeof navigator !== "undefined" &&
    navigator &&
    navigator.serviceWorker &&
    typeof navigator.serviceWorker.addEventListener === "function"
  ) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      const data = event && event.data;
      if (!data || data.type !== PUSH_ROTATED_MESSAGE) return;
      resyncPush(api)
        .then(() => refreshPushButton(api, btnEl, hintEl))
        .catch(() => {
          // Best effort; the next app open runs the same repair.
        });
    });
  }
}

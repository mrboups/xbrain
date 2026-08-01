/**
 * xbrain PWA service worker (Phase 27, Plan 27-05).
 *
 * Three jobs, and deliberately no fourth:
 *   1. precache the app SHELL so /app/ opens instantly and survives a flaky link;
 *   2. turn a `push` event into a visible notification (the payload is delivered
 *      by 27-04's server-side send);
 *   3. focus or open /app/ when that notification is clicked.
 *
 * WHAT IT MUST NEVER DO: cache a response that belonged to a signed-in person.
 * One service worker is registered per ORIGIN, not per account — the next person
 * to use this browser profile inherits it. A cached /v1/... body replayed to them
 * would be a real data leak, not a stale-UI annoyance. The four guards in `fetch`
 * below exist for that single reason; read them before touching this file.
 *
 * There is also no runtime caching at all: nothing is ever written to the cache
 * outside `install`. A response can only be served from cache if it was in SHELL.
 */

const CACHE = "xb-app-shell-v1";

/**
 * The shell. Plans 27-06 (chat surface) and 27-07 (push opt-in) add some of
 * these files; they are listed now so the precache list does not need editing
 * twice. A missing entry must NOT brick the install, so each URL is added
 * individually via allSettled instead of `cache.addAll`, which rejects the whole
 * batch on a single 404.
 */
const SHELL = [
  "/app/",
  "/app/index.html",
  "/app/app.css",
  "/app/app.js",
  "/app/auth.js",
  "/app/platform_web.js",
  "/app/push.js",
  "/app/chat.js",
  "/app/manifest.webmanifest",
  "/app/vendor/centrifuge.js",
  "/app/icons/icon-192.png",
  "/app/icons/icon-512.png",
  "/app/icons/icon-maskable-512.png",
  "/app/chat_core/chat_stream.js",
  "/app/chat_core/api.js",
  "/app/chat_core/platform.js",
  "/app/chat_core/nudge_open.js",
  "/app/chat_core/theme.js",
  "/app/chat_core/render.js",
  "/app/chat_core/publication.js",
  "/app/chat_core/realtime.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      const results = await Promise.allSettled(SHELL.map((url) => cache.add(url)));
      results.forEach((r, i) => {
        if (r.status === "rejected") {
          // Expected for shell entries a later plan has not shipped yet.
          console.warn("[xbrain] shell precache miss:", SHELL[i]);
        }
      });
      // Take over immediately: a half-updated shell (new HTML, old JS) is worse
      // than a hard swap, and `activate` clears the previous cache anyway.
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((n) => n !== CACHE).map((n) => caches.delete(n)),
      );
      await self.clients.claim();
    })(),
  );
});

/**
 * Shell-only, cache-first fetch.
 *
 * Each guard is on its own line so each is independently greppable, and each
 * returns WITHOUT calling event.respondWith — which hands the request straight
 * back to the network, untouched by this worker.
 */
self.addEventListener("fetch", (event) => {
  const req = event.request;
  // 1. Only GET is ever cacheable.
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // 2. NEVER a cross-origin request. api.grooveos.app responses are per-user;
  //    this worker is shared by every profile on the device, so a cached
  //    /v1/... response served to a later, different user on a shared device
  //    would be a real leak.
  if (url.origin !== self.location.origin) return;
  // 3. NEVER an API path, even same-origin (defence in depth, in case the API is
  //    ever reverse-proxied under this origin).
  if (url.pathname.startsWith("/v1/")) return;
  // 4. NEVER a credentialed request.
  if (req.headers.has("Authorization")) return;
  // Cache-first, shell only. Nothing is written to the cache here, so a miss is
  // a plain network fetch and stays a miss.
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});

/** Fallback copy when a push arrives with no body or an unparseable one. */
const DEFAULT_TITLE = "GrooveOS";
const DEFAULT_BODY = "You have a new notification.";

/**
 * Only a first-party /app/ path may be opened from a notification.
 *
 * The payload is assembled server-side from data other people typed, so the
 * destination is validated here rather than trusted: an absolute URL, a
 * javascript: scheme or a protocol-relative "//evil.example" must not become a
 * window this worker opens on the user's behalf.
 */
function safeAppPath(value) {
  if (typeof value !== "string") return "/app/";
  if (!value.startsWith("/app/")) return "/app/";
  if (value.startsWith("//")) return "/app/";
  return value;
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = (event.data && event.data.json()) || {};
  } catch (e) {
    payload = {};
  }

  const title = typeof payload.title === "string" && payload.title ? payload.title : DEFAULT_TITLE;
  const body = typeof payload.body === "string" && payload.body ? payload.body : DEFAULT_BODY;
  // A tag collapses repeat notifications from the same conversation instead of
  // stacking one per message.
  const tag = typeof payload.tag === "string" && payload.tag ? payload.tag : "xbrain";

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag,
      icon: "/app/icons/icon-192.png",
      badge: "/app/icons/icon-192.png",
      data: { url: safeAppPath(payload.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = safeAppPath(event.notification.data && event.notification.data.url);

  event.waitUntil(
    (async () => {
      const clientList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of clientList) {
        // Reuse an open tab rather than piling up a new one per notification.
        if (new URL(client.url).pathname.startsWith("/app/") && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
      return undefined;
    })(),
  );
});

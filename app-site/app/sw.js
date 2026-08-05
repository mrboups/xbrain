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

/**
 * GENERATED. Do not edit this value by hand — run `make shell-cache`.
 *
 * It is a hash of every file in SHELL below, computed by scripts/shell-cache.mjs
 * and written here. Change any precached file and the name changes; change none
 * of them and it does not. `activate` deletes every cache that is not this one,
 * so a new name means the previous shell goes in one piece and there is no window
 * where a half-updated mix of old and new files is served together.
 *
 * WHY IT IS DERIVED RATHER THAN BUMPED. This constant was hand-maintained through
 * v11 and missed three times in three days. The last miss is the argument: two
 * agents independently reserved `v10` in separate worktrees, and only the merge
 * revealed it. A number nobody can RESERVE will eventually be taken twice, and
 * every miss presents as a broken feature — a module the cache never fetched, an
 * old chat against a new API — rather than as a caching problem, which is why
 * each one cost hours to find.
 *
 * A derived name has no step to forget. `node scripts/shell-cache.mjs --check`
 * is the gate that says so: it runs beside the chat-core drift check, in the
 * Makefile and in verify-phase27.sh, and goes red when a precached file changed
 * and this line did not.
 */
const CACHE = "xb-app-shell-dbda74998ca6";

/**
 * The shell. Every entry below now ships (27-06 landed the chat surface, 27-07
 * push.js), and both PWA test files assert that in each direction: nothing
 * shipped is missing from this list, and nothing on this list is missing from
 * disk. Each URL is still added individually via allSettled rather than
 * `cache.addAll`, which rejects the whole batch on a single 404 - one bad entry
 * must degrade the offline shell, not brick the install.
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
  "/app/panels.js",
  "/app/profile.js",
  "/app/import.js",
  "/app/team_keys.js",
  "/app/viewport.js",
  "/app/bridge_link.js",
  "/app/manifest.webmanifest",
  "/app/vendor/centrifuge.js",
  "/app/icons/icon-192.png",
  "/app/icons/icon-512.png",
  "/app/icons/icon-maskable-512.png",
  "/app/chat_core/chat_stream.js",
  "/app/chat_core/api.js",
  "/app/chat_core/dom.js",
  "/app/chat_core/platform.js",
  "/app/chat_core/nudge_open.js",
  "/app/chat_core/theme.js",
  "/app/chat_core/markdown.js",
  "/app/chat_core/render.js",
  "/app/chat_core/message_menu.js",
  "/app/chat_core/publication.js",
  "/app/chat_core/realtime.js",
  "/app/chat_core/people.js",
  "/app/chat_core/invite.js",
  "/app/chat_core/team_api_keys.js",
  "/app/chat_core/swipe_nav.js",
  "/app/chat_core/team_rail.js",
  "/app/chat_core/teams.js",
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
 * Where Android's share sheet lands, declared as `share_target` in the manifest
 * and rewritten to the app shell by firebase.json.
 *
 * Named here so the guard below is greppable and so the three places that must
 * agree about this path (manifest, hosting rewrite, worker) can be checked
 * against each other by a test.
 */
const SHARE_TARGET_PATH = "/app/share-target";

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
  // 1b. NEVER a share. The shared conversation rides in this navigation's query
  //     string; answering it from the shell would hand back the app WITHOUT it,
  //     and the person's conversation would be gone with no error to explain
  //     where. Today nothing under this path is in SHELL so the match would
  //     miss anyway — this guard is what stops a future navigation fallback
  //     (`caches.match("/app/")` for any navigate request, the usual pattern)
  //     from silently swallowing every share.
  if (url.pathname === SHARE_TARGET_PATH) return;
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

/**
 * The browser rotated this device's subscription.
 *
 * It may do that on its own, at any time, with no user action - and a rotated
 * subscription is a device that silently stops receiving anything while the
 * button still says notifications are on. Nothing reports it.
 *
 * This worker deliberately does NOT re-subscribe and does NOT call memory-api:
 * a worker holds no bearer token, so an unauthenticated write is the only thing
 * it could attempt, and it would be rejected. It also must not take a
 * subscription of its own - the app has exactly one place allowed to do that,
 * and it is not here (D-27-05).
 *
 * So it tells whoever is listening. push.js re-runs the authenticated repair on
 * that message. If no window is open, the next app open runs the same repair,
 * and the stale endpoint is pruned server-side on its first 404/410 (27-04).
 * Strictly best effort: this handler must never throw.
 */
self.addEventListener("pushsubscriptionchange", (event) => {
  const oldEndpoint =
    (event.oldSubscription && event.oldSubscription.endpoint) || null;

  event.waitUntil(
    (async () => {
      try {
        const clientList = await self.clients.matchAll({
          type: "window",
          includeUncontrolled: true,
        });
        for (const client of clientList) {
          client.postMessage({ type: "xbrain-push-rotated", oldEndpoint });
        }
      } catch (e) {
        console.warn("[xbrain] could not report a rotated subscription:", e);
      }
    })(),
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

// session.ts — reads the board id and the one-time access token out of the URL,
// and derives the same-origin WebSocket URL for the Hocuspocus /collab endpoint.
//
// The access token arrives in the URL FRAGMENT (`#t=<token>`), never the query
// string. A fragment is never transmitted to a server, so the secret cannot reach
// an nginx access log, a Referer header, or any upstream proxy log. Because it
// must also not linger in the address bar or in a browser history entry, it is
// stripped with history.replaceState BEFORE any network call is made. If this
// value were ever appended as a query parameter it WOULD reach those logs — so it
// never is; it is handed to Hocuspocus through the provider's async token supplier
// and travels inside the Hocuspocus Auth message instead (see Board.tsx).
//
// This module makes ZERO network calls. In 26a the SPA never calls memory-api —
// the token is handed to it in the fragment, not minted on load — which is why
// Pitfall 6 (a CORS preflight on a memory-api call) needs no change in this slice.

// Canonical UUID (any version). An id that is not a UUID is treated as absent so
// the page shows the "no board" state rather than opening an attacker-chosen
// document name on the socket.
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface Session {
  boardId: string | null;
  token: string | null;
  wsUrl: string;
}

export function readSession(): Session {
  // Board id from the query string; validated as a canonical UUID.
  const rawBoardId = new URLSearchParams(location.search).get("b");
  const boardId = rawBoardId && UUID_RE.test(rawBoardId) ? rawBoardId : null;

  // Token from the FRAGMENT. Parse `location.hash` for `t=<value>` and
  // decodeURIComponent it. The fragment is used (not the query string) precisely
  // so the token is never transmitted to a server (see the module header).
  // codex P2: parse AND strip inside try/finally so the token can never linger. A
  // malformed percent-encoding makes decodeURIComponent throw, and replaceState can
  // itself throw — either would skip the strip and leave the secret in the address bar /
  // history entry. The `finally` guarantees the fragment is cleared no matter what, and a
  // failed decode degrades to no-token (the board then shows its access-denied state)
  // rather than leaking.
  let token: string | null = null;
  const fragment = location.hash.startsWith("#")
    ? location.hash.slice(1)
    : location.hash;
  try {
    for (const pair of fragment.split("&")) {
      const eq = pair.indexOf("=");
      if (eq === -1) continue;
      if (pair.slice(0, eq) === "t") {
        const value = pair.slice(eq + 1);
        token = value ? decodeURIComponent(value) : null;
        break;
      }
    }
  } catch {
    token = null; // malformed encoding — treat as no token, never leak the raw fragment
  } finally {
    // Strip the fragment IMMEDIATELY, before any network call, so the secret does not
    // linger. Guarded so a replaceState failure can't itself abort the strip path.
    if (location.hash) {
      try {
        history.replaceState(null, "", location.pathname + location.search);
      } catch {
        location.hash = ""; // last-resort clear if replaceState is unavailable/throws
      }
    }
  }

  // WS URL DERIVED from the current location, never from a build-time env var.
  // One image works for every self-hoster and for the SaaS deploy, in both
  // editions, with no rebuild — nginx routes /collab on this same vhost to the
  // Hocuspocus container (26-06).
  const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/collab`;

  return { boardId, token, wsUrl };
}

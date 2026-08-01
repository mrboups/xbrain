/**
 * The team rail — one square per team, shared by both surfaces (D-27-04).
 *
 * Lifted out of chrome-extension/popup.js wholesale. The PWA needs the same
 * control, and a second copy of "initials, badge, order, drag" would drift from
 * the extension's within a week: the ordering rule alone (new teams go to the
 * RIGHT, never into the middle) is the kind of thing that gets re-derived
 * differently by two authors on two days.
 *
 * Nothing here knows which surface it runs on. The document, the container, the
 * team list, the active id, the switch action and the unread lookup are all
 * injected; persistence goes through the platform storage shim (platform.js),
 * never through an extension namespace or localStorage directly.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/chat.js      (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_core_team_rail.mjs
 *
 * XSS discipline, same as render.js: a team name is a string somebody typed.
 * Every one of them reaches the document through textContent or an attribute
 * setter. This file assigns no markup string anywhere.
 */

/**
 * Where the user's arrangement lives. Read through the platform shim, so it
 * lands in the extension's own local area on one surface and in the browser's
 * page storage on the other — two different stores, deliberately: a rail order
 * is a per-surface layout preference, not an account setting.
 */
export const TEAM_ORDER_KEY = "xbrain_team_order_v1";

/**
 * Two-letter mark for a team square — initials of the first two words, else the
 * first two characters. Falls back to "?" so a nameless team still renders.
 *
 * @param {string} name
 * @returns {string}
 */
export function teamInitials(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/**
 * Order the rail: teams the user has explicitly arranged keep their position, and
 * anything not in that list (a team just created or just joined) goes to the END —
 * so a new square appears to the RIGHT of the newest rather than jumping into the
 * middle because the API happened to sort differently.
 *
 * @param {Array<{id: string}>} teams
 * @param {Array<string>} order
 * @returns {Array<{id: string}>} a new array; the input is never mutated
 */
export function orderTeams(teams, order) {
  const list = Array.isArray(teams) ? teams : [];
  if (!Array.isArray(order) || !order.length) return list.slice();
  const rank = new Map(order.map((id, i) => [String(id), i]));
  return list.slice().sort((a, b) => {
    const ra = rank.has(String(a.id)) ? rank.get(String(a.id)) : Number.MAX_SAFE_INTEGER;
    const rb = rank.has(String(b.id)) ? rank.get(String(b.id)) : Number.MAX_SAFE_INTEGER;
    return ra - rb; // ties (both unknown) keep the API's own order
  });
}

/**
 * Read a stored order back out of whichever shim wrote it.
 *
 * The two storage backends do not round-trip the same value. The extension's
 * area stores structured data, so an array comes back an array; the page-storage
 * one stores strings, so an array handed to it comes back as "a,b,c" — which
 * would restore a corrupted order silently, on the surface that has no devtools
 * open. Writing JSON and accepting BOTH shapes on read is what makes one module
 * correct on both, and it keeps every extension user's pre-existing array
 * working.
 *
 * @param {unknown} raw
 * @returns {Array<string>} ids, or [] for anything unrecognisable
 */
function parseStoredOrder(raw) {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch (e) {
      return [];
    }
  }
  if (!Array.isArray(value)) return [];
  return value.filter((id) => typeof id === "string" && id.length > 0);
}

/**
 * Build the rail for one chat surface.
 *
 * @param {{
 *   doc: Document,
 *   railEl: Element,
 *   storage: {get: Function, set: Function},
 *   getTeams: () => Array<{id: string, display_name?: string, slug?: string}>,
 *   getActiveTeamId: () => (string|null|undefined),
 *   onSelectTeam: (teamId: string) => void,
 *   getUnreadCount?: ((teamId: string) => Promise<number|null>)|null
 * }} opts
 *   doc             — the document the nodes are created in
 *   railEl          — the container; its children are replaced on every render
 *   storage         — the platform shim's storage area (get/set), for the order
 *   getTeams        — read late: the list changes when a team is joined or left
 *   getActiveTeamId — read late: it changes on every switch
 *   onSelectTeam    — called with the clicked team's id; the surface owns what
 *                     "switch" means (teardown, history, subscriptions)
 *   getUnreadCount  — optional. Returns the unread count for an INACTIVE team,
 *                     or null when there is nothing to show. A surface that does
 *                     not track read state passes null and gets no badges rather
 *                     than badges that never clear.
 * @returns {{render: () => Promise<void>, refreshBadges: () => Promise<void>}}
 */
export function createTeamRail(opts) {
  const cfg = opts || {};
  const doc = cfg.doc;
  const railEl = cfg.railEl;
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("createTeamRail requires opts.doc");
  }
  if (!railEl) throw new TypeError("createTeamRail requires opts.railEl");

  const storage = cfg.storage || null;
  const getTeams = typeof cfg.getTeams === "function" ? cfg.getTeams : () => [];
  const getActiveTeamId =
    typeof cfg.getActiveTeamId === "function" ? cfg.getActiveTeamId : () => null;
  const onSelectTeam =
    typeof cfg.onSelectTeam === "function" ? cfg.onSelectTeam : () => {};
  const getUnreadCount =
    typeof cfg.getUnreadCount === "function" ? cfg.getUnreadCount : null;

  /** The saved arrangement, or [] when there is none / it cannot be read. */
  async function readTeamOrder() {
    if (!storage || typeof storage.get !== "function") return [];
    try {
      const got = await storage.get([TEAM_ORDER_KEY]);
      return parseStoredOrder(got && got[TEAM_ORDER_KEY]);
    } catch (e) {
      return [];
    }
  }

  /** Persist an arrangement. An unsaved order is a cosmetic loss, never a blocker. */
  async function writeTeamOrder(ids) {
    if (!storage || typeof storage.set !== "function") return;
    try {
      await storage.set({ [TEAM_ORDER_KEY]: JSON.stringify(ids) });
    } catch (e) {
      /* see the docstring */
    }
  }

  /** Every square currently in the rail, in DOM order. */
  function railIcons() {
    return Array.from(railEl.children).filter(
      (el) => el.dataset && typeof el.dataset.teamId === "string",
    );
  }

  /** Persist whatever order the rail currently shows. */
  async function persistCurrentOrder() {
    await writeTeamOrder(railIcons().map((el) => el.dataset.teamId).filter(Boolean));
  }

  /**
   * One square.
   *
   * Drag to rearrange: native HTML5 DnD keeps this to a few handlers and no
   * pointer-math; the drop target moves the dragged square before or after
   * itself depending on which half was entered, then the new order is saved.
   *
   * The dragged element is found by scanning the rail rather than through a
   * built attribute selector — a team id interpolated into a selector string is
   * a parse error waiting for the first id that is not a plain uuid, and the
   * escape helper that would fix it is a browser global this module does not
   * take a dependency on.
   */
  function buildTeamIcon(team) {
    const name = team.display_name || team.slug || "team";
    const btn = doc.createElement("button");
    btn.type = "button";
    btn.className = "xb-team-icon";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", String(team.id === getActiveTeamId()));
    btn.title = name;
    btn.setAttribute("aria-label", name);
    btn.dataset.teamId = team.id;
    // textContent, never markup: a team name is a string somebody typed.
    btn.textContent = teamInitials(name);

    btn.addEventListener("click", () => {
      if (team.id !== getActiveTeamId()) onSelectTeam(team.id);
    });

    btn.draggable = true;
    btn.addEventListener("dragstart", (e) => {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", team.id);
      btn.classList.add("is-dragging");
    });
    btn.addEventListener("dragend", () => btn.classList.remove("is-dragging"));
    btn.addEventListener("dragover", (e) => {
      e.preventDefault(); // required to allow a drop
      e.dataTransfer.dropEffect = "move";
    });
    btn.addEventListener("drop", async (e) => {
      e.preventDefault();
      const draggedId = e.dataTransfer.getData("text/plain");
      if (!draggedId || draggedId === team.id) return;
      const dragged = railIcons().find((el) => el.dataset.teamId === draggedId);
      if (!dragged) return;
      const rect = btn.getBoundingClientRect();
      const after = e.clientX > rect.left + rect.width / 2;
      railEl.insertBefore(dragged, after ? btn.nextSibling : btn);
      await persistCurrentOrder();
    });

    return btn;
  }

  /**
   * Render one square per team. Clicking a square switches the chat; the active
   * one is filled. Unread counts are badged onto the square itself so a team
   * with new messages is visible without opening anything — which a <select>
   * could not do.
   */
  async function paintRail() {
    const order = await readTeamOrder();
    while (railEl.firstChild) railEl.removeChild(railEl.firstChild);
    for (const team of orderTeams(getTeams(), order)) {
      railEl.appendChild(buildTeamIcon(team));
    }
    await paintBadges();
  }

  /**
   * Badge each INACTIVE team with its unread count. The active team is skipped
   * on purpose: you are looking at it, and the surface marks it read a moment
   * later, so a badge there would flash and vanish. Fail-soft — a badge is a
   * nicety, never a blocker.
   */
  async function paintBadges() {
    if (!getUnreadCount) return;
    const activeId = getActiveTeamId();
    for (const btn of railIcons()) {
      const id = btn.dataset.teamId;
      const existing = btn.querySelector(".xb-team-badge");
      if (!id || id === activeId) {
        if (existing) existing.remove();
        continue;
      }
      let count = null;
      try {
        count = await getUnreadCount(id);
      } catch (e) {
        continue; // one team's badge failing must not stop the others
      }
      if (!count || !(count > 0)) {
        if (existing) existing.remove();
        continue;
      }
      let badge = existing;
      if (!badge) {
        badge = doc.createElement("span");
        badge.className = "xb-team-badge";
        btn.appendChild(badge);
      }
      badge.textContent = count > 99 ? "99+" : String(count);
    }
  }

  return { render: paintRail, refreshBadges: paintBadges };
}

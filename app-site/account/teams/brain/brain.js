/**
 * brain.js — app-site/account/teams/brain page (Phase 11, plan 11-08).
 *
 * Renders the Brain Monitor: paginated feed of /v1/brain/events with
 * lateral filters, inline truth_level editor, soft-delete + restore,
 * 30s live polling using a since-cursor that PREPENDS new rows.
 *
 * Auth: reads xbt_token + user_sub from localStorage (canonical keys
 * minted by app-site/account/teams/teams.js — Phase 10 GHA-08).
 * If no token, surfaces a sign-in CTA pointing to /account/teams/.
 *
 * Team scope: read from URL ?team=<slug>. Falls back to "default" if missing.
 *
 * Endpoints (memory-api, Phase 11 Wave 1-3):
 *   GET    /v1/brain/events?...
 *   PATCH  /v1/brain/events/{entity_type}/{entity_id}   {truth_level}
 *   DELETE /v1/brain/events/{entity_type}/{entity_id}
 *   POST   /v1/brain/events/{entity_type}/{entity_id}/restore
 *
 * Revision 1 (per 11-08-PLAN Task 2):
 *   - M-2: dedicated pollForNewItems() with since-cursor → prepend; never
 *     touches state.nextCursor; honors current state.filters.
 *   - M-5: explicit 403 toast wording matching the API error body;
 *     change handler short-circuits on !canEdit(item) even if DOM is
 *     manipulated to re-enable a disabled <select>.
 */

(function () {
  "use strict";

  // ── Config ──────────────────────────────────────────────────────────────
  const MEMORY_API_BASE = "https://api.grooveos.app";
  const QS = new URLSearchParams(location.search);
  const TEAM_SLUG = QS.get("team") || "default";
  // Phase 11 plan 11-11 Task 6 — superadmin drill-down mode.
  // When the URL carries ?as_superadmin=1, this page:
  //   1. Renders the yellow "Viewing as superadmin" banner.
  //   2. Routes all reads (initial load + 30s polling) through
  //      /v1/admin/brain/events?team_slug=<slug> instead of the
  //      team-scoped /v1/brain/events. The admin endpoint is gated by
  //      assert_is_superadmin and writes an audit_log row synchronously
  //      on every call — the only path that satisfies SC-9 AND that
  //      works for a kind='user' superadmin who isn't a member of the
  //      target team (deps.py get_team_scope only bypasses for bridge).
  //   3. Hides edit / delete / restore / bulk controls — drill-down is
  //      read-only in v1. Admin write endpoints are deferred to Phase 12+.
  // The banner is purely informational — the actual elevation is enforced
  // by assert_is_superadmin on the admin endpoints; a malicious link can
  // toggle the UI but cannot grant API access.
  const IS_SUPERADMIN_VIEW = QS.get("as_superadmin") === "1";
  const TRUTH_LEVELS = ["EPHEMERAL", "WORKING", "VALIDATED", "CANONICAL", "PUBLIC"];
  const ENTITY_TYPES = [
    "memory_item", "conversation", "message",
    "team_message", "task", "contact", "granola_note",
  ];
  const POLL_INTERVAL_MS = 30_000;
  const PAGE_SIZE = 50;
  const PREVIEW_MAX_CHARS = 200;
  const BULK_CONCURRENCY = 4;

  // Canonical localStorage keys (shared with teams.js + Chrome extension).
  const STORAGE_TOKEN = "xbt_token";
  const STORAGE_EMAIL = "user_sub";
  // Legacy keys — read-only fallback (teams.js migrates on its first run).
  const LEGACY_STORAGE_TOKEN = "xbrain_xbt_token";
  const LEGACY_STORAGE_EMAIL = "xbrain_user_email";

  // ── State ───────────────────────────────────────────────────────────────
  const state = {
    items: [],
    rowsById: new Map(),         // entity composite key → HTMLTableRowElement
    nextCursor: null,
    hasMore: false,
    filters: {
      entity_type: [],
      truth_level: [],
      source: "",
      created_by: "",
      q: "",
      since: null,
      include_deleted: false,
    },
    loading: false,
    pollTimer: null,
    pollPaused: false,           // when tab hidden
    pollErrorStreak: 0,
    activeEditRowId: null,       // composite key of row currently being edited
    selected: new Set(),         // composite keys selected via bulk checkbox
    currentUserId: null,
    membership: null,             // { role: 'admin' | 'member', team_id }
    intersectionObserver: null,
  };

  // ── DOM refs (resolved at init time) ────────────────────────────────────
  let dom = null;

  // ── Boot ────────────────────────────────────────────────────────────────
  window.addEventListener("load", init);

  async function init() {
    migrateLegacyKeysReadOnly();

    dom = {
      signinSection: document.getElementById("signin-section"),
      authSection:   document.getElementById("auth-section"),
      superadminBanner: document.getElementById("superadmin-banner"),
      hdrUser:       document.getElementById("hdr-user"),
      hdrEmail:      document.getElementById("hdr-email"),
      hdrTeamSlug:   document.getElementById("hdr-team-slug"),
      pageTeamLabel: document.getElementById("page-team-label"),
      btnSignout:    document.getElementById("btn-signout"),
      btnRefresh:    document.getElementById("btn-refresh"),
      btnApply:      document.getElementById("btn-apply-filters"),
      btnReset:      document.getElementById("btn-reset-filters"),
      fltEntityType: document.getElementById("flt-entity-type"),
      fltTruthLevel: document.getElementById("flt-truth-level"),
      fltSource:     document.getElementById("flt-source"),
      fltCreatedBy:  document.getElementById("flt-created-by"),
      fltQ:          document.getElementById("flt-q"),
      fltSince:      document.getElementById("flt-since"),
      fltShowDeleted:document.getElementById("flt-show-deleted"),
      tbody:         document.getElementById("brain-tbody"),
      table:         document.getElementById("brain-table"),
      thSelect:      document.getElementById("th-select"),
      selectAll:     document.getElementById("select-all"),
      bulkBar:       document.getElementById("bulk-bar"),
      bulkCount:     document.getElementById("bulk-count"),
      bulkTruth:     document.getElementById("bulk-truth-level"),
      bulkDelete:    document.getElementById("bulk-delete"),
      bulkClear:     document.getElementById("bulk-clear"),
      emptyState:    document.getElementById("empty-state"),
      loadingSkel:   document.getElementById("loading-skeleton"),
      feedStatus:    document.getElementById("feed-status"),
      sentinel:      document.getElementById("sentinel"),
      loadMoreStatus:document.getElementById("load-more-status"),
      pollIndicator: document.getElementById("poll-indicator"),
      pollLabel:     document.querySelector("#poll-indicator .poll-label"),
      toastStack:    document.getElementById("toast-stack"),
      modal:         document.getElementById("modal"),
      modalTitle:    document.getElementById("modal-title"),
      modalBody:     document.getElementById("modal-body"),
      modalConfirm:  document.getElementById("modal-confirm"),
    };

    // No token → show sign-in CTA, abort.
    const token = readToken();
    if (!token) {
      showSignIn();
      return;
    }

    // (260603) When the URL has no ?team=, TEAM_SLUG falls back to "default" — a team
    // nobody is a member of, so every events fetch 403s. Resolve the signed-in user's
    // first team and redirect to ?team=<slug> so TEAM_SLUG is correct on reload. The
    // /v1/teams/my-teams endpoint is user-scoped (not team-gated), so it works even while
    // the (wrong) "default" scope is in effect. Falls through on error / no teams.
    if (!QS.get("team")) {
      try {
        const teams = await xbtFetch("/v1/teams/my-teams");
        if (Array.isArray(teams) && teams.length) {
          const slug = teams[0].slug || teams[0].team_slug;
          if (slug) {
            location.replace(location.pathname + "?team=" + encodeURIComponent(slug));
            return;
          }
        }
      } catch (_e) {
        // Keep the existing default behavior + its error message.
      }
    }

    // Render header label.
    if (dom.hdrTeamSlug) {
      dom.hdrTeamSlug.textContent = "· " + TEAM_SLUG;
      dom.hdrTeamSlug.hidden = false;
    }
    if (dom.pageTeamLabel) dom.pageTeamLabel.textContent = TEAM_SLUG;
    const email = localStorage.getItem(STORAGE_EMAIL) || "signed in";
    if (dom.hdrEmail) dom.hdrEmail.textContent = email;
    dom.hdrUser.hidden = false;

    // Wire global event handlers.
    wireEvents();

    // Show authenticated UI shell.
    dom.signinSection.hidden = true;
    dom.authSection.hidden = false;

    // Phase 11 plan 11-11 Task 6 — toggle the superadmin banner. The
    // banner is informational only; the API enforces the actual
    // elevation. Hidden flag drives `display: none` via the global
    // [hidden] rule in brain.css.
    if (IS_SUPERADMIN_VIEW && dom.superadminBanner) {
      dom.superadminBanner.hidden = false;
    }

    // Load identity + team membership, then first page + polling.
    try {
      await loadIdentityAndMembership();
    } catch (e) {
      if (e && e.status === 401) {
        clearTokenAndShowSignIn();
        return;
      }
      showToast("Couldn't load your account: " + safeMessage(e), "error");
    }

    // Phase 11 plan 11-11 Task 6 — drill-down is read-only in v1.
    // Force a non-admin "viewer" role so canEdit() returns false for
    // every item; this hides inline edit + delete + restore + bulk.
    // Server-side this is belt-and-braces — admin write endpoints don't
    // exist yet, so any attempt would 403 anyway.
    if (IS_SUPERADMIN_VIEW) {
      state.membership = { role: "viewer", team_id: null };
    }

    // Bulk-action UI is admin-only; never shown in superadmin view.
    if (state.membership && state.membership.role === "admin") {
      dom.thSelect.hidden = false;
    }

    await loadEvents({ append: false, cursor: null });
    setupIntersectionObserver();
    startPolling();

    // Pause polling when the tab is hidden, resume when visible (M-2).
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }

  // ── Token + identity helpers ────────────────────────────────────────────

  function readToken() {
    let t = null;
    try {
      t = localStorage.getItem(STORAGE_TOKEN);
      if (!t) t = localStorage.getItem(LEGACY_STORAGE_TOKEN);
    } catch { /* localStorage unavailable */ }
    return t;
  }

  function migrateLegacyKeysReadOnly() {
    // teams.js fully migrates; brain.js only does a read-fallback so users
    // landing here directly without visiting /account/teams/ first still work.
    try {
      if (!localStorage.getItem(STORAGE_TOKEN)) {
        const legacy = localStorage.getItem(LEGACY_STORAGE_TOKEN);
        if (legacy) localStorage.setItem(STORAGE_TOKEN, legacy);
      }
      if (!localStorage.getItem(STORAGE_EMAIL)) {
        const legacyEmail = localStorage.getItem(LEGACY_STORAGE_EMAIL);
        if (legacyEmail) localStorage.setItem(STORAGE_EMAIL, legacyEmail);
      }
    } catch { /* ignore */ }
  }

  function showSignIn() {
    dom.signinSection.hidden = false;
    dom.authSection.hidden = true;
    dom.hdrUser.hidden = true;
  }

  function clearTokenAndShowSignIn() {
    try { localStorage.removeItem(STORAGE_TOKEN); } catch { /* ignore */ }
    showSignIn();
    showToast("Session expired — please sign in again.", "error");
  }

  async function loadIdentityAndMembership() {
    const me = await xbtFetch("/v1/me");
    state.currentUserId = me && me.id;
    const teams = await xbtFetch("/v1/teams/my-teams");
    if (Array.isArray(teams)) {
      const t = teams.find(
        (x) => x.slug === TEAM_SLUG || x.team_slug === TEAM_SLUG,
      );
      if (t) {
        state.membership = { role: t.role || "member", team_id: t.id };
      } else {
        // The user isn't a member of the requested team — degrade to read-only;
        // most likely the API will refuse the data calls anyway.
        state.membership = { role: "member", team_id: null };
      }
    } else {
      state.membership = { role: "member", team_id: null };
    }
  }

  // ── Fetch helper ────────────────────────────────────────────────────────

  async function xbtFetch(path, opts) {
    opts = opts || {};
    const method = opts.method || "GET";
    const body = opts.body || null;
    const skipTeamScope = !!opts.skipTeamScope;
    const token = readToken();
    if (!token) throw makeError("not signed in", 401);

    const headers = {
      Authorization: "Bearer " + token,
    };
    // Phase 11 plan 11-11 Task 6 — admin endpoints reject (or ignore)
    // the X-Team-Scope header. The caller passes skipTeamScope: true
    // when targeting /v1/admin/brain/* in superadmin view.
    if (!skipTeamScope) {
      headers["X-Team-Scope"] = TEAM_SLUG;
    }
    if (body !== null) headers["Content-Type"] = "application/json";

    const r = await fetch(MEMORY_API_BASE + path, {
      method,
      headers,
      body: body !== null ? JSON.stringify(body) : null,
    });
    if (r.status === 401) throw makeError("session expired", 401);
    if (r.status === 204) return null;
    const text = await r.text().catch(() => "");
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = null; }
    }
    if (!r.ok) {
      const detail = (data && (data.detail || data.message)) || text.slice(0, 200);
      throw makeError(detail || ("HTTP " + r.status), r.status, data);
    }
    return data;
  }

  /**
   * brainEventsPath — picks the correct read endpoint for the current view.
   *
   * Returns { path, opts } where opts is passed straight to xbtFetch.
   *
   * Team-scoped view (default): /v1/brain/events?<qs>  + X-Team-Scope.
   * Superadmin view (?as_superadmin=1):
   *   /v1/admin/brain/events?team_slug=<slug>&<qs>  + NO X-Team-Scope.
   *   The admin endpoint writes the audit_log row server-side and
   *   bypasses get_team_scope (which only opens for bridge JWTs).
   */
  function brainEventsPath(qs) {
    if (IS_SUPERADMIN_VIEW) {
      // Prepend team_slug to the existing query string. URLSearchParams
      // tolerates duplicate keys so we just glue them together.
      const adminQs = "team_slug=" + encodeURIComponent(TEAM_SLUG)
        + (qs ? "&" + qs : "");
      return {
        path: "/v1/admin/brain/events?" + adminQs,
        opts: { skipTeamScope: true },
      };
    }
    return {
      path: "/v1/brain/events?" + qs,
      opts: {},
    };
  }

  function makeError(msg, status, body) {
    const e = new Error(msg);
    e.status = status;
    e.body = body || null;
    return e;
  }

  function safeMessage(e) {
    if (!e) return "unknown error";
    return e.message || String(e);
  }

  // ── Filter wiring ───────────────────────────────────────────────────────

  function wireEvents() {
    dom.btnSignout.addEventListener("click", () => {
      if (!confirm("Sign out?")) return;
      try {
        localStorage.removeItem(STORAGE_TOKEN);
        localStorage.removeItem(STORAGE_EMAIL);
      } catch { /* ignore */ }
      showSignIn();
    });

    dom.btnApply.addEventListener("click", onApplyFilters);
    dom.btnReset.addEventListener("click", onResetFilters);
    dom.btnRefresh.addEventListener("click", () => {
      loadEvents({ append: false, cursor: null });
    });

    // Submit filters on Enter inside any text input.
    ["fltSource", "fltCreatedBy", "fltQ"].forEach((k) => {
      dom[k].addEventListener("keydown", (e) => {
        if (e.key === "Enter") onApplyFilters();
      });
    });

    // Bulk actions
    dom.selectAll.addEventListener("change", onSelectAllToggle);
    dom.bulkClear.addEventListener("click", clearSelection);
    dom.bulkDelete.addEventListener("click", onBulkDelete);
    dom.bulkTruth.addEventListener("change", onBulkTruthChange);

    // Single delegated change handler for inline truth_level edits +
    // row checkboxes.
    dom.tbody.addEventListener("change", onTbodyChange);
    // Single delegated click handler for row action buttons.
    dom.tbody.addEventListener("click", onTbodyClick);
    // Track focus on the truth-level dropdown to suppress poll re-renders
    // of a row mid-edit.
    dom.tbody.addEventListener("focusin", onTbodyFocusIn, true);
    dom.tbody.addEventListener("focusout", onTbodyFocusOut, true);

    // Modal dismiss buttons + confirm.
    dom.modal.querySelectorAll("[data-modal-dismiss]").forEach((el) => {
      el.addEventListener("click", closeModal);
    });
    dom.modalConfirm.addEventListener("click", () => {
      const fn = activeModalConfirm;
      activeModalConfirm = null;
      activeModalCancel = null;
      dom.modal.hidden = true;
      if (typeof fn === "function") {
        try { fn(); } catch (e) { showToast("Action failed: " + safeMessage(e), "error"); }
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !dom.modal.hidden) closeModal();
    });
  }

  function onApplyFilters() {
    state.filters.entity_type = readCheckedValues(dom.fltEntityType);
    state.filters.truth_level = readCheckedValues(dom.fltTruthLevel);
    state.filters.source = (dom.fltSource.value || "").trim();
    state.filters.created_by = (dom.fltCreatedBy.value || "").trim();
    state.filters.q = (dom.fltQ.value || "").trim();
    state.filters.include_deleted = dom.fltShowDeleted.checked;
    const since = dom.fltSince.value;
    state.filters.since = since ? new Date(since).toISOString() : null;
    loadEvents({ append: false, cursor: null });
  }

  function onResetFilters() {
    dom.fltEntityType.querySelectorAll("input").forEach((c) => (c.checked = false));
    dom.fltTruthLevel.querySelectorAll("input").forEach((c) => (c.checked = false));
    dom.fltSource.value = "";
    dom.fltCreatedBy.value = "";
    dom.fltQ.value = "";
    dom.fltSince.value = "";
    dom.fltShowDeleted.checked = false;
    state.filters = {
      entity_type: [], truth_level: [], source: "", created_by: "",
      q: "", since: null, include_deleted: false,
    };
    loadEvents({ append: false, cursor: null });
  }

  function readCheckedValues(container) {
    return Array.from(container.querySelectorAll("input:checked")).map((c) => c.value);
  }

  // ── Load events (initial / pagination / refresh) ────────────────────────

  async function loadEvents(opts) {
    opts = opts || {};
    const append = !!opts.append;
    const cursor = opts.cursor || null;

    if (state.loading) return;
    state.loading = true;

    if (!append) {
      // Replacing the feed — clear state but don't drop scroll yet.
      dom.tbody.innerHTML = "";
      state.items = [];
      state.rowsById.clear();
      state.selected.clear();
      updateBulkBar();
      dom.loadingSkel.hidden = false;
      dom.emptyState.hidden = true;
      dom.feedStatus.textContent = "Loading…";
    } else {
      dom.loadMoreStatus.textContent = "Loading more…";
    }

    try {
      const qs = buildQueryString({
        ...state.filters,
        cursor,
        limit: PAGE_SIZE,
      });
      // Route through admin endpoint when in superadmin drill-down view.
      const req = brainEventsPath(qs);
      const r = await xbtFetch(req.path, req.opts);
      const items = (r && r.items) || [];
      state.nextCursor = (r && r.next_cursor) || null;
      state.hasMore = !!state.nextCursor;

      if (append) {
        state.items.push(...items);
        for (const it of items) appendRow(it);
      } else {
        state.items = items;
        renderInitial(items);
      }

      const totalLoaded = state.items.length;
      const totalLabel = (r && r.total != null) ? (r.total + " total") : (totalLoaded + " loaded");
      dom.feedStatus.textContent = totalLabel;
      dom.loadMoreStatus.textContent = state.hasMore
        ? "Scroll for more…"
        : (totalLoaded === 0 ? "" : "End of feed.");
      dom.emptyState.hidden = totalLoaded !== 0;
    } catch (e) {
      if (e && e.status === 401) { clearTokenAndShowSignIn(); return; }
      // Phase 11 plan 11-11 Task 6 — 403 in superadmin view means the
      // viewer is not actually a superadmin. Surface a clear message
      // instead of silently falling back to the team-scoped endpoint
      // (which would leak nothing but would obscure the real problem).
      if (e && e.status === 403 && IS_SUPERADMIN_VIEW) {
        dom.feedStatus.textContent = "Superadmin access denied";
        showToast(
          "You are not a superadmin. The admin endpoint refused this request.",
          "error",
        );
      } else {
        dom.feedStatus.textContent = "Failed: " + safeMessage(e);
        showToast("Failed to load events: " + safeMessage(e), "error");
      }
      dom.emptyState.hidden = state.items.length !== 0;
    } finally {
      state.loading = false;
      dom.loadingSkel.hidden = true;
    }
  }

  function buildQueryString(params) {
    const sp = new URLSearchParams();
    if (Array.isArray(params.entity_type) && params.entity_type.length) {
      for (const v of params.entity_type) sp.append("entity_type", v);
    }
    if (Array.isArray(params.truth_level) && params.truth_level.length) {
      for (const v of params.truth_level) sp.append("truth_level", v);
    }
    if (params.source) sp.set("source", params.source);
    if (params.created_by) {
      const cb = params.created_by === "me" && state.currentUserId
        ? state.currentUserId
        : params.created_by;
      sp.set("created_by", cb);
    }
    if (params.q) sp.set("q", params.q);
    if (params.since) sp.set("since", params.since);
    if (params.include_deleted) sp.set("include_deleted", "true");
    if (params.cursor) sp.set("cursor", params.cursor);
    if (params.limit) sp.set("limit", String(params.limit));
    return sp.toString();
  }

  // ── Render ──────────────────────────────────────────────────────────────

  function compositeKey(item) {
    return item.entity_type + ":" + item.entity_id;
  }

  function renderInitial(items) {
    state.rowsById.clear();
    dom.tbody.innerHTML = "";
    const frag = document.createDocumentFragment();
    for (const it of items) {
      const tr = buildRow(it);
      state.rowsById.set(compositeKey(it), tr);
      frag.appendChild(tr);
    }
    dom.tbody.appendChild(frag);
  }

  function appendRow(item) {
    const tr = buildRow(item);
    state.rowsById.set(compositeKey(item), tr);
    dom.tbody.appendChild(tr);
  }

  function prependRow(item, highlight) {
    const tr = buildRow(item);
    if (highlight) tr.classList.add("is-newly-added");
    state.rowsById.set(compositeKey(item), tr);
    if (dom.tbody.firstChild) {
      dom.tbody.insertBefore(tr, dom.tbody.firstChild);
    } else {
      dom.tbody.appendChild(tr);
    }
  }

  function replaceRow(item) {
    const key = compositeKey(item);
    const old = state.rowsById.get(key);
    if (!old) return;
    const tr = buildRow(item);
    state.rowsById.set(key, tr);
    old.replaceWith(tr);
  }

  function removeRow(item) {
    const key = compositeKey(item);
    const old = state.rowsById.get(key);
    if (!old) return;
    old.remove();
    state.rowsById.delete(key);
    state.selected.delete(key);
    updateBulkBar();
  }

  function buildRow(item) {
    const tr = document.createElement("tr");
    const key = compositeKey(item);
    tr.dataset.key = key;
    tr.dataset.entityType = item.entity_type;
    tr.dataset.entityId = item.entity_id;
    if (item.deleted_at) tr.classList.add("is-deleted");

    const editable = canEdit(item);
    const showBulk = state.membership && state.membership.role === "admin";

    const cells = [];

    // Select checkbox column (admin only).
    cells.push(
      "<td class=\"col-select\"" + (showBulk ? "" : " hidden") + ">"
      + (showBulk
          ? "<input type=\"checkbox\" class=\"row-select\""
            + (state.selected.has(key) ? " checked" : "") + " />"
          : "")
      + "</td>",
    );

    // Entity type.
    cells.push(
      "<td class=\"col-type\">" + escapeHtml(item.entity_type || "—") + "</td>",
    );

    // Preview (truncated 200 chars) — or media thumbnail/chip for media items.
    if (item.media) {
      cells.push("<td class=\"col-preview\">" + mediaCellHtml(item) + "</td>");
    } else {
      cells.push(
        "<td class=\"col-preview\">" + escapeHtml(makePreview(item)) + "</td>",
      );
    }

    // Truth level dropdown.
    let select = "<select class=\"truth-level\"";
    if (!editable) select += " disabled title=\"Only the author or a team admin can edit truth_level.\"";
    select += ">";
    for (const lvl of TRUTH_LEVELS) {
      select += "<option value=\"" + lvl + "\"" + (item.truth_level === lvl ? " selected" : "") + ">" + lvl + "</option>";
    }
    select += "</select>";
    cells.push("<td class=\"col-truth\">" + select + "</td>");

    // Source.
    cells.push(
      "<td class=\"col-source\">" + escapeHtml(item.source || "—") + "</td>",
    );

    // Author.
    cells.push(
      "<td class=\"col-author\" title=\"" + escapeHtml(item.created_by || "") + "\">"
      + escapeHtml(item.created_by_email || item.created_by_display || shortenUUID(item.created_by) || "—")
      + "</td>",
    );

    // Time (relative).
    cells.push(
      "<td class=\"col-time\" title=\"" + escapeHtml(item.created_at || "") + "\">"
      + escapeHtml(formatRelative(item.created_at))
      + "</td>",
    );

    // Actions.
    let actions = "<div class=\"row-actions\">";
    if (item.deleted_at) {
      if (editable) actions += "<button type=\"button\" class=\"btn btn-sm act-restore\">Restore</button>";
    } else {
      if (editable) actions += "<button type=\"button\" class=\"btn btn-sm btn-danger act-delete\">Delete</button>";
    }
    actions += "</div>";
    cells.push("<td class=\"col-actions\">" + actions + "</td>");

    tr.innerHTML = cells.join("");
    return tr;
  }

  function makePreview(item) {
    const raw = item.preview || item.content || item.text || item.title || "";
    const s = String(raw).replace(/\s+/g, " ").trim();
    if (s.length <= PREVIEW_MAX_CHARS) return s || "—";
    return s.slice(0, PREVIEW_MAX_CHARS - 1) + "…";
  }

  /**
   * mediaCellHtml — render an image thumbnail or document chip for media items.
   *
   * Returns raw HTML (NOT escaped) — called only when item.media is set (server-
   * provided, token-signed URL).  Filename is always escapeHtml'd before use in
   * text nodes / attributes.
   *
   * Security: item.media.url comes from the server (already a signed /v1/... path),
   * never from user input.  Only filename text is escaped.
   */
  function mediaCellHtml(item) {
    const media = item.media;
    if (!media || !media.url) return escapeHtml(makePreview(item));
    const href = MEMORY_API_BASE + media.url;
    const safeFilename = escapeHtml(media.filename || "file");
    const mime = media.mime || "";
    if (mime.startsWith("image/")) {
      return (
        "<a href=\"" + href + "\" target=\"_blank\" rel=\"noopener\">"
        + "<img class=\"xb-media-thumb\""
        + " src=\"" + href + "\""
        + " alt=\"" + safeFilename + "\""
        + " loading=\"lazy\" />"
        + "</a>"
      );
    }
    // Non-image: clickable file chip with a document icon.
    return (
      "<a class=\"xb-media-chip\" href=\"" + href + "\" target=\"_blank\" rel=\"noopener\">"
      + "📄 " + safeFilename
      + "</a>"
    );
  }

  function shortenUUID(s) {
    if (!s || typeof s !== "string") return "";
    if (s.length < 12) return s;
    return s.slice(0, 8);
  }

  function formatRelative(iso) {
    if (!iso) return "—";
    const ts = Date.parse(iso);
    if (Number.isNaN(ts)) return iso;
    const diff = Date.now() - ts;
    const s = Math.floor(diff / 1000);
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    const d = Math.floor(h / 24);
    if (d < 30) return d + "d ago";
    const mo = Math.floor(d / 30);
    if (mo < 12) return mo + "mo ago";
    return Math.floor(mo / 12) + "y ago";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ── Authorization gate (M-5) ────────────────────────────────────────────

  function canEdit(item) {
    // Phase 11 plan 11-11 Task 6 — drill-down view is strictly read-only.
    // Admin write endpoints for the cross-team superadmin path are
    // deferred to Phase 12+; until then, hide every edit/delete affordance
    // even if the viewer happens to be the author (e.g. seeing their own
    // ingested LibreChat message in a team they aren't a member of).
    if (IS_SUPERADMIN_VIEW) return false;
    if (!state.membership) return false;
    if (state.membership.role === "admin") return true;
    if (state.currentUserId && item.created_by === state.currentUserId) return true;
    return false;
  }

  // ── Delegated handlers ──────────────────────────────────────────────────

  function findItemByRow(tr) {
    if (!tr || !tr.dataset) return null;
    const key = tr.dataset.key;
    if (!key) return null;
    return state.items.find((it) => compositeKey(it) === key) || null;
  }

  function onTbodyChange(ev) {
    const target = ev.target;
    const tr = target && target.closest && target.closest("tr");
    if (!tr) return;
    const item = findItemByRow(tr);
    if (!item) return;

    if (target.classList.contains("truth-level")) {
      // Defensive M-5 guard: even if the user re-enabled the disabled
      // select via DevTools, the handler short-circuits when canEdit
      // is false (no PATCH fires).
      if (!canEdit(item)) {
        // Revert the select to the stored value visually.
        target.value = item.truth_level;
        return;
      }
      const newValue = target.value;
      if (newValue === item.truth_level) return;
      patchTruthLevel(item, newValue, target);
      return;
    }

    if (target.classList.contains("row-select")) {
      const key = compositeKey(item);
      if (target.checked) state.selected.add(key);
      else state.selected.delete(key);
      updateBulkBar();
      return;
    }
  }

  function onTbodyClick(ev) {
    const target = ev.target;
    if (!target || !target.closest) return;
    const tr = target.closest("tr");
    if (!tr) return;
    const item = findItemByRow(tr);
    if (!item) return;

    if (target.classList.contains("act-delete")) {
      onDeleteClick(item);
    } else if (target.classList.contains("act-restore")) {
      onRestoreClick(item);
    }
  }

  function onTbodyFocusIn(ev) {
    const tr = ev.target && ev.target.closest && ev.target.closest("tr");
    if (!tr || !tr.dataset || !tr.dataset.key) return;
    if (ev.target.classList.contains("truth-level")) {
      state.activeEditRowId = tr.dataset.key;
    }
  }

  function onTbodyFocusOut(ev) {
    if (ev.target && ev.target.classList && ev.target.classList.contains("truth-level")) {
      // Clear shortly after blur so a poll firing during the same tick
      // doesn't replace the row.
      setTimeout(() => { state.activeEditRowId = null; }, 250);
    }
  }

  // ── PATCH truth_level (M-5: 403 has explicit wording) ───────────────────

  async function patchTruthLevel(item, newValue, selectEl) {
    const prev = item.truth_level;
    // Optimistic update — replace in state + visual saving marker.
    item.truth_level = newValue;
    if (selectEl) selectEl.classList.add("is-saving");

    try {
      const updated = await xbtFetch(
        "/v1/brain/events/" + encodeURIComponent(item.entity_type) +
        "/" + encodeURIComponent(item.entity_id),
        { method: "PATCH", body: { truth_level: newValue } },
      );
      // On 200, replace the local item with whatever the server returned
      // (timestamps, computed columns).
      if (updated && typeof updated === "object") {
        Object.assign(item, updated);
      }
      if (selectEl) selectEl.classList.remove("is-saving");
      showToast("Truth level updated to " + newValue, "success");
    } catch (e) {
      // Rollback the in-state value AND the visible <select>.
      item.truth_level = prev;
      if (selectEl) {
        selectEl.classList.remove("is-saving");
        selectEl.value = prev;
      }
      if (e && e.status === 403) {
        showToast(
          "You can only edit items you created. Contact a team admin to modify items created by others.",
          "error",
        );
      } else if (e && e.status >= 500) {
        showToast("Server error — please retry", "error");
      } else {
        showToast("Failed to update truth level", "error");
      }
    }
  }

  // ── Soft delete + restore ───────────────────────────────────────────────

  function onDeleteClick(item) {
    if (!canEdit(item)) {
      showToast(
        "You can only delete items you created. Contact a team admin to delete items created by others.",
        "error",
      );
      return;
    }
    openModal({
      title: "Delete this " + item.entity_type + "?",
      body:
        "This will be purged permanently in 30 days unless restored. " +
        "Other team members will no longer see it in the default view.",
      confirmLabel: "Delete",
      onConfirm: () => softDelete(item),
    });
  }

  async function softDelete(item) {
    try {
      await xbtFetch(
        "/v1/brain/events/" + encodeURIComponent(item.entity_type) +
        "/" + encodeURIComponent(item.entity_id),
        { method: "DELETE" },
      );
      // Mark in state.
      item.deleted_at = new Date().toISOString();
      if (state.filters.include_deleted) {
        // Trash view → just re-render the row to update its class + actions.
        replaceRow(item);
      } else {
        // Default view → drop the row entirely.
        removeRow(item);
        state.items = state.items.filter(
          (x) => compositeKey(x) !== compositeKey(item),
        );
      }
      showToast("Item deleted (30-day window before purge).", "success");
    } catch (e) {
      if (e && e.status === 403) {
        showToast(
          "You can only delete items you created. Contact a team admin to delete items created by others.",
          "error",
        );
      } else if (e && e.status >= 500) {
        showToast("Server error — please retry", "error");
      } else {
        showToast("Failed to delete: " + safeMessage(e), "error");
      }
    }
  }

  function onRestoreClick(item) {
    if (!canEdit(item)) {
      showToast(
        "You can only restore items you created. Contact a team admin to restore items created by others.",
        "error",
      );
      return;
    }
    restoreItem(item);
  }

  async function restoreItem(item) {
    try {
      const updated = await xbtFetch(
        "/v1/brain/events/" + encodeURIComponent(item.entity_type) +
        "/" + encodeURIComponent(item.entity_id) + "/restore",
        { method: "POST" },
      );
      if (updated && typeof updated === "object") {
        Object.assign(item, updated);
      }
      item.deleted_at = null;
      replaceRow(item);
      showToast("Item restored.", "success");
    } catch (e) {
      if (e && e.status === 410) {
        showToast("Retention window expired — item cannot be restored.", "error");
      } else if (e && e.status === 403) {
        showToast(
          "You can only restore items you created. Contact a team admin to restore items created by others.",
          "error",
        );
      } else if (e && e.status >= 500) {
        showToast("Server error — please retry", "error");
      } else {
        showToast("Failed to restore: " + safeMessage(e), "error");
      }
    }
  }

  // ── Bulk actions (admin only) ───────────────────────────────────────────

  function onSelectAllToggle(ev) {
    const checked = !!ev.target.checked;
    dom.tbody.querySelectorAll(".row-select").forEach((cb) => {
      cb.checked = checked;
      const tr = cb.closest("tr");
      if (!tr || !tr.dataset.key) return;
      if (checked) state.selected.add(tr.dataset.key);
      else state.selected.delete(tr.dataset.key);
    });
    updateBulkBar();
  }

  function clearSelection() {
    state.selected.clear();
    dom.tbody.querySelectorAll(".row-select").forEach((cb) => { cb.checked = false; });
    dom.selectAll.checked = false;
    updateBulkBar();
  }

  function updateBulkBar() {
    if (!state.membership || state.membership.role !== "admin") {
      dom.bulkBar.hidden = true;
      return;
    }
    const n = state.selected.size;
    if (n === 0) {
      dom.bulkBar.hidden = true;
      return;
    }
    dom.bulkBar.hidden = false;
    dom.bulkCount.textContent = n + " selected";
  }

  async function onBulkDelete() {
    const keys = Array.from(state.selected);
    if (!keys.length) return;
    openModal({
      title: "Delete " + keys.length + " selected item(s)?",
      body: "Items will be purged permanently in 30 days unless restored.",
      confirmLabel: "Delete " + keys.length,
      onConfirm: () => runBulk(keys, "delete"),
    });
  }

  async function onBulkTruthChange(ev) {
    const newValue = ev.target.value;
    if (!newValue) return;
    const keys = Array.from(state.selected);
    if (!keys.length) {
      ev.target.value = "";
      return;
    }
    openModal({
      title: "Set truth level to " + newValue + " on " + keys.length + " item(s)?",
      body: "This applies the change to all selected items.",
      confirmLabel: "Apply",
      onConfirm: () => runBulk(keys, "truth", newValue),
      onCancel: () => { ev.target.value = ""; },
    });
  }

  async function runBulk(keys, op, newTruth) {
    let success = 0;
    const failures = [];
    const queue = keys.slice();
    async function worker() {
      while (queue.length) {
        const key = queue.shift();
        const item = state.items.find((it) => compositeKey(it) === key);
        if (!item) continue;
        try {
          if (op === "delete") {
            await xbtFetch(
              "/v1/brain/events/" + encodeURIComponent(item.entity_type) +
              "/" + encodeURIComponent(item.entity_id),
              { method: "DELETE" },
            );
            item.deleted_at = new Date().toISOString();
            if (state.filters.include_deleted) replaceRow(item);
            else { removeRow(item); state.items = state.items.filter((x) => compositeKey(x) !== key); }
          } else if (op === "truth" && newTruth) {
            const updated = await xbtFetch(
              "/v1/brain/events/" + encodeURIComponent(item.entity_type) +
              "/" + encodeURIComponent(item.entity_id),
              { method: "PATCH", body: { truth_level: newTruth } },
            );
            if (updated && typeof updated === "object") Object.assign(item, updated);
            else item.truth_level = newTruth;
            replaceRow(item);
          }
          success += 1;
        } catch (e) {
          failures.push({ key, status: e && e.status, msg: safeMessage(e) });
        }
      }
    }
    const workers = [];
    for (let i = 0; i < Math.min(BULK_CONCURRENCY, keys.length); i += 1) {
      workers.push(worker());
    }
    await Promise.all(workers);

    if (op === "truth") dom.bulkTruth.value = "";
    clearSelection();

    if (failures.length === 0) {
      showToast(
        op === "delete"
          ? success + " item(s) deleted."
          : success + " item(s) updated to " + newTruth + ".",
        "success",
      );
    } else {
      const sample = failures.slice(0, 3)
        .map((f) => "[" + (f.status || "?") + "] " + (f.msg || ""))
        .join("; ");
      showToast(
        success + "/" + (success + failures.length)
        + " succeeded, " + failures.length + " failed: " + sample
        + (failures.length > 3 ? "…" : ""),
        "error",
      );
    }
  }

  // ── Polling (M-2) ───────────────────────────────────────────────────────

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(pollForNewItems, POLL_INTERVAL_MS);
    setPollIndicator("live", false);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "hidden") {
      stopPolling();
      state.pollPaused = true;
      setPollIndicator("paused", false);
    } else if (state.pollPaused) {
      state.pollPaused = false;
      // Fire one poll immediately to catch up on what we missed.
      pollForNewItems().finally(startPolling);
    }
  }

  /**
   * pollForNewItems — fires every POLL_INTERVAL_MS while the tab is visible.
   * Uses since={newest_created_at} so the server only returns rows with
   * created_at > since (strict greater-than). Prepends to the top.
   * Does NOT touch state.nextCursor and does NOT call loadEvents.
   * Honors all active state.filters so the live feed matches the user's
   * current view.
   */
  async function pollForNewItems() {
    if (state.loading) return;
    if (state.activeEditRowId) {
      // Defer one cycle if the user is mid-edit, to avoid yanking focus.
      return;
    }
    const newest = state.items[0] && state.items[0].created_at;
    if (!newest) return; // empty feed → wait for user-driven load
    try {
      const qs = buildQueryString({
        ...state.filters,
        since: newest,
        limit: PAGE_SIZE,
      });
      // Polling honours the same endpoint substitution as initial load
      // — every poll cycle in superadmin view writes a fresh audit_log
      // row server-side. The 30s cadence is intentional (CONTEXT lock).
      const req = brainEventsPath(qs);
      const r = await xbtFetch(req.path, req.opts);
      const items = (r && r.items) || [];
      if (!items.length) {
        state.pollErrorStreak = 0;
        setPollIndicator("live", false);
        return;
      }
      // Skip duplicates if the server's strict > somehow returns an existing
      // row (defensive).
      const known = new Set(state.items.map(compositeKey));
      const fresh = items.filter((it) => !known.has(compositeKey(it)));
      if (!fresh.length) return;
      // Prepend in chronological order (oldest of the new batch first so
      // the very newest ends up on top after iterative prepends).
      // The API returns newest-first by default; reverse to walk old → new
      // and prepend each.
      const ordered = fresh.slice().reverse();
      state.items = fresh.concat(state.items);
      for (const it of ordered) prependRow(it, true);
      state.pollErrorStreak = 0;
      setPollIndicator("live", false);
      const total = state.items.length;
      dom.feedStatus.textContent = total + " loaded · " + fresh.length + " new";
    } catch (e) {
      if (e && e.status === 401) { clearTokenAndShowSignIn(); return; }
      state.pollErrorStreak += 1;
      setPollIndicator("error", true);
      // Don't toast every cycle — only the first error in a streak.
      if (state.pollErrorStreak === 1) {
        showToast("Live feed paused — couldn't reach the server.", "warn");
      }
    }
  }

  function setPollIndicator(mode, isError) {
    if (!dom.pollIndicator) return;
    dom.pollIndicator.classList.toggle("is-paused", mode === "paused");
    dom.pollIndicator.classList.toggle("is-error", !!isError);
    if (dom.pollLabel) {
      dom.pollLabel.textContent =
        mode === "paused" ? "paused" :
        mode === "error"  ? "error"  : "live";
    }
  }

  // ── IntersectionObserver pagination ────────────────────────────────────

  function setupIntersectionObserver() {
    if (state.intersectionObserver) state.intersectionObserver.disconnect();
    if (!("IntersectionObserver" in window)) return;
    state.intersectionObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && state.hasMore && !state.loading) {
          loadEvents({ append: true, cursor: state.nextCursor });
        }
      }
    }, { rootMargin: "100px" });
    state.intersectionObserver.observe(dom.sentinel);
  }

  // ── Toasts ──────────────────────────────────────────────────────────────

  function showToast(msg, kind) {
    if (!dom.toastStack) return;
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " is-" + kind : "");
    el.textContent = msg;
    dom.toastStack.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.3s ease";
      setTimeout(() => el.remove(), 350);
    }, kind === "error" ? 6000 : 3500);
  }

  // ── Modal ──────────────────────────────────────────────────────────────

  let activeModalConfirm = null;
  let activeModalCancel = null;

  function openModal(opts) {
    dom.modalTitle.textContent = opts.title || "Confirm";
    dom.modalBody.textContent = opts.body || "";
    dom.modalConfirm.textContent = opts.confirmLabel || "Confirm";
    activeModalConfirm = opts.onConfirm || null;
    activeModalCancel = opts.onCancel || null;
    dom.modal.hidden = false;
    setTimeout(() => dom.modalConfirm.focus(), 0);
  }

  function closeModal() {
    dom.modal.hidden = true;
    if (typeof activeModalCancel === "function") {
      try { activeModalCancel(); } catch { /* swallow */ }
    }
    activeModalConfirm = null;
    activeModalCancel = null;
  }

})();

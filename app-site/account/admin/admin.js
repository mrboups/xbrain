/**
 * admin.js — app-site/account/admin/ superadmin dashboard (Phase 11, plan 11-11).
 *
 * Phase 11 plan 11-11 — Task 1 baseline: page shell, auth + superadmin probe,
 * 403 fallback, parallel section loader orchestration. Section renderers are
 * wired by subsequent tasks (Task 2 overview, Task 3 storage, Task 4 activity
 * with sparklines, Task 5 sources).
 *
 * Auth: reads xbt_token from localStorage (canonical key minted by teams.js).
 *   - No token → redirect to /account/teams/ (let the existing sign-in flow take over).
 *   - 403 from probe → render the "Access denied" fallback panel; no further fetches.
 *
 * Superadmin detection (PROBE STRATEGY): a successful GET /v1/admin/brain/overview
 * proves the principal is a superadmin (the endpoint is gated by
 * assert_is_superadmin server-side). We cache the response into state.overview so
 * the probe doubles as the first data load — saves a round trip on the happy path.
 *
 * Rationale for probe over a new /v1/me field:
 *   1. Zero backend change required.
 *   2. The probe IS the first data fetch — cached, so 1 round trip not 2.
 *   3. Failure modes are explicit (403 → fallback, other → error panel).
 *   4. Forward-compatible: if a future phase adds /v1/me { is_superadmin },
 *      this same probe pattern remains a valid fallback.
 *
 * No real-time polling on this page (manual refresh = page reload).
 */

// ── Config ────────────────────────────────────────────────────────────────
const MEMORY_API_BASE = "https://api.grooveos.app";

// Canonical localStorage keys (shared with brain.js + teams.js).
const STORAGE_TOKEN = "xbt_token";
const STORAGE_EMAIL = "user_sub";
const LEGACY_STORAGE_TOKEN = "xbrain_xbt_token";
const LEGACY_STORAGE_EMAIL = "xbrain_user_email";

// ── State ────────────────────────────────────────────────────────────────
const state = {
  xbt: null,
  email: null,
  isSuperadmin: null, // null = unknown, true/false after probe
  overview: null,
  storage: null,
  activity: null,
  sources: null,
};

// ── Token helpers ─────────────────────────────────────────────────────────
function readToken() {
  try {
    return (
      localStorage.getItem(STORAGE_TOKEN) ||
      localStorage.getItem(LEGACY_STORAGE_TOKEN) ||
      null
    );
  } catch {
    return null;
  }
}

function readEmail() {
  try {
    return (
      localStorage.getItem(STORAGE_EMAIL) ||
      localStorage.getItem(LEGACY_STORAGE_EMAIL) ||
      "signed in"
    );
  } catch {
    return "signed in";
  }
}

// ── Generic fetch helper ─────────────────────────────────────────────────
async function adminFetch(path) {
  const resp = await fetch(MEMORY_API_BASE + path, {
    headers: { Authorization: `Bearer ${state.xbt}` },
  });
  if (resp.status === 401) {
    // Token invalid — clear it and redirect to sign in.
    try {
      localStorage.removeItem(STORAGE_TOKEN);
    } catch {
      /* ignore */
    }
    location.href = "/account/teams/";
    throw new Error("session expired");
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    const err = new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

// ── Probe (superadmin gate + first data load) ────────────────────────────
async function probeSuperadmin() {
  try {
    state.overview = await adminFetch("/v1/admin/brain/overview");
    state.isSuperadmin = true;
    return true;
  } catch (exc) {
    if (exc.status === 403) {
      state.isSuperadmin = false;
      return false;
    }
    throw exc;
  }
}

// ── Section loaders (called in parallel after a successful probe) ────────
async function loadStorage() {
  state.storage = await adminFetch("/v1/admin/brain/storage");
}
async function loadActivity() {
  state.activity = await adminFetch("/v1/admin/brain/activity?days=30");
}
async function loadSources() {
  state.sources = await adminFetch("/v1/admin/brain/sources?days=30");
}

// ── HTML helpers (shared across renderers) ───────────────────────────────
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function shortenUUID(s) {
  if (!s || typeof s !== "string") return "";
  if (s.length < 12) return s;
  return s.slice(0, 8);
}

function buildDrillDownCell(slug) {
  return `<td class="actions"><button type="button" class="drill-down" data-team="${escapeHtml(
    slug,
  )}">Drill down &rarr;</button></td>`;
}

function wireDrillDown(root) {
  root.querySelectorAll(".drill-down").forEach((btn) => {
    btn.addEventListener("click", () => {
      const team = btn.dataset.team;
      if (!team) return;
      // brain.js (11-08) reads as_superadmin=1 + team slug from the URL,
      // renders the banner, and routes all reads through
      // /v1/admin/brain/events (which writes the audit row server-side).
      const url = `/account/teams/brain/?team=${encodeURIComponent(
        team,
      )}&as_superadmin=1`;
      location.href = url;
    });
  });
}

// ── Section renderers — wired by Tasks 2-5 ───────────────────────────────
function renderOverview() {
  const root = document.getElementById("overview-content");
  const data = state.overview;
  if (!root) return;
  if (!data || data.length === 0) {
    root.innerHTML = `<p class="empty">No teams found.</p>`;
    return;
  }
  // Discover entity_types dynamically (don't hardcode the 7-arm list —
  // if a future view migration adds an arm, the matrix expands).
  const entityTypes = Array.from(
    new Set(data.flatMap((t) => Object.keys(t.counts || {}))),
  ).sort();

  let html = `<table class="admin-table"><thead><tr><th>Team</th>`;
  for (const et of entityTypes) {
    html += `<th class="count">${escapeHtml(et)}</th>`;
  }
  html += `<th class="count">Total</th><th>Actions</th></tr></thead><tbody>`;

  for (const team of data) {
    let rowTotal = 0;
    html += `<tr><td class="team-name">${escapeHtml(
      team.team_slug,
    )}<span class="team-uuid">${escapeHtml(
      shortenUUID(team.team_id),
    )}</span></td>`;
    for (const et of entityTypes) {
      const truths = (team.counts && team.counts[et]) || {};
      const total = Object.values(truths).reduce((a, b) => a + b, 0);
      rowTotal += total;
      // Tooltip exposes per-truth_level breakdown on hover.
      const tooltipEntries = Object.entries(truths);
      const tooltip = tooltipEntries.length
        ? tooltipEntries.map(([tl, c]) => `${tl}: ${c}`).join("\n")
        : "0";
      html += `<td class="count${
        total === 0 ? " zero" : ""
      }" title="${escapeHtml(tooltip)}">${total}</td>`;
    }
    html += `<td class="count"><strong>${rowTotal}</strong></td>`;
    html += buildDrillDownCell(team.team_slug);
    html += `</tr>`;
  }
  html += `</tbody></table>`;
  root.innerHTML = html;
  wireDrillDown(root);
}

function renderStorage() {
  const root = document.getElementById("storage-content");
  if (root) root.innerHTML = `<p class="empty">Storage renderer not wired yet.</p>`;
}
function renderActivity() {
  const root = document.getElementById("activity-content");
  if (root) root.innerHTML = `<p class="empty">Activity renderer not wired yet.</p>`;
}
function renderSources() {
  const root = document.getElementById("sources-content");
  if (root) root.innerHTML = `<p class="empty">Sources renderer not wired yet.</p>`;
}

// ── Boot ──────────────────────────────────────────────────────────────────
function showForbidden(reason) {
  const fb = document.getElementById("forbidden-fallback");
  const app = document.getElementById("app");
  if (!fb || !app) return;
  if (reason) {
    const r = fb.querySelector(".reason");
    if (r) r.textContent = reason;
  }
  fb.hidden = false;
  app.hidden = true;
}

function showApp() {
  const fb = document.getElementById("forbidden-fallback");
  const app = document.getElementById("app");
  if (fb) fb.hidden = true;
  if (app) app.hidden = false;
}

function wireHeader() {
  state.email = readEmail();
  const emailEl = document.getElementById("hdr-email");
  if (emailEl) emailEl.textContent = state.email;
  const user = document.getElementById("hdr-user");
  if (user) user.hidden = false;
  const signout = document.getElementById("btn-signout");
  if (signout) {
    signout.addEventListener("click", () => {
      if (!confirm("Sign out?")) return;
      try {
        localStorage.removeItem(STORAGE_TOKEN);
        localStorage.removeItem(STORAGE_EMAIL);
      } catch {
        /* ignore */
      }
      location.href = "/account/teams/";
    });
  }
}

async function init() {
  state.xbt = readToken();
  if (!state.xbt) {
    // No token → kick to teams page (existing sign-in flow handles it).
    location.href = "/account/teams/";
    return;
  }
  wireHeader();

  try {
    const ok = await probeSuperadmin();
    if (!ok) {
      showForbidden(
        "You are not a superadmin. This dashboard is restricted.",
      );
      return;
    }
    showApp();
    // Fan out the remaining 3 fetches in parallel; overview is already cached.
    await Promise.all([
      loadStorage().catch((e) => {
        console.error("admin: storage load failed", e);
      }),
      loadActivity().catch((e) => {
        console.error("admin: activity load failed", e);
      }),
      loadSources().catch((e) => {
        console.error("admin: sources load failed", e);
      }),
    ]);
    renderOverview();
    renderStorage();
    renderActivity();
    renderSources();
  } catch (exc) {
    console.error("admin init failed", exc);
    showForbidden(`Error loading dashboard: ${exc.message || exc}`);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

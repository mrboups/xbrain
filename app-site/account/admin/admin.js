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
import { renderSparkline } from "/account/admin/sparkline.js";

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

// ── Human-readable byte formatter (Task 3) ───────────────────────────────
function humanBytes(n) {
  if (n === null || n === undefined) return "N/A";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(
    units.length - 1,
    Math.floor(Math.log(n) / Math.log(1024)),
  );
  return `${(n / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function renderStorage() {
  const root = document.getElementById("storage-content");
  const data = state.storage;
  if (!root) return;
  if (!data || data.length === 0) {
    root.innerHTML = `<p class="empty">No storage data.</p>`;
    return;
  }

  let totalPgRows = 0;
  let totalQdrant = 0;
  let totalMinio = 0;
  let qdrantHasNull = false;
  let minioHasNull = false;

  let html = `<table class="admin-table"><thead><tr>
    <th>Team</th>
    <th class="count">PG rows</th>
    <th class="count">Qdrant points</th>
    <th class="bytes">MinIO bytes</th>
    <th>Actions</th>
  </tr></thead><tbody>`;

  for (const team of data) {
    const pgRows = team.pg_rows || {};
    const pgTotal = Object.values(pgRows).reduce((a, b) => a + b, 0);
    totalPgRows += pgTotal;
    // Tooltip exposes per-table breakdown on hover.
    const pgEntries = Object.entries(pgRows);
    const pgTooltip = pgEntries.length
      ? pgEntries.map(([tbl, c]) => `${tbl}: ${c}`).join("\n")
      : "0";

    const qd = team.qdrant_points;
    if (qd === null || qd === undefined) qdrantHasNull = true;
    else totalQdrant += qd;
    const qdCell =
      qd === null || qd === undefined ? "N/A" : qd.toLocaleString();

    const mb = team.minio_bytes;
    if (mb === null || mb === undefined) minioHasNull = true;
    else totalMinio += mb;
    const mbCell = humanBytes(mb);

    html += `<tr>
      <td class="team-name">${escapeHtml(
        team.team_slug,
      )}<span class="team-uuid">${escapeHtml(shortenUUID(team.team_id))}</span></td>
      <td class="count" title="${escapeHtml(pgTooltip)}">${pgTotal.toLocaleString()}</td>
      <td class="count">${qdCell}</td>
      <td class="bytes">${escapeHtml(mbCell)}</td>
      ${buildDrillDownCell(team.team_slug)}
    </tr>`;
  }

  // Total row aggregates across teams. Null cells (N/A) are excluded
  // from the running total; an asterisk warns the operator.
  html += `<tr class="total-row">
    <td class="team-name">All teams</td>
    <td class="count">${totalPgRows.toLocaleString()}</td>
    <td class="count">${
      qdrantHasNull
        ? `${totalQdrant.toLocaleString()}*`
        : totalQdrant.toLocaleString()
    }</td>
    <td class="bytes">${
      minioHasNull ? `${humanBytes(totalMinio)}*` : humanBytes(totalMinio)
    }</td>
    <td class="actions"></td>
  </tr>`;
  html += `</tbody></table>`;
  if (qdrantHasNull || minioHasNull) {
    html += `<p class="section-desc"><code>*</code> Some teams returned <code>N/A</code> (backing store unavailable); totals exclude those rows.</p>`;
  }
  root.innerHTML = html;
  wireDrillDown(root);
}
function renderActivity() {
  const root = document.getElementById("activity-content");
  const data = state.activity;
  if (!root) return;
  if (!data || data.length === 0) {
    root.innerHTML = `<p class="empty">No activity data.</p>`;
    return;
  }

  let html = `<table class="admin-table"><thead><tr>
    <th>Team</th>
    <th>30-day sparkline</th>
    <th class="count">Total events</th>
    <th>Actions</th>
  </tr></thead><tbody>`;

  for (const team of data) {
    const total = (team.daily || []).reduce(
      (a, b) => a + (Number(b.events) || 0),
      0,
    );
    html += `<tr>
      <td class="team-name">${escapeHtml(
        team.team_slug,
      )}<span class="team-uuid">${escapeHtml(shortenUUID(team.team_id))}</span></td>
      <td><div class="sparkline-cell" data-team="${escapeHtml(team.team_slug)}"></div></td>
      <td class="total-events">${total.toLocaleString()}</td>
      ${buildDrillDownCell(team.team_slug)}
    </tr>`;
  }
  html += `</tbody></table>`;
  root.innerHTML = html;

  // Render one sparkline per team into its placeholder div. CSS.escape
  // is required because team slugs may contain selector-special chars.
  for (const team of data) {
    const cell = root.querySelector(
      `.sparkline-cell[data-team="${CSS.escape(team.team_slug)}"]`,
    );
    if (cell) {
      renderSparkline(cell, team.daily || [], {
        width: 200,
        height: 40,
        color: "currentColor",
      });
    }
  }
  wireDrillDown(root);
}
function renderSources() {
  const root = document.getElementById("sources-content");
  const data = state.sources;
  if (!root) return;
  if (!data || data.length === 0) {
    root.innerHTML = `<p class="empty">No source data.</p>`;
    return;
  }

  // First pass: aggregate global source counts to pick the top-5 columns.
  // Sources beyond rank 5 are lumped into an "other" column to keep the
  // column count bounded regardless of how many distinct sources exist.
  const globalCounts = {};
  for (const team of data) {
    const sources = team.sources || {};
    for (const [label, count] of Object.entries(sources)) {
      globalCounts[label] = (globalCounts[label] || 0) + count;
    }
  }
  const sortedSources = Object.entries(globalCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([label]) => label);
  const topSources = sortedSources.slice(0, 5);
  const hasOther = sortedSources.length > 5;
  const otherSet = new Set(sortedSources.slice(5));

  let html = `<table class="admin-table"><thead><tr><th>Team</th>`;
  for (const src of topSources) {
    html += `<th class="source-col">${escapeHtml(src)}</th>`;
  }
  if (hasOther) html += `<th class="source-col">other</th>`;
  html += `<th class="count">Total</th><th>Actions</th></tr></thead><tbody>`;

  // Per-column totals for the bottom row.
  const colTotals = Object.fromEntries(topSources.map((s) => [s, 0]));
  let otherTotal = 0;
  let grandTotal = 0;

  for (const team of data) {
    const sources = team.sources || {};
    let rowTotal = 0;
    let rowOther = 0;
    html += `<tr><td class="team-name">${escapeHtml(
      team.team_slug,
    )}<span class="team-uuid">${escapeHtml(
      shortenUUID(team.team_id),
    )}</span></td>`;
    for (const src of topSources) {
      const c = Number(sources[src] || 0);
      colTotals[src] += c;
      rowTotal += c;
      html += `<td class="source-cell${
        c === 0 ? " zero" : ""
      }">${c.toLocaleString()}</td>`;
    }
    if (hasOther) {
      for (const [label, count] of Object.entries(sources)) {
        if (otherSet.has(label)) rowOther += Number(count) || 0;
      }
      otherTotal += rowOther;
      rowTotal += rowOther;
      html += `<td class="source-cell other">${rowOther.toLocaleString()}</td>`;
    }
    grandTotal += rowTotal;
    html += `<td class="count"><strong>${rowTotal.toLocaleString()}</strong></td>`;
    html += buildDrillDownCell(team.team_slug);
    html += `</tr>`;
  }

  // Total row aggregates across teams.
  html += `<tr class="total-row"><td class="team-name">All teams</td>`;
  for (const src of topSources) {
    html += `<td class="source-cell">${colTotals[src].toLocaleString()}</td>`;
  }
  if (hasOther) {
    html += `<td class="source-cell other">${otherTotal.toLocaleString()}</td>`;
  }
  html += `<td class="count">${grandTotal.toLocaleString()}</td><td class="actions"></td></tr>`;
  html += `</tbody></table>`;
  root.innerHTML = html;
  wireDrillDown(root);
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

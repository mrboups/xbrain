/**
 * wipe.js — Danger zone (superadmin wipe operations) for /account/admin/.
 *
 * Loaded as an ES module alongside admin.js. Self-contained: hydrates the
 * #danger-zone section, wires its own modal, and uses the canonical
 * localStorage xbt_token key (same as admin.js).
 *
 * Backend endpoints :
 *   POST /v1/admin/wipe-team/{team_slug}   body: {confirm: "<team_slug>"}
 *   POST /v1/admin/wipe-database           body: {confirm: "WIPE EVERYTHING"}
 *
 * Both are gated by Depends(assert_is_superadmin); if the xbt_ token holder
 * is not in ADMIN_USER_SUBS the response is 403 and we surface the error.
 */

const MEMORY_API_BASE = "https://api.grooveos.app";
const STORAGE_TOKEN = "xbt_token";
const LEGACY_STORAGE_TOKEN = "xbrain_xbt_token";

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

async function apiFetch(path, { method = "GET", body = null } = {}) {
  const token = readToken();
  if (!token) throw new Error("not signed in");
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const r = await fetch(MEMORY_API_BASE + path, opts);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    const err = new Error(`HTTP ${r.status}: ${text.slice(0, 400)}`);
    err.status = r.status;
    err.body = text;
    throw err;
  }
  if (r.status === 204) return null;
  return r.json();
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Modal ─────────────────────────────────────────────────────────────────

let modalCtx = null;

function ensureModal() {
  let modal = document.getElementById("wipe-modal");
  if (modal) return modal;
  modal = document.createElement("div");
  modal.id = "wipe-modal";
  modal.className = "wipe-modal";
  modal.hidden = true;
  modal.innerHTML = `
    <div class="wipe-modal__panel">
      <h3 id="wipe-modal-title">Confirm</h3>
      <p id="wipe-modal-body" class="wipe-modal__body"></p>
      <p class="wipe-modal__hint">
        Type <code id="wipe-modal-required">…</code> to enable the confirm button.
      </p>
      <input type="text" id="wipe-modal-input" class="wipe-modal__input"
             autocomplete="off" spellcheck="false" />
      <div class="wipe-modal__actions">
        <button class="btn" id="wipe-modal-cancel">Cancel</button>
        <button class="btn btn-danger" id="wipe-modal-confirm" disabled>Confirm</button>
      </div>
      <div id="wipe-modal-error" class="wipe-modal__error" hidden></div>
    </div>
  `;
  document.body.appendChild(modal);
  const input = modal.querySelector("#wipe-modal-input");
  const confirmBtn = modal.querySelector("#wipe-modal-confirm");
  const cancelBtn = modal.querySelector("#wipe-modal-cancel");
  input.addEventListener("input", () => {
    if (!modalCtx) return;
    confirmBtn.disabled = input.value !== modalCtx.required;
  });
  cancelBtn.addEventListener("click", closeModal);
  confirmBtn.addEventListener("click", async () => {
    if (!modalCtx || input.value !== modalCtx.required) return;
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    try { await modalCtx.onConfirm(); }
    finally {
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  });
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) closeModal();
  });
  return modal;
}

function openModal({ title, body, required, onConfirm }) {
  const modal = ensureModal();
  modalCtx = { required, onConfirm };
  modal.querySelector("#wipe-modal-title").textContent = title;
  modal.querySelector("#wipe-modal-body").textContent = body;
  modal.querySelector("#wipe-modal-required").textContent = required;
  const input = modal.querySelector("#wipe-modal-input");
  input.value = "";
  input.placeholder = required;
  modal.querySelector("#wipe-modal-confirm").disabled = true;
  const err = modal.querySelector("#wipe-modal-error");
  err.hidden = true;
  err.textContent = "";
  modal.hidden = false;
  setTimeout(() => input.focus(), 30);
}

function closeModal() {
  modalCtx = null;
  const modal = document.getElementById("wipe-modal");
  if (modal) modal.hidden = true;
}

function showModalError(msg) {
  const modal = document.getElementById("wipe-modal");
  if (!modal) return;
  const err = modal.querySelector("#wipe-modal-error");
  err.textContent = msg;
  err.hidden = false;
}

// ── Section render ────────────────────────────────────────────────────────

export function renderDangerZone(container) {
  container.innerHTML = `
    <h2>Danger zone</h2>
    <p class="section-desc">
      These operations permanently delete data. Backups are kept in GCS
      if you need to restore. Read each modal carefully before confirming.
    </p>
    <div class="danger-grid">
      <div class="danger-card">
        <div class="danger-card__head">
          <span class="danger-icon" aria-hidden="true">!</span>
          <h3>Wipe a team</h3>
        </div>
        <p>
          Deletes one team and all its scoped data: memberships,
          conversations, memory items, tasks, contacts, integrations,
          and the corresponding Qdrant + Neo4j entries. Users keep
          their accounts and can rejoin other teams.
        </p>
        <div class="danger-card__row">
          <select id="wipe-team-select" class="danger-select">
            <option value="">Loading teams…</option>
          </select>
          <button class="btn btn-danger" id="btn-wipe-team" disabled>
            Wipe team
          </button>
        </div>
        <div id="wipe-team-status" class="danger-status"></div>
      </div>

      <div class="danger-card danger-card--critical">
        <div class="danger-card__head">
          <span class="danger-icon" aria-hidden="true">!!</span>
          <h3>Wipe entire database</h3>
        </div>
        <p>
          Full nuke: every team, every user, every chat, every memory
          item, every integration. Also clears Qdrant, Neo4j, LibreChat
          MongoDB and the <code>xbrain-decks</code> MinIO bucket.
          Schema is preserved and <code>meeting-recap</code> is re-seeded.
        </p>
        <button class="btn btn-danger btn-wipe-full" id="btn-wipe-db">
          WIPE EVERYTHING
        </button>
        <div id="wipe-db-status" class="danger-status"></div>
      </div>
    </div>
  `;

  populateTeamSelect();

  document.getElementById("btn-wipe-team").addEventListener("click", () => {
    const slug = document.getElementById("wipe-team-select").value;
    if (!slug) return;
    openModal({
      title: `Wipe team ${slug}`,
      body:
        `This permanently deletes team "${slug}" and ALL its scoped data: ` +
        `memberships, conversations, memory items, tasks, contacts, integrations. ` +
        `Qdrant + Neo4j entries for this team will also be removed. ` +
        `Users keep their accounts and can rejoin other teams. ` +
        `This action is logged in the audit log.`,
      required: slug,
      onConfirm: () => doWipeTeam(slug),
    });
  });

  document.getElementById("btn-wipe-db").addEventListener("click", () => {
    openModal({
      title: "Wipe entire database",
      body:
        "This permanently deletes EVERY team, user, membership, conversation, " +
        "memory item, integration. It also clears Qdrant, Neo4j, LibreChat MongoDB, " +
        "and the xbrain-decks MinIO bucket. The schema is preserved and the " +
        "meeting-recap agent is re-seeded. Cannot be undone without a GCS restore.",
      required: "WIPE EVERYTHING",
      onConfirm: doWipeDatabase,
    });
  });
}

async function populateTeamSelect() {
  const sel = document.getElementById("wipe-team-select");
  const btn = document.getElementById("btn-wipe-team");
  if (!sel || !btn) return;
  try {
    // Try the superadmin brain overview first (lists ALL teams). Fall back
    // to /v1/teams/my-teams which only returns the caller's memberships.
    let teams = [];
    try {
      const overview = await apiFetch("/v1/admin/brain/overview");
      const rows = (overview && overview.rows) || overview || [];
      teams = rows.map((r) => ({
        slug: r.team_slug || r.slug,
        display_name: r.display_name || r.team_slug || r.slug,
      })).filter((t) => t.slug);
    } catch {
      const mine = await apiFetch("/v1/teams/my-teams");
      teams = mine || [];
    }

    sel.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = teams.length
      ? "Select a team to wipe…"
      : "(no teams found)";
    sel.appendChild(placeholder);
    for (const t of teams) {
      const opt = document.createElement("option");
      opt.value = t.slug;
      opt.textContent = `${t.display_name || t.slug} (${t.slug})`;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => {
      btn.disabled = !sel.value;
    });
  } catch (e) {
    sel.innerHTML = `<option value="">(failed to load: ${escapeHtml(e.message || "error")})</option>`;
  }
}

async function doWipeTeam(slug) {
  const status = document.getElementById("wipe-team-status");
  if (status) {
    status.className = "danger-status";
    status.textContent = `Wiping team ${slug}…`;
  }
  try {
    const res = await apiFetch(
      `/v1/admin/wipe-team/${encodeURIComponent(slug)}`,
      { method: "POST", body: { confirm: slug } },
    );
    if (status) {
      status.className = "danger-status is-success";
      status.innerHTML =
        `Team <code>${escapeHtml(slug)}</code> wiped successfully.\n` +
        `<pre>${escapeHtml(JSON.stringify(res, null, 2))}</pre>`;
    }
    closeModal();
    await populateTeamSelect();
  } catch (e) {
    showModalError(e.message || String(e));
  }
}

async function doWipeDatabase() {
  const status = document.getElementById("wipe-db-status");
  if (status) {
    status.className = "danger-status";
    status.textContent = "Wiping entire database…";
  }
  try {
    const res = await apiFetch(`/v1/admin/wipe-database`, {
      method: "POST",
      body: { confirm: "WIPE EVERYTHING" },
    });
    if (status) {
      status.className = "danger-status is-success";
      status.innerHTML =
        `Database wiped. You are about to be signed out — your xbt_ token is no longer valid.\n` +
        `<pre>${escapeHtml(JSON.stringify(res, null, 2))}</pre>`;
    }
    closeModal();
    setTimeout(() => {
      try {
        localStorage.removeItem(STORAGE_TOKEN);
        localStorage.removeItem(LEGACY_STORAGE_TOKEN);
      } catch { /* ignore */ }
      window.location.href = "/";
    }, 3500);
  } catch (e) {
    showModalError(e.message || String(e));
  }
}

// Auto-hydrate when imported into the admin dashboard.
window.addEventListener("DOMContentLoaded", () => {
  const target = document.getElementById("danger-zone");
  if (target) renderDangerZone(target);
});

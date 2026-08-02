/**
 * Options page wiring — loads settings on open, persists on change.
 *
 * Pure UI glue. The schema + merge logic lives in ./settings.js so it can
 * be tested in node without chrome.* polyfills.
 */

import { loadSettings, saveSettings } from "./settings.js";
import {
  THEME_STORAGE_KEY,
  resolveInitialTheme,
  applyTheme,
} from "./chat_core/theme.js";

const STATUS_FADE_MS = 1500;

function showStatus(text) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = text;
  setTimeout(() => {
    // Only clear if the message we set is still there — prevents racing
    // multiple in-flight saves clobbering each other's "Saved ✓" timer.
    if (el.textContent === text) el.textContent = "";
  }, STATUS_FADE_MS);
}

/**
 * Theme control — a segmented light/dark switch pinned to the top-right of the
 * page, not a row in the settings list. It is a display mode you flip and see,
 * not a preference to read past, and it is the same .seg control the chat
 * surfaces carry so the product reads as one thing.
 *
 * Stored under the SAME key the popup reads (THEME_STORAGE_KEY) so a change here
 * is what the chat picks up on its next open; unset still means "follow the
 * system".
 */
async function wireThemeToggle() {
  const lightBtn = document.getElementById("btn-theme-light");
  const darkBtn = document.getElementById("btn-theme-dark");
  if (!lightBtn || !darkBtn) return;

  const paint = (mode) => {
    applyTheme(document.documentElement, mode);
    lightBtn.setAttribute("aria-pressed", String(mode === "light"));
    darkBtn.setAttribute("aria-pressed", String(mode === "dark"));
  };

  let stored = null;
  try {
    const got = await chrome.storage.local.get([THEME_STORAGE_KEY]);
    stored = got[THEME_STORAGE_KEY] || null;
  } catch {
    /* unreadable storage — fall back to the system preference below */
  }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  paint(resolveInitialTheme({ storedTheme: stored, prefersDark }));

  const choose = async (mode) => {
    paint(mode);
    try {
      await chrome.storage.local.set({ [THEME_STORAGE_KEY]: mode });
      showStatus(`Saved ✓  —  ${mode} theme`);
    } catch {
      showStatus("Could not save the theme");
    }
  };
  lightBtn.addEventListener("click", () => choose("light"));
  darkBtn.addEventListener("click", () => choose("dark"));
}

async function init() {
  const settings = await loadSettings(chrome.storage.sync);
  await wireThemeToggle();

  // === Existing toggles ===
  const cbSidePanel = document.getElementById("opt-side-panel");
  const cbAutofill = document.getElementById("opt-autofill-librechat");

  cbSidePanel.checked = settings.openInSidePanel;
  cbAutofill.checked = settings.autoFillLibreChat;

  cbSidePanel.addEventListener("change", async () => {
    await saveSettings(chrome.storage.sync, {
      openInSidePanel: cbSidePanel.checked,
    });
    showStatus(
      cbSidePanel.checked
        ? "Saved ✓  —  side panel will be used on next click of the extension icon"
        : "Saved ✓  —  popup mode restored on next click",
    );
  });

  cbAutofill.addEventListener("change", async () => {
    await saveSettings(chrome.storage.sync, {
      autoFillLibreChat: cbAutofill.checked,
    });
    showStatus(
      cbAutofill.checked
        ? "Saved ✓  —  LibreChat auto-fill enabled (reload LibreChat to apply)"
        : "Saved ✓  —  LibreChat auto-fill disabled",
    );
  });

  // The master notification switch. Same setting the chat header's bell edits,
  // so the two surfaces can never show different answers — there is one value.
  const cbShowNotifications = document.getElementById("opt-show-notifications");
  if (cbShowNotifications) {
    cbShowNotifications.checked = settings.showNotifications;
    cbShowNotifications.addEventListener("change", async () => {
      await saveSettings(chrome.storage.sync, {
        showNotifications: cbShowNotifications.checked,
      });
      showStatus(
        cbShowNotifications.checked
          ? "Saved ✓  —  desktop notifications on"
          : "Saved ✓  —  desktop notifications off (nothing will pop up)",
      );
    });
  }

  // Phase 22 push-a-link (D-22-04) — recipient opt-out. Guarded with if(cb)
  // so older markup without the checkbox doesn't throw.
  const cbAllowOpenLink = document.getElementById("opt-allow-open-link");
  if (cbAllowOpenLink) {
    cbAllowOpenLink.checked = settings.allowOpenLinkRequests;
    cbAllowOpenLink.addEventListener("change", async () => {
      await saveSettings(chrome.storage.sync, {
        allowOpenLinkRequests: cbAllowOpenLink.checked,
      });
      showStatus(
        cbAllowOpenLink.checked
          ? "Saved ✓  —  teammates can send you open-link requests (you click to open)"
          : "Saved ✓  —  open-link requests from teammates will be ignored",
      );
    });
  }

  // Auto-open (opt-in). Kept as a SEPARATE toggle rather than a third state of the
  // one above, because the two answer different questions: "may they send?" and
  // "may it skip my click?".
  const cbAutoOpenLink = document.getElementById("opt-auto-open-link");
  if (cbAutoOpenLink) {
    cbAutoOpenLink.checked = settings.autoOpenLinkRequests;
    cbAutoOpenLink.addEventListener("change", async () => {
      await saveSettings(chrome.storage.sync, {
        autoOpenLinkRequests: cbAutoOpenLink.checked,
      });
      showStatus(
        cbAutoOpenLink.checked
          ? "Saved ✓  —  links from teammates will open WITHOUT asking"
          : "Saved ✓  —  you will be asked before a link opens",
      );
    });
  }

  // === Clip defaults (Wave 3.5) ===
  const inProject = document.getElementById("opt-clip-project");
  const radioTruth = document.getElementsByName("opt-clip-truth");
  const cbSkip = document.getElementById("opt-clip-skip-overlay");

  if (inProject) {
    inProject.value = settings.clipDefaultProject || "";
    let _projTimer = null;
    inProject.addEventListener("input", () => {
      // Debounce 300ms so we don't write on every keystroke.
      clearTimeout(_projTimer);
      _projTimer = setTimeout(async () => {
        const v = inProject.value.trim();
        await saveSettings(chrome.storage.sync, {
          clipDefaultProject: v || null,
        });
        showStatus(
          v
            ? `Saved ✓  —  default project: ${v}`
            : "Saved ✓  —  no default project (overlay will ask each time)",
        );
      }, 300);
    });
  }

  if (radioTruth && radioTruth.length) {
    for (const r of radioTruth) {
      r.checked = r.value === settings.clipDefaultTruthLevel;
      r.addEventListener("change", async () => {
        if (!r.checked) return;
        await saveSettings(chrome.storage.sync, {
          clipDefaultTruthLevel: r.value,
        });
        showStatus(`Saved ✓  —  default truth level: ${r.value}`);
      });
    }
  }

  if (cbSkip) {
    cbSkip.checked = settings.clipSkipOverlay;
    cbSkip.addEventListener("change", async () => {
      await saveSettings(chrome.storage.sync, {
        clipSkipOverlay: cbSkip.checked,
      });
      showStatus(
        cbSkip.checked
          ? "Saved ✓  —  clip overlay will auto-send after 1.5s when defaults are set"
          : "Saved ✓  —  clip overlay always opens for confirmation",
      );
    });
  }

  // === Claude Pro/Max session ===
  await renderClaudeSession();
  const btnRefreshClaude = document.getElementById("btn-refresh-claude-session");
  if (btnRefreshClaude) {
    btnRefreshClaude.addEventListener("click", refreshClaudeSession);
  }

  // === Team management (Stage 2) ===
  await renderTeamsList();
}

// ─── Team management ────────────────────────────────────────────────────────

const MEMORY_API_BASE = "https://api.grooveos.app";

const teamState = {
  me: null,           // /v1/me result {id, source_user_id, email, ...}
  teams: [],          // [{id, slug, display_name, ...}]
  membersByTeam: {},  // teamId → list of MemberOut
  expanded: new Set(), // set of expanded team_ids
};

async function _xbtFetch(path, { method = "GET", body = null } = {}) {
  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (!xbt_token) throw new Error("not signed in");
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${xbt_token}`,
      "Content-Type": "application/json",
    },
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const r = await fetch(`${MEMORY_API_BASE}${path}`, opts);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    const err = new Error(`HTTP ${r.status}: ${text.slice(0, 300)}`);
    err.status = r.status;
    err.body = text;
    throw err;
  }
  if (r.status === 204) return null;
  return r.json();
}

async function renderTeamsList() {
  const list = document.getElementById("teams-list");
  const empty = document.getElementById("teams-empty");
  const loading = document.getElementById("teams-loading");
  if (!list || !empty || !loading) return;

  list.innerHTML = "";
  empty.hidden = true;
  loading.hidden = false;

  try {
    teamState.me = await _xbtFetch("/v1/me");
    teamState.teams = await _xbtFetch("/v1/teams/my-teams");
  } catch (e) {
    loading.hidden = true;
    list.innerHTML = "";
    const card = document.createElement("div");
    card.className = "settings-card";
    card.innerHTML = `
      <div class="setting">
        <div class="setting-body" style="flex:1">
          <p class="setting-help" style="margin:0;color:var(--destructive)">
            Couldn't load teams: ${escapeHtml(e.message || "unknown error")}.
            Sign in via the extension popup and try again.
          </p>
        </div>
      </div>`;
    list.appendChild(card);
    return;
  }

  loading.hidden = true;
  if (!teamState.teams || teamState.teams.length === 0) {
    empty.hidden = false;
    return;
  }

  for (const t of teamState.teams) {
    list.appendChild(buildTeamCard(t));
  }
}

function buildTeamCard(team) {
  const card = document.createElement("div");
  card.className = "team-card";
  card.dataset.teamId = team.id;

  // Header
  const header = document.createElement("div");
  header.className = "team-card-header";
  header.innerHTML = `
    <div class="team-card-title">
      <strong>${escapeHtml(team.display_name || team.slug)}</strong>
      <small>${escapeHtml(team.slug)}${team.github_org ? " · github:" + escapeHtml(team.github_org) : ""}</small>
    </div>
    <span class="team-role-pill" data-role-pill>…</span>
    <span class="team-card-chevron" aria-hidden="true">▾</span>
  `;
  card.appendChild(header);

  const body = document.createElement("div");
  body.className = "team-card-body";
  body.hidden = true;
  card.appendChild(body);

  header.addEventListener("click", () => toggleTeamCard(team, header, body));

  // If already expanded (e.g. after invite re-renders), keep it open.
  if (teamState.expanded.has(team.id)) {
    header.classList.add("is-open");
    body.hidden = false;
    void fillTeamBody(team, body);
  }
  return card;
}

async function toggleTeamCard(team, header, body) {
  const opening = body.hidden;
  body.hidden = !opening;
  header.classList.toggle("is-open", opening);
  if (opening) {
    teamState.expanded.add(team.id);
    await fillTeamBody(team, body);
  } else {
    teamState.expanded.delete(team.id);
  }
}

async function fillTeamBody(team, body) {
  body.innerHTML = `<p class="setting-help" style="margin:0">Loading members…</p>`;
  let members;
  try {
    members = await _xbtFetch(`/v1/teams/${team.id}/members`);
  } catch (e) {
    body.innerHTML = `<p class="setting-help" style="margin:0;color:var(--destructive)">
      Couldn't load members: ${escapeHtml(e.message)}
    </p>`;
    return;
  }
  teamState.membersByTeam[team.id] = members;

  const meMember = members.find((m) => m.user_id === teamState.me.id);
  const myRole = meMember ? meMember.role : "member";
  const isAdmin = myRole === "admin";

  // Update header pill
  const card = body.parentElement;
  const pill = card.querySelector("[data-role-pill]");
  if (pill) {
    pill.textContent = myRole;
    pill.className = `team-role-pill ${isAdmin ? "is-admin" : "is-member"}`;
  }
  const sub = card.querySelector(".team-card-title small");
  if (sub) {
    sub.textContent =
      `${team.slug}${team.github_org ? " · github:" + team.github_org : ""}` +
      ` · ${members.length} member${members.length === 1 ? "" : "s"}`;
  }

  // Build body
  body.innerHTML = "";

  // Members list
  const membersLabel = document.createElement("p");
  membersLabel.className = "team-members-label";
  membersLabel.textContent = "Members";
  body.appendChild(membersLabel);

  const adminCount = members.filter((m) => m.role === "admin").length;

  for (const m of members) {
    const row = document.createElement("div");
    row.className = "team-member-row";

    const av = document.createElement("div");
    av.className = "team-member-avatar";
    const initial = ((m.display_name || m.email || "?").charAt(0) || "?").toUpperCase();
    av.textContent = initial;
    row.appendChild(av);

    const info = document.createElement("div");
    info.className = "team-member-info";
    const label = m.display_name || (m.email ? m.email.split("@")[0] : "Member");
    const isMe = m.user_id === teamState.me.id;
    info.innerHTML = `
      <strong>${escapeHtml(label)}${isMe ? " (you)" : ""}</strong>
      <small>${escapeHtml(m.email || m.source_user_id || m.user_id)}</small>
    `;
    row.appendChild(info);

    const roleSpan = document.createElement("span");
    roleSpan.className = "team-member-role";
    roleSpan.textContent = m.role;
    row.appendChild(roleSpan);

    if (isAdmin && !isMe) {
      // Phase 10 GHA-03 — Block/Unblock toggle. Always visible to admins;
      // a "blocked" pill renders next to the role pill when blocked_at is set.
      const blockBtn = document.createElement("button");
      blockBtn.className = "team-member-block";
      blockBtn.type = "button";
      if (m.blocked_at) {
        blockBtn.textContent = "Unblock";
        blockBtn.classList.add("is-blocked");
        blockBtn.title = "Lift the block — member regains team access";
      } else {
        blockBtn.textContent = "Block";
        blockBtn.title = "Block this member — keeps the row, denies all scoped API access";
      }
      blockBtn.addEventListener("click", () => toggleMemberBlock(team, m, blockBtn));
      row.appendChild(blockBtn);

      const rmBtn = document.createElement("button");
      rmBtn.className = "team-member-remove";
      rmBtn.type = "button";
      rmBtn.textContent = "Remove";
      // Block removing last admin client-side too (server enforces, this just keeps UX clear)
      if (m.role === "admin" && adminCount <= 1) {
        rmBtn.disabled = true;
        rmBtn.title = "Can't remove the last admin";
      }
      rmBtn.addEventListener("click", () => removeMember(team, m));
      row.appendChild(rmBtn);
    }

    // Show a "blocked" pill on every viewer's row (admins + members) so it's
    // visible that a teammate is blocked, even to non-admins.
    if (m.blocked_at) {
      const blockedPill = document.createElement("span");
      blockedPill.className = "team-role-pill is-blocked";
      blockedPill.textContent = "blocked";
      blockedPill.title = `Blocked at ${m.blocked_at}`;
      row.appendChild(blockedPill);
    }
    body.appendChild(row);
  }

  // Admin actions: invite form
  if (isAdmin) {
    const inviteLabel = document.createElement("p");
    inviteLabel.className = "team-members-label";
    inviteLabel.style.marginTop = "8px";
    inviteLabel.textContent = "Invite by email";
    body.appendChild(inviteLabel);

    const form = document.createElement("div");
    form.className = "team-invite-form";
    form.innerHTML = `
      <input type="email" placeholder="teammate@example.com" autocomplete="off" />
      <select>
        <option value="member" selected>member</option>
        <option value="admin">admin</option>
      </select>
      <button type="button" class="team-invite-btn">Invite</button>
    `;
    body.appendChild(form);

    const input = form.querySelector("input");
    const sel = form.querySelector("select");
    const btn = form.querySelector("button");

    btn.addEventListener("click", () => inviteMember(team, input, sel, btn));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") inviteMember(team, input, sel, btn);
    });

    // Phase 10 GHA-05 — Pre-block by GitHub login. Admin-only. Lets an admin
    // ban a github_login BEFORE that user has signed into xbrain — when they
    // later complete the GitHub OAuth flow, auto-grant pipeline consults
    // team_org_blocks and skips this team.
    const blockSection = document.createElement("div");
    blockSection.className = "org-block-form";
    blockSection.innerHTML = `
      <label style="font-size:11px;color:var(--muted-fg);text-transform:uppercase;letter-spacing:0.04em;">
        Pre-block by GitHub login
      </label>
      <div class="invite-form-row">
        <input type="text" placeholder="github-login (e.g. octocat)" autocomplete="off" />
        <button type="button" class="org-block-btn">Pre-block</button>
      </div>
      <div class="org-blocks-list"></div>
    `;
    body.appendChild(blockSection);

    const blockInput = blockSection.querySelector("input");
    const blockBtn = blockSection.querySelector("button");
    const blocksList = blockSection.querySelector(".org-blocks-list");

    const trySubmitBlock = () =>
      submitOrgBlock(team, blockInput, blockBtn, blocksList);
    blockBtn.addEventListener("click", trySubmitBlock);
    blockInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") trySubmitBlock();
    });

    // Initial load of existing pre-blocks. Fire-and-forget; errors render
    // inline inside loadOrgBlocks itself.
    void loadOrgBlocks(team, blocksList);

    // Admin actions: agent name (per-team mention alias). PATCHes
    // /v1/teams/{id}/agent-aliases and re-fetches, so a changed name takes
    // effect with no restart (D-21-05). @agent always works; this ADDS a
    // custom name — the server re-validates and keeps the defaults.
    const aliasLabel = document.createElement("p");
    aliasLabel.className = "team-members-label";
    aliasLabel.style.marginTop = "8px";
    aliasLabel.textContent = "Agent name";
    body.appendChild(aliasLabel);

    const aliasForm = document.createElement("div");
    aliasForm.className = "team-invite-form";
    aliasForm.innerHTML = `
      <input type="text" placeholder="wizard" autocomplete="off" maxlength="32" />
      <button type="button" class="team-invite-btn">Save</button>
    `;
    body.appendChild(aliasForm);

    const aliasHelp = document.createElement("p");
    aliasHelp.className = "setting-help";
    aliasHelp.style.cssText =
      "margin:4px 0 0;font-size:11px;color:var(--muted-fg)";
    aliasHelp.textContent =
      "Set a custom name to summon the agent (e.g. @wizard). @agent always works.";
    body.appendChild(aliasHelp);

    const aliasInput = aliasForm.querySelector("input");
    const aliasBtn = aliasForm.querySelector("button");

    // Prefill with the team's current effective alias list.
    void loadAgentAliases(team, aliasInput);

    const trySaveAlias = () => saveAgentAliases(team, aliasInput, aliasBtn);
    aliasBtn.addEventListener("click", trySaveAlias);
    aliasInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") trySaveAlias();
    });
  }

  // Per-team status line + leave button
  const statusEl = document.createElement("div");
  statusEl.className = "team-action-status";
  statusEl.dataset.teamStatus = team.id;
  body.appendChild(statusEl);

  const leaveBtn = document.createElement("button");
  leaveBtn.className = "team-leave-btn";
  leaveBtn.type = "button";
  leaveBtn.textContent = "Leave team";
  if (isAdmin && adminCount <= 1) {
    leaveBtn.disabled = true;
    leaveBtn.title = "You're the only admin — promote another member first";
  }
  leaveBtn.addEventListener("click", () => leaveTeam(team, leaveBtn, statusEl));
  body.appendChild(leaveBtn);
}

async function inviteMember(team, input, sel, btn) {
  const email = (input.value || "").trim();
  if (!email) {
    setTeamStatus(team.id, "Enter an email first", "error");
    return;
  }
  btn.disabled = true;
  setTeamStatus(team.id, "Inviting…", "loading");
  try {
    await _xbtFetch(`/v1/teams/${team.id}/invite`, {
      method: "POST",
      body: { email, role: sel.value },
    });
    setTeamStatus(team.id, `Invited ${email} ✓`, "success");
    input.value = "";
    // Re-fetch members so the new row appears.
    const card = document.querySelector(`[data-team-id="${team.id}"]`);
    const body = card && card.querySelector(".team-card-body");
    if (body) await fillTeamBody(team, body);
  } catch (e) {
    const msg =
      e.status === 404
        ? `${email} isn't registered yet — ask them to sign in once first.`
        : `Invite failed: ${e.message}`;
    setTeamStatus(team.id, msg, "error");
  } finally {
    btn.disabled = false;
  }
}

// GET the team's EFFECTIVE agent-alias list and prefill the input with it
// (comma-joined) so the admin sees exactly what currently summons the agent.
// Non-fatal on error — the placeholder still guides input.
async function loadAgentAliases(team, input) {
  try {
    const data = await _xbtFetch(`/v1/teams/${team.id}/agent-aliases`);
    const aliases = (data && data.aliases) || [];
    input.value = aliases.join(", ");
  } catch {
    /* leave empty — the placeholder guides the admin */
  }
}

// PATCH the team's custom agent alias(es), then re-render the field with the
// server's resulting EFFECTIVE list so the change is reflected immediately
// (no restart, D-21-05). Surfaces 403 (non-admin) and 422 (validation) cleanly.
async function saveAgentAliases(team, input, btn) {
  const raw = (input.value || "").trim();
  // Split on commas so an admin can paste the effective list back; the server
  // re-validates + dedupes, strips leading "@", and always re-adds the
  // defaults (@agent included), so sending the whole list is safe.
  const aliases = raw
    .split(",")
    .map((a) => a.trim().replace(/^@/, ""))
    .filter((a) => a.length > 0);
  btn.disabled = true;
  setTeamStatus(team.id, "Saving agent name…", "loading");
  try {
    const data = await _xbtFetch(`/v1/teams/${team.id}/agent-aliases`, {
      method: "PATCH",
      body: { aliases },
    });
    const effective = (data && data.aliases) || [];
    input.value = effective.join(", ");
    setTeamStatus(team.id, "Agent name saved ✓", "success");
  } catch (e) {
    const msg =
      e.status === 403
        ? "Only team admins can change the agent name."
        : e.status === 422
          ? `Invalid agent name: ${(e.body || "").slice(0, 140)}`
          : `Save failed: ${e.message}`;
    setTeamStatus(team.id, msg, "error");
  } finally {
    btn.disabled = false;
  }
}

async function removeMember(team, member) {
  const label = member.display_name || member.email || "this member";
  if (!confirm(`Remove ${label} from ${team.display_name}?`)) return;
  setTeamStatus(team.id, `Removing ${label}…`, "loading");
  try {
    await _xbtFetch(`/v1/teams/${team.id}/members/${member.user_id}`, {
      method: "DELETE",
    });
    setTeamStatus(team.id, `${label} removed ✓`, "success");
    const card = document.querySelector(`[data-team-id="${team.id}"]`);
    const body = card && card.querySelector(".team-card-body");
    if (body) await fillTeamBody(team, body);
  } catch (e) {
    setTeamStatus(team.id, `Remove failed: ${e.message}`, "error");
  }
}

// ─── Phase 10 GHA-03 — Block / Unblock member ──────────────────────────────

async function toggleMemberBlock(team, member, btn) {
  const action = member.blocked_at ? "unblock" : "block";
  const label = member.display_name || member.email || "this member";
  if (!confirm(
    action === "block"
      ? `Block ${label}? They'll keep their row but be denied access to ${team.display_name}.`
      : `Unblock ${label}? They'll regain access to ${team.display_name}.`,
  )) return;
  btn.disabled = true;
  setTeamStatus(team.id, `${action === "block" ? "Blocking" : "Unblocking"} ${label}…`, "loading");
  try {
    await _xbtFetch(
      `/v1/teams/${team.id}/members/${member.user_id}/${action}`,
      { method: "POST", body: {} },
    );
    setTeamStatus(
      team.id,
      action === "block" ? `${label} blocked ✓` : `${label} unblocked ✓`,
      "success",
    );
    // Re-fetch members so blocked_at / button state picks up the change.
    const card = document.querySelector(`[data-team-id="${team.id}"]`);
    const body = card && card.querySelector(".team-card-body");
    if (body) await fillTeamBody(team, body);
  } catch (e) {
    setTeamStatus(team.id, `${action} failed: ${e.message}`, "error");
    btn.disabled = false;
  }
}

// ─── Phase 10 GHA-05 — Pre-block by GitHub login ───────────────────────────

async function loadOrgBlocks(team, listEl) {
  listEl.innerHTML = `<p class="setting-help" style="margin:0;font-size:11px;">Loading pre-blocks…</p>`;
  let blocks;
  try {
    blocks = await _xbtFetch(`/v1/teams/${team.id}/org-blocks`);
  } catch (e) {
    listEl.innerHTML = `<p class="setting-help" style="margin:0;color:var(--destructive);font-size:11px;">
      Couldn't load pre-blocks: ${escapeHtml(e.message)}
    </p>`;
    return;
  }
  listEl.innerHTML = "";
  if (!blocks || blocks.length === 0) {
    const hint = document.createElement("p");
    hint.className = "setting-help";
    hint.style.cssText = "margin:0;font-size:11px;color:var(--muted-fg);";
    hint.textContent = "No pre-blocks yet.";
    listEl.appendChild(hint);
    return;
  }
  for (const b of blocks) {
    const item = document.createElement("div");
    item.className = "org-block-item";

    const left = document.createElement("span");
    left.innerHTML = `
      <span class="org-block-login">@${escapeHtml(b.github_login)}</span>
      <span class="org-block-meta"> · ${escapeHtml((b.blocked_at || "").slice(0, 10))}${b.blocked_by_email ? " by " + escapeHtml(b.blocked_by_email) : ""}</span>
    `;
    item.appendChild(left);

    const rm = document.createElement("button");
    rm.className = "org-block-remove";
    rm.type = "button";
    rm.textContent = "Remove";
    rm.title = `Lift the pre-block on @${b.github_login}`;
    rm.addEventListener("click", () => removeOrgBlock(team, b.github_login, listEl));
    item.appendChild(rm);

    listEl.appendChild(item);
  }
}

async function submitOrgBlock(team, input, btn, listEl) {
  const login = (input.value || "").trim().replace(/^@+/, "");
  if (!login) {
    setTeamStatus(team.id, "Enter a GitHub login first", "error");
    return;
  }
  // GitHub logins: ASCII letters / digits / hyphens, 1-39 chars, no leading
  // hyphen. Reject obvious garbage client-side; server validates too.
  if (!/^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,38})$/.test(login)) {
    setTeamStatus(team.id, "Invalid GitHub login format", "error");
    return;
  }
  btn.disabled = true;
  setTeamStatus(team.id, `Pre-blocking @${login}…`, "loading");
  try {
    await _xbtFetch(`/v1/teams/${team.id}/org-blocks`, {
      method: "POST",
      body: { github_login: login },
    });
    setTeamStatus(team.id, `Pre-blocked @${login} ✓`, "success");
    input.value = "";
    await loadOrgBlocks(team, listEl);
  } catch (e) {
    setTeamStatus(team.id, `Pre-block failed: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function removeOrgBlock(team, githubLogin, listEl) {
  if (!confirm(`Lift the pre-block on @${githubLogin}? They'll be auto-granted to ${team.display_name} on next GitHub sign-in if they match an org.`)) {
    return;
  }
  setTeamStatus(team.id, `Removing pre-block on @${githubLogin}…`, "loading");
  try {
    await _xbtFetch(
      `/v1/teams/${team.id}/org-blocks/${encodeURIComponent(githubLogin)}`,
      { method: "DELETE" },
    );
    setTeamStatus(team.id, `Pre-block on @${githubLogin} removed ✓`, "success");
    await loadOrgBlocks(team, listEl);
  } catch (e) {
    setTeamStatus(team.id, `Remove failed: ${e.message}`, "error");
  }
}

async function leaveTeam(team, btn, statusEl) {
  if (!confirm(`Leave ${team.display_name}? You'll lose access to its chat and memory.`)) return;
  btn.disabled = true;
  setTeamStatus(team.id, "Leaving…", "loading");
  try {
    await _xbtFetch(`/v1/teams/${team.id}/members/me`, { method: "DELETE" });
    setTeamStatus(team.id, "Left ✓", "success");
    // Tell the SW to rebuild the right-click menu so the team disappears.
    chrome.runtime.sendMessage({ type: "REFRESH_TEAMS_MENU" }).catch(() => {});
    // Re-render the whole list (the team is gone now).
    setTimeout(() => renderTeamsList(), 600);
  } catch (e) {
    const msg =
      e.status === 409
        ? "You're the only admin — promote another member to admin before leaving."
        : `Leave failed: ${e.message}`;
    setTeamStatus(team.id, msg, "error");
    btn.disabled = false;
  }
}

function setTeamStatus(teamId, text, type = "info") {
  const el = document.querySelector(`[data-team-status="${teamId}"]`);
  if (!el) return;
  el.textContent = text;
  el.classList.remove("is-success", "is-error");
  if (type === "success") el.classList.add("is-success");
  if (type === "error") el.classList.add("is-error");
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const SUB_PILL_CLASSES = {
  Pro: "is-pro",
  Max: "is-max",
  "Max 5x": "is-max",
  "Max 20x": "is-max",
  Free: "is-free",
  Team: "is-pro",
  Enterprise: "is-pro",
  Unknown: "",
};

async function renderClaudeSession() {
  const dot = document.getElementById("claude-session-dot");
  const status = document.getElementById("claude-session-status");
  const email = document.getElementById("claude-session-email");
  const sub = document.getElementById("claude-session-sub");
  const org = document.getElementById("claude-session-org");
  if (!status || !email || !sub || !org) return;

  status.textContent = "Loading…";
  email.textContent = "—";
  sub.textContent = "—";
  org.textContent = "—";
  if (dot) {
    dot.classList.remove("is-online", "is-offline");
    dot.classList.add("is-unknown");
  }

  let info = null;
  try {
    info = await chrome.runtime.sendMessage({ type: "GET_CLAUDE_SESSION_INFO" });
  } catch (e) {
    status.textContent = `error: ${e.message}`;
    if (dot) {
      dot.classList.remove("is-unknown", "is-online");
      dot.classList.add("is-offline");
    }
    return;
  }
  if (!info) {
    status.textContent = "no response from service worker";
    return;
  }
  const diag = document.getElementById("claude-session-diag");
  const diagBody = document.getElementById("claude-session-diag-body");
  if (!info.signed_in) {
    status.textContent = "Not signed in on claude.ai in this Chrome browser";
    if (dot) {
      dot.classList.remove("is-unknown", "is-online");
      dot.classList.add("is-offline");
    }
    // Expose the diagnostic dump so the user can see what came back from
    // claude.ai and why we concluded "not signed in".
    if (diag && diagBody) {
      diag.hidden = false;
      diagBody.textContent = JSON.stringify(info.debug || {}, null, 2);
    }
    return;
  }
  status.textContent = "Connected";
  email.textContent = info.email || "—";
  sub.textContent = info.subscription || "Unknown";
  sub.className = "sub-pill " + (SUB_PILL_CLASSES[info.subscription] || "");
  org.textContent = info.organization_name || "—";
  if (dot) {
    dot.classList.remove("is-unknown", "is-offline");
    dot.classList.add("is-online");
  }
  // Hide diagnostics on success.
  if (diag) diag.hidden = true;
}

async function refreshClaudeSession() {
  const btn = document.getElementById("btn-refresh-claude-session");
  const actionStatus = document.getElementById("claude-session-action-status");
  if (btn) btn.disabled = true;
  if (actionStatus) actionStatus.textContent = "Reconnecting bridge…";
  try {
    const resp = await chrome.runtime.sendMessage({
      type: "REFRESH_CLAUDE_SESSION",
    });
    if (resp && resp.ok) {
      if (actionStatus) actionStatus.textContent = "Reconnected ✓ — fetching session…";
      // Give the WS a beat to finish the register frame, then re-introspect.
      setTimeout(async () => {
        await renderClaudeSession();
        if (actionStatus) actionStatus.textContent = "Refreshed ✓";
        setTimeout(() => {
          if (actionStatus) actionStatus.textContent = "";
        }, 1500);
        if (btn) btn.disabled = false;
      }, 800);
    } else {
      if (actionStatus) {
        actionStatus.textContent = `Refresh failed: ${(resp && resp.error) || "unknown"}`;
      }
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    if (actionStatus) actionStatus.textContent = `Refresh failed: ${e.message}`;
    if (btn) btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", init);

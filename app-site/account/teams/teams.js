/**
 * teams.js — app-site/account/teams page (quick task 260513-tmu Stage 3).
 *
 * Mirrors the extension Settings team management UI for users who don't
 * have the extension installed (e.g. mobile-PWA candidates later).
 *
 * Auth flow:
 *   1. Google Identity Services renders a "Sign in with Google" button.
 *   2. Success → ID token JWT (`credential`) returned to our callback.
 *   3. We POST it to memory-api /v1/me/api-token with {team_scope:"default"}
 *      → xbt_ personal API token.
 *   4. Cache xbt_ in localStorage. Use it for all subsequent calls.
 *   5. On Sign out: revoke the Google session via google.accounts.id.disableAutoSelect
 *      and clear the xbt_ from localStorage.
 *
 * IMPORTANT — OAuth setup: this site's origin (https://grooveos.app and
 * https://xbrain-495115.web.app) MUST be in the Google OAuth client's
 * "Authorized JavaScript origins" list. Same client_id as the extension
 * + LibreChat.
 */

(function () {
  "use strict";

  const MEMORY_API_BASE = "https://api.grooveos.app";
  const GOOGLE_CLIENT_ID = "50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n.apps.googleusercontent.com";
  const STORAGE_TOKEN = "xbrain_xbt_token";
  const STORAGE_EMAIL = "xbrain_user_email";

  const state = {
    me: null,             // /v1/me result
    teams: [],
    expanded: new Set(),
  };

  // ── Initialization ────────────────────────────────────────────────────────

  window.addEventListener("load", init);

  async function init() {
    const xbt = localStorage.getItem(STORAGE_TOKEN);
    if (xbt) {
      // Already signed in — go straight to the teams UI.
      try {
        await loadAuthenticatedUI();
        return;
      } catch {
        // Stored token rejected — fall through to sign-in.
        localStorage.removeItem(STORAGE_TOKEN);
      }
    }
    showSignIn();
  }

  function showSignIn() {
    document.getElementById("signin-section").hidden = false;
    document.getElementById("auth-section").hidden = true;
    document.getElementById("hdr-user").hidden = true;

    // GIS may not have loaded yet — wait for the global.
    function tryInit() {
      if (window.google && google.accounts && google.accounts.id) {
        google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: handleGoogleCredential,
          auto_select: false,
          ux_mode: "popup",
        });
        google.accounts.id.renderButton(
          document.getElementById("google-signin-btn"),
          { theme: "filled_black", size: "large", shape: "rectangular", text: "signin_with" },
        );
      } else {
        setTimeout(tryInit, 200);
      }
    }
    tryInit();
  }

  async function handleGoogleCredential(resp) {
    const idToken = resp && resp.credential;
    if (!idToken) {
      alert("Sign-in failed — no credential returned.");
      return;
    }
    try {
      const r = await fetch(`${MEMORY_API_BASE}/v1/me/api-token`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${idToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ team_scope: "default", name: "web-teams-page" }),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        throw new Error(`mint failed: HTTP ${r.status} ${text.slice(0, 200)}`);
      }
      const { token } = await r.json();
      localStorage.setItem(STORAGE_TOKEN, token);

      // Decode the ID token payload (no verification — display only).
      try {
        const payload = JSON.parse(atob(idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
        if (payload.email) localStorage.setItem(STORAGE_EMAIL, payload.email);
      } catch { /* ignore */ }

      await loadAuthenticatedUI();
    } catch (e) {
      alert(`Sign-in failed: ${e.message}`);
    }
  }

  async function loadAuthenticatedUI() {
    document.getElementById("signin-section").hidden = true;
    document.getElementById("auth-section").hidden = false;
    document.getElementById("hdr-user").hidden = false;

    const email = localStorage.getItem(STORAGE_EMAIL) || "signed in";
    document.getElementById("hdr-email").textContent = email;

    const btnSignout = document.getElementById("btn-signout");
    if (btnSignout && !btnSignout._wired) {
      btnSignout._wired = true;
      btnSignout.addEventListener("click", signOut);
    }

    await renderTeamsList();
  }

  function signOut() {
    if (!confirm("Sign out from the Teams page?")) return;
    localStorage.removeItem(STORAGE_TOKEN);
    localStorage.removeItem(STORAGE_EMAIL);
    try {
      if (window.google && google.accounts && google.accounts.id) {
        google.accounts.id.disableAutoSelect();
      }
    } catch { /* ignore */ }
    state.me = null;
    state.teams = [];
    state.expanded.clear();
    document.getElementById("teams-list").innerHTML = "";
    showSignIn();
  }

  // ── Memory-api fetch helper ────────────────────────────────────────────

  async function xbtFetch(path, { method = "GET", body = null } = {}) {
    const token = localStorage.getItem(STORAGE_TOKEN);
    if (!token) throw new Error("not signed in");
    const opts = {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    };
    if (body !== null) opts.body = JSON.stringify(body);
    const r = await fetch(`${MEMORY_API_BASE}${path}`, opts);
    if (r.status === 401) {
      localStorage.removeItem(STORAGE_TOKEN);
      throw new Error("session expired — please sign in again");
    }
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

  // ── Teams list ────────────────────────────────────────────────────────

  async function renderTeamsList() {
    const list = document.getElementById("teams-list");
    const empty = document.getElementById("teams-empty");
    const loading = document.getElementById("teams-loading");

    list.innerHTML = "";
    empty.hidden = true;
    loading.hidden = false;

    try {
      state.me = await xbtFetch("/v1/me");
      state.teams = await xbtFetch("/v1/teams/my-teams");
    } catch (e) {
      loading.hidden = true;
      if (e.message && e.message.includes("session expired")) {
        showSignIn();
        return;
      }
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `<p style="color:var(--red);font-size:13px;margin:0">
        Couldn't load teams: ${escapeHtml(e.message || "unknown error")}.
      </p>`;
      list.appendChild(card);
      return;
    }

    loading.hidden = true;
    if (!state.teams || state.teams.length === 0) {
      empty.hidden = false;
      return;
    }
    for (const t of state.teams) {
      list.appendChild(buildTeamCard(t));
    }
  }

  function buildTeamCard(team) {
    const card = document.createElement("div");
    card.className = "team-card";
    card.dataset.teamId = team.id;

    const header = document.createElement("div");
    header.className = "team-card__header";
    header.innerHTML = `
      <div class="team-card__title">
        <strong>${escapeHtml(team.display_name || team.slug)}</strong>
        <small>${escapeHtml(team.slug)}${team.github_org ? " · github:" + escapeHtml(team.github_org) : ""}</small>
      </div>
      <span class="role-pill" data-role-pill>…</span>
      <span class="chevron" aria-hidden="true">▾</span>
    `;
    card.appendChild(header);

    const body = document.createElement("div");
    body.className = "team-card__body";
    body.hidden = true;
    card.appendChild(body);

    header.addEventListener("click", () => toggleCard(team, header, body));

    if (state.expanded.has(team.id)) {
      header.classList.add("is-open");
      body.hidden = false;
      void fillTeamBody(team, body);
    }
    return card;
  }

  async function toggleCard(team, header, body) {
    const opening = body.hidden;
    body.hidden = !opening;
    header.classList.toggle("is-open", opening);
    if (opening) {
      state.expanded.add(team.id);
      await fillTeamBody(team, body);
    } else {
      state.expanded.delete(team.id);
    }
  }

  async function fillTeamBody(team, body) {
    body.innerHTML = `<div class="loading">Loading members…</div>`;
    let members;
    try {
      members = await xbtFetch(`/v1/teams/${team.id}/members`);
    } catch (e) {
      body.innerHTML = `<p style="color:var(--red);font-size:13px;margin:0">
        Couldn't load members: ${escapeHtml(e.message)}
      </p>`;
      return;
    }

    const meMember = members.find((m) => m.user_id === state.me.id);
    const myRole = meMember ? meMember.role : "member";
    const isAdmin = myRole === "admin";

    // Update header pill + subtitle
    const card = body.parentElement;
    const pill = card.querySelector("[data-role-pill]");
    if (pill) {
      pill.textContent = myRole;
      pill.className = `role-pill ${isAdmin ? "is-admin" : "is-member"}`;
    }
    const sub = card.querySelector(".team-card__title small");
    if (sub) {
      sub.textContent =
        `${team.slug}${team.github_org ? " · github:" + team.github_org : ""}` +
        ` · ${members.length} member${members.length === 1 ? "" : "s"}`;
    }

    body.innerHTML = "";

    // Members
    const lbl = document.createElement("p");
    lbl.className = "members-label";
    lbl.textContent = "Members";
    body.appendChild(lbl);

    const adminCount = members.filter((m) => m.role === "admin").length;

    for (const m of members) {
      const row = document.createElement("div");
      row.className = "member-row";

      const av = document.createElement("div");
      av.className = "member-avatar";
      av.textContent = ((m.display_name || m.email || "?").charAt(0) || "?").toUpperCase();
      row.appendChild(av);

      const info = document.createElement("div");
      info.className = "member-info";
      const isMe = m.user_id === state.me.id;
      const label = m.display_name || (m.email ? m.email.split("@")[0] : "Member");
      info.innerHTML = `
        <strong>${escapeHtml(label)}${isMe ? " (you)" : ""}</strong>
        <small>${escapeHtml(m.email || m.source_user_id || m.user_id)}</small>
      `;
      row.appendChild(info);

      const role = document.createElement("span");
      role.className = "member-role";
      role.textContent = m.role;
      row.appendChild(role);

      if (isAdmin && !isMe) {
        const rmBtn = document.createElement("button");
        rmBtn.type = "button";
        rmBtn.className = "btn btn-sm btn-danger";
        rmBtn.textContent = "Remove";
        if (m.role === "admin" && adminCount <= 1) {
          rmBtn.disabled = true;
          rmBtn.title = "Can't remove the last admin";
        }
        rmBtn.addEventListener("click", () => removeMember(team, m));
        row.appendChild(rmBtn);
      }
      body.appendChild(row);
    }

    // Invite form (admin)
    if (isAdmin) {
      const iLbl = document.createElement("p");
      iLbl.className = "members-label";
      iLbl.style.marginTop = "16px";
      iLbl.textContent = "Invite by email";
      body.appendChild(iLbl);

      const form = document.createElement("div");
      form.className = "invite-form";
      form.innerHTML = `
        <input type="email" placeholder="teammate@example.com" autocomplete="off" />
        <select>
          <option value="member" selected>member</option>
          <option value="admin">admin</option>
        </select>
        <button type="button" class="btn btn-primary">Invite</button>
      `;
      body.appendChild(form);
      const input = form.querySelector("input");
      const sel = form.querySelector("select");
      const btn = form.querySelector("button");
      btn.addEventListener("click", () => inviteMember(team, input, sel, btn));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") inviteMember(team, input, sel, btn);
      });
    }

    // Status line
    const statusEl = document.createElement("div");
    statusEl.className = "action-status";
    statusEl.dataset.teamStatus = team.id;
    body.appendChild(statusEl);

    // Leave button
    const leaveBtn = document.createElement("button");
    leaveBtn.type = "button";
    leaveBtn.className = "btn btn-danger";
    leaveBtn.style.marginTop = "12px";
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
      setStatus(team.id, "Enter an email first", "error");
      return;
    }
    btn.disabled = true;
    setStatus(team.id, "Inviting…");
    try {
      await xbtFetch(`/v1/teams/${team.id}/invite`, {
        method: "POST",
        body: { email, role: sel.value },
      });
      setStatus(team.id, `Invited ${email} ✓`, "success");
      input.value = "";
      const card = document.querySelector(`[data-team-id="${team.id}"]`);
      const body = card && card.querySelector(".team-card__body");
      if (body) await fillTeamBody(team, body);
    } catch (e) {
      const msg =
        e.status === 404
          ? `${email} isn't registered yet — ask them to sign in once first.`
          : `Invite failed: ${e.message}`;
      setStatus(team.id, msg, "error");
    } finally {
      btn.disabled = false;
    }
  }

  async function removeMember(team, m) {
    const label = m.display_name || m.email || "this member";
    if (!confirm(`Remove ${label} from ${team.display_name || team.slug}?`)) return;
    setStatus(team.id, `Removing ${label}…`);
    try {
      await xbtFetch(`/v1/teams/${team.id}/members/${m.user_id}`, { method: "DELETE" });
      setStatus(team.id, `${label} removed ✓`, "success");
      const card = document.querySelector(`[data-team-id="${team.id}"]`);
      const body = card && card.querySelector(".team-card__body");
      if (body) await fillTeamBody(team, body);
    } catch (e) {
      setStatus(team.id, `Remove failed: ${e.message}`, "error");
    }
  }

  async function leaveTeam(team, btn, statusEl) {
    if (!confirm(`Leave ${team.display_name || team.slug}? You'll lose access to its chat and memory.`)) return;
    btn.disabled = true;
    setStatus(team.id, "Leaving…");
    try {
      await xbtFetch(`/v1/teams/${team.id}/members/me`, { method: "DELETE" });
      setStatus(team.id, "Left ✓", "success");
      setTimeout(() => renderTeamsList(), 600);
    } catch (e) {
      const msg =
        e.status === 409
          ? "You're the only admin — promote another member first."
          : `Leave failed: ${e.message}`;
      setStatus(team.id, msg, "error");
      btn.disabled = false;
    }
  }

  function setStatus(teamId, text, type) {
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
})();

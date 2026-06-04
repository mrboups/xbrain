/**
 * xbrain team onboarding modal — injected into LibreChat's index.html.
 *
 * Runs at chat.grooveos.app context. Uses /api/xbrain/token (same-origin)
 * to get a bridge JWT, then calls api.grooveos.app/v1/teams/* endpoints.
 *
 * Shows a non-closable 4-step modal if the user has no team assigned.
 * Hides itself and stores completion in sessionStorage to avoid re-checking.
 */

(function () {
  'use strict';

  const MEMORY_API = 'https://api.grooveos.app';
  const STORAGE_KEY = 'xbrain_onboarding_done';

  // Skip if already completed this session
  if (sessionStorage.getItem(STORAGE_KEY)) return;

  // LibreChat stores its JWT only in axios.defaults (in-memory), never in cookies.
  // We capture it by intercepting XHR.setRequestHeader — axios uses XHR, not fetch.
  // Always update _libreToken so we pick up refreshed tokens.
  let _libreToken = null;
  let _libreTokenUrl = null;
  const _origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._xbrainReqUrl = String(url);
    return _origOpen.apply(this, arguments);
  };
  const _origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (name.toLowerCase() === 'authorization' && value.startsWith('Bearer ')) {
      _libreToken = value.slice(7);
      _libreTokenUrl = this._xbrainReqUrl || '?';
    }
    return _origSetHeader.apply(this, arguments);
  };

  async function getToken() {
    if (!_libreToken) return null;
    try {
      const r = await fetch('/api/xbrain/token', {
        headers: { Authorization: 'Bearer ' + _libreToken },
      });
      if (!r.ok) return null;
      const { token } = await r.json();
      return token;
    } catch (e) {
      return null;
    }
  }

  async function apiCall(method, path, body, token) {
    const opts = {
      method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(`${MEMORY_API}${path}`, opts);
    return r;
  }

  async function checkTeam(token) {
    const r = await apiCall('GET', '/v1/teams/my-team', null, token);
    if (r.status === 204) return null;
    if (r.ok) return await r.json();
    return null; // treat errors as "no team" to avoid blocking the UI
  }

  // ── Styles ──────────────────────────────────────────────────────────────

  const CSS = `
    #xbrain-onboarding-overlay {
      position: fixed; inset: 0; z-index: 9999;
      background: #212121;
      display: flex; align-items: center; justify-content: center;
      font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
      color: #ececec;
    }
    #xbrain-onboarding-modal {
      background: #2a2b2e;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 16px;
      padding: 48px 48px 40px;
      width: min(540px, calc(100vw - 32px));
      max-height: calc(100vh - 48px);
      overflow-y: auto;
    }
    #xbrain-onboarding-modal h2 {
      margin: 0 0 10px; font-size: 22px; font-weight: 600;
      color: #ececec; letter-spacing: -0.01em;
    }
    .xb-desc {
      margin: 0 0 24px; font-size: 14px;
      color: rgba(236,236,236,0.55); line-height: 1.6;
    }
    .xb-input {
      width: 100%; padding: 11px 14px; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.1);
      background: #353537; color: #ececec; font-size: 14px;
      box-sizing: border-box; margin-bottom: 12px;
      transition: border-color 0.15s;
    }
    .xb-input::placeholder { color: rgba(236,236,236,0.3); }
    .xb-input:focus { outline: none; border-color: #60a5fa; background: #3a3b3e; }
    .xb-select {
      width: 100%; padding: 11px 14px; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.1);
      background: #353537; color: #ececec; font-size: 14px;
      box-sizing: border-box; margin-bottom: 12px;
    }
    .xb-btn {
      padding: 10px 22px; border-radius: 8px; border: none;
      font-size: 14px; cursor: pointer; font-weight: 500;
      transition: opacity 0.15s, background 0.15s;
    }
    .xb-btn-primary { background: #3b82f6; color: #fff; }
    .xb-btn-primary:hover { background: #2563eb; }
    .xb-btn-secondary {
      background: rgba(255,255,255,0.06); color: rgba(236,236,236,0.7);
      border: 1px solid rgba(255,255,255,0.12); margin-left: 8px;
    }
    .xb-btn-secondary:hover { background: rgba(255,255,255,0.1); }
    .xb-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .xb-key-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
    .xb-key-row select, .xb-key-row input { flex: 1; }
    .xb-key-row button {
      background: none; border: none; color: rgba(248,113,113,0.8);
      cursor: pointer; font-size: 16px; padding: 4px 8px;
    }
    .xb-error { color: #f87171; font-size: 13px; margin-bottom: 12px; }
    .xb-team-btn {
      display: block; width: 100%; text-align: left; padding: 13px 16px;
      margin-bottom: 8px; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.1); background: #353537;
      color: #ececec; cursor: pointer; font-size: 14px;
      transition: border-color 0.15s, background 0.15s;
    }
    .xb-team-btn:hover { border-color: #60a5fa; background: #3a3b3e; }
    .xb-step-indicator {
      font-size: 12px; color: rgba(236,236,236,0.4);
      margin-bottom: 24px; font-weight: 500; letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .xb-spinner {
      display: inline-block; width: 14px; height: 14px;
      border: 2px solid rgba(255,255,255,0.15);
      border-top-color: #60a5fa; border-radius: 50%;
      animation: xb-spin 0.7s linear infinite; margin-right: 8px;
      vertical-align: middle;
    }
    @keyframes xb-spin { to { transform: rotate(360deg); } }
    .xb-logout {
      display: block; text-align: center; margin-top: 28px;
      font-size: 12px; color: rgba(236,236,236,0.3);
      cursor: pointer; background: none; border: none; width: 100%;
    }
    .xb-logout:hover { color: rgba(236,236,236,0.6); }
  `;

  // ── State ────────────────────────────────────────────────────────────────

  let token = null;
  let selectedTeam = null;

  // ── DOM helpers ──────────────────────────────────────────────────────────

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on')) e[k] = v;
      else e.setAttribute(k, v);
    });
    children.forEach(c => {
      if (typeof c === 'string') e.appendChild(document.createTextNode(c));
      else if (c) e.appendChild(c);
    });
    return e;
  }

  function setHtml(container, ...nodes) {
    container.innerHTML = '';
    nodes.forEach(n => container.appendChild(n));
  }

  // ── Render views ─────────────────────────────────────────────────────────

  function getBody() {
    return document.getElementById('xbrain-onboarding-modal').querySelector('.xb-body');
  }

  function dismiss() {
    sessionStorage.setItem(STORAGE_KEY, '1');
    document.getElementById('xbrain-onboarding-overlay').remove();
  }

  async function createSoloTeam() {
    try {
      const r = await apiCall('POST', '/v1/teams/self-solo', null, token);
      if (r.ok) return await r.json();
    } catch (_) {}
    return null;
  }

  function renderSoloWelcome() {
    const body = getBody();
    const title = el('h2', {}, 'Votre espace de travail est prêt');
    const desc = el('p', { class: 'xb-desc' },
      'Vous êtes en mode solo. Connectez votre organisation GitHub pour collaborer avec votre équipe.');
    const githubBtn = el('button', { class: 'xb-btn xb-btn-primary' }, '🔗 Connecter une org GitHub');
    githubBtn.onclick = () => { location.href = '/oauth/github'; };
    const soloBtn = el('button', { class: 'xb-btn xb-btn-secondary' }, 'Continuer en solo →');
    soloBtn.onclick = dismiss;
    const btnRow = el('div', { style: 'display:flex;gap:8px;margin-top:8px' }, githubBtn, soloBtn);
    setHtml(body, title, desc, btnRow);
  }

  function renderWelcome() {
    const body = getBody();
    const title = el('h2', {}, `Welcome to "${selectedTeam?.display_name || 'your team'}"!`);
    const desc = el('p', { class: 'xb-desc' }, 'Your team is set up. You\'re ready to use xbrain.');
    const startBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Get started →');
    startBtn.onclick = dismiss;

    // Phase 8 plan 08-07 — optional Granola connect step
    const granolaBtn = el('button', { class: 'xb-btn xb-btn-secondary' }, 'Connect Granola (optional)');
    granolaBtn.onclick = renderGranolaStep;

    setHtml(body, title, desc, startBtn, granolaBtn);
  }

  // After creating a team linked to a GitHub org, that org must have the xbrain
  // App installed so the backend (GitHub App token) can see the org in members'
  // /user/orgs and auto-join them on sign-in. The creator (org admin / App owner)
  // installs it here — once per org. Without this step the org never gets the
  // App, so teammates hit the install dead-end and can never auto-join.
  function renderInstallStep(githubOrg) {
    const body = getBody();
    const title = el('h2', {}, `"${selectedTeam?.display_name || 'Team'}" created ✓`);
    const desc = el('p', { class: 'xb-desc' },
      'One quick step: install xbrain on the ',
      el('strong', {}, githubOrg),
      ' GitHub org. This lets teammates in this org auto-join when they sign in, '
      + 'and lets the team read its repos.');
    const installBtn = el('button', { class: 'xb-btn xb-btn-primary' },
      `Install xbrain on ${githubOrg} →`);
    installBtn.onclick = () =>
      window.open('https://github.com/apps/xbrain-auth/installations/new', '_blank');
    const doneBtn = el('button', { class: 'xb-btn xb-btn-secondary' }, 'Done — continue →');
    doneBtn.onclick = renderWelcome;
    const note = el('p', { class: 'xb-desc', style: 'font-size:11.5px;margin-top:6px' },
      'Install on GitHub, then come back and click "Done". You only need to do this '
      + 'once per org — after that, teammates in the org join automatically.');
    setHtml(body, title, desc, installBtn, doneBtn, note);
  }

  // Phase 8 plan 08-07 — D1 RESEARCH.md: per-user Granola key submission.
  async function renderGranolaStep() {
    const body = getBody();

    setHtml(body, el('p', { class: 'xb-desc' }, 'Loading Granola status…'));

    const freshToken = await getToken();
    if (freshToken) {
      try {
        const r = await apiCall('GET', '/v1/me/granola-key', null, freshToken);
        if (r.ok) {
          const data = await r.json();
          if (data && data.connected === true) {
            return renderGranolaConnected(data);
          }
        }
      } catch (_e) {
        // fail-soft: fall through to connect mode
      }
    }

    const title = el('h2', {}, 'Connect Granola');
    const desc = el('p', { class: 'xb-desc' },
      'Paste your personal Granola API key. We\'ll poll your meetings privately and feed them into your team\'s memory. ' +
      'You can disconnect anytime. Your key is encrypted at rest.');
    const stepLabel = el('p', { class: 'xb-step-indicator' }, 'STEP 4 OF 4 — OPTIONAL');
    const keyInput = el('input', {
      class: 'xb-input',
      type: 'password',
      placeholder: 'Granola API key (gnlk_...)',
      autocomplete: 'off',
    });
    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });

    const saveBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Save & continue');
    const skipBtn = el('button', { class: 'xb-btn xb-btn-secondary' }, 'Skip for now');

    saveBtn.onclick = async () => {
      errorDiv.style.display = 'none';
      const apiKey = (keyInput.value || '').trim();
      if (apiKey.length < 8) {
        errorDiv.textContent = 'Please paste a valid Granola API key.';
        errorDiv.style.display = 'block';
        return;
      }
      saveBtn.disabled = true;
      skipBtn.disabled = true;
      // Pitfall 7 RESEARCH.md: refresh token just before submit.
      const submitToken = await getToken();
      if (!submitToken) {
        errorDiv.textContent = 'Authentication expired. Please refresh the page and try again.';
        errorDiv.style.display = 'block';
        saveBtn.disabled = false;
        skipBtn.disabled = false;
        return;
      }
      try {
        const r = await apiCall('POST', '/v1/me/granola-key', {
          api_key: apiKey,
          team_scope: selectedTeam?.slug || selectedTeam?.team_scope || '',
        }, submitToken);
        if (!r.ok) {
          const errBody = await r.json().catch(() => ({}));
          throw new Error(errBody.detail || `HTTP ${r.status}`);
        }
        dismiss();
      } catch (e) {
        errorDiv.textContent = `Could not save key: ${e.message || 'unknown error'}`;
        errorDiv.style.display = 'block';
        saveBtn.disabled = false;
        skipBtn.disabled = false;
      }
    };

    skipBtn.onclick = dismiss;

    setHtml(body, stepLabel, title, desc, keyInput, errorDiv, saveBtn, skipBtn);
  }

  // Phase 8 plan 08-07 Task 2 — Mode "already connected": allows key revocation.
  // Criterion 1 ROADMAP: the connection is revocable.
  function renderGranolaConnected(statusData) {
    const body = getBody();
    const stepLabel = el('p', { class: 'xb-step-indicator' }, 'GRANOLA CONNECTION');
    const title = el('h2', {}, '✓ Granola connecté');

    const teamScope = (statusData && statusData.team_scope) || (selectedTeam?.slug) || '';
    const desc = el('p', { class: 'xb-desc' },
      `Your Granola key is active${teamScope ? ' for team "' + teamScope + '"' : ''}. ` +
      'New meetings will be polled and added to your team\'s memory automatically. ' +
      'You can disconnect at any time — your existing meetings will remain in memory.');

    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const successDiv = el('p', { class: 'xb-desc', style: 'color:#7fc08f;display:none' });

    const closeBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Close');
    closeBtn.onclick = dismiss;

    const disconnectBtn = el('button', { class: 'xb-btn xb-btn-secondary' }, 'Déconnecter');
    disconnectBtn.onclick = async () => {
      errorDiv.style.display = 'none';
      disconnectBtn.disabled = true;
      closeBtn.disabled = true;
      const freshToken = await getToken();
      if (!freshToken) {
        errorDiv.textContent = 'Authentication expired. Please refresh and try again.';
        errorDiv.style.display = 'block';
        disconnectBtn.disabled = false;
        closeBtn.disabled = false;
        return;
      }
      try {
        const r = await apiCall('DELETE', '/v1/me/granola-key', null, freshToken);
        if (!r.ok && r.status !== 204) {
          const errBody = await r.json().catch(() => ({}));
          throw new Error(errBody.detail || `HTTP ${r.status}`);
        }
        successDiv.textContent = 'Granola déconnecté.';
        successDiv.style.display = 'block';
        title.textContent = 'Granola déconnecté';
        desc.textContent = 'Your Granola key has been removed. You can reconnect at any time.';
        disconnectBtn.style.display = 'none';
        const reconnectBtn = el('button', { class: 'xb-btn xb-btn-secondary' }, 'Reconnect');
        reconnectBtn.onclick = renderGranolaStep;
        body.appendChild(reconnectBtn);
        closeBtn.disabled = false;
      } catch (e) {
        errorDiv.textContent = `Could not disconnect: ${e.message || 'unknown error'}`;
        errorDiv.style.display = 'block';
        disconnectBtn.disabled = false;
        closeBtn.disabled = false;
      }
    };

    setHtml(body, stepLabel, title, desc, errorDiv, successDiv, closeBtn, disconnectBtn);
  }

  function renderPending(teamName) {
    const body = getBody();
    const title = el('h2', {}, 'Request sent');
    const desc = el('p', { class: 'xb-desc' },
      `Your request to join "${teamName}" has been submitted. An admin will review it shortly.`);
    const okBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Got it');
    okBtn.onclick = dismiss;
    setHtml(body, title, desc, okBtn);
  }

  async function joinTeam(team, errorDiv, btn) {
    btn.disabled = true;
    errorDiv.style.display = 'none';
    try {
      if (team.visibility === 'open') {
        const r = await apiCall('POST', `/v1/teams/${team.id}/join`, null, token);
        if (!r.ok && r.status !== 204) throw new Error('join failed');
        selectedTeam = team;
        renderWelcome();
      } else {
        const r = await apiCall('POST', `/v1/teams/${team.id}/join-request`, null, token);
        if (!r.ok) throw new Error('request failed');
        renderPending(team.display_name);
      }
    } catch {
      errorDiv.textContent = 'Something went wrong. Please try again.';
      errorDiv.style.display = 'block';
      btn.disabled = false;
    }
  }

  async function createTeam(name, errorDiv, btn, githubOrg, description) {
    const slug = name.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '').slice(0, 64);
    if (!slug) {
      errorDiv.textContent = 'Team name is required.';
      errorDiv.style.display = 'block';
      return;
    }
    btn.disabled = true;
    errorDiv.style.display = 'none';
    try {
      // TEMP stopgap (2026-06-04): create teams OPEN so a 2nd org member can
      // Join without admin approval, until the LibreChat->memory-api GitHub
      // token bridge lands (task #145). Revert to 'closed' once auto-join works.
      const body = { slug, display_name: name, visibility: 'open' };
      if (githubOrg) body.github_org = githubOrg;
      if (description) body.description = description;
      const r = await apiCall('POST', '/v1/teams/self', body, token);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || 'create failed');
      }
      selectedTeam = await r.json();
      // If the team is linked to a GitHub org, route to the install step so the
      // creator installs the App on that org (enables teammate auto-join). No
      // org → straight to welcome.
      if (githubOrg) renderInstallStep(githubOrg);
      else renderWelcome();
    } catch (e) {
      errorDiv.textContent = e.message || 'Something went wrong. Please try again.';
      errorDiv.style.display = 'block';
      btn.disabled = false;
    }
  }

  function renderPicker() {
    const body = getBody();
    const title = el('h2', {}, 'Join or create your team');
    const desc = el('p', { class: 'xb-desc' },
      'Select your GitHub organization, or search for a team by name.');

    const githubSection = el('div', {});
    const divider = el('p', {
      class: 'xb-desc',
      style: 'margin-top:16px;margin-bottom:8px;display:none',
    }, 'Or search manually:');
    const nameInput = el('input', {
      class: 'xb-input', type: 'text', placeholder: 'Team name...',
    });
    const descInput = el('textarea', {
      class: 'xb-input', rows: '2',
      placeholder: 'What does this team work on? (optional, helps contextualize the team for the AI)',
      style: 'resize:vertical',
    });
    const orgSelect = el('select', { class: 'xb-input' });
    orgSelect.appendChild(el('option', { value: '' }, '— Link to a GitHub org (optional) —'));
    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const resultDiv = el('div', {});
    const actionDiv = el('div', {});

    let searchTimer = null;
    let searchHadExactMatch = false;
    // Must match GITHUB_APP_SLUG on the backend — the real app slug is xbrain-auth.
    const INSTALL_URL = 'https://github.com/apps/xbrain-auth/installations/new';

    function updateAction(q) {
      actionDiv.innerHTML = '';
      if (!q) return;
      // If the search just returned an exact slug/name match, suppress the
      // "Create" CTA — the team already exists; the user should Join or
      // Request access via the result button above, not create a duplicate.
      if (searchHadExactMatch) return;
      const btn = el('button', {
        class: 'xb-btn xb-btn-primary', style: 'margin-top:8px',
      }, `Create "${q}" →`);
      btn.onclick = () => createTeam(q, errorDiv, btn, orgSelect.value || null, descInput.value.trim());
      actionDiv.appendChild(btn);
    }

    nameInput.oninput = () => {
      clearTimeout(searchTimer);
      const q = nameInput.value.trim();
      errorDiv.style.display = 'none';
      searchHadExactMatch = false;
      updateAction(q);
      resultDiv.innerHTML = '';
      if (q.length < 2) return;
      resultDiv.innerHTML = '<span class="xb-spinner"></span>';
      searchTimer = setTimeout(async () => {
        try {
          const r = await apiCall('GET', `/v1/teams/search?name=${encodeURIComponent(q)}`, null, token);
          if (!r.ok) throw new Error();
          const results = await r.json();
          resultDiv.innerHTML = '';
          const qLower = q.toLowerCase();
          results.forEach(t => {
            const isExact = t.slug.toLowerCase() === qLower
              || (t.display_name || '').toLowerCase() === qLower;
            if (isExact) searchHadExactMatch = true;
            const btn = el('button', { class: 'xb-team-btn' },
              el('strong', {}, t.display_name),
              document.createTextNode(
                ` — ${t.visibility === 'open' ? '🔓 Join' : '🔒 Request access'}`,
              ),
            );
            btn.onclick = () => joinTeam(t, errorDiv, btn);
            resultDiv.appendChild(btn);
          });
          // Hide the duplicate-Create CTA if an exact match was found
          updateAction(q);
        } catch {
          resultDiv.innerHTML = '';
        }
      }, 400);
    };

    // descInput is placed at the TOP (right after the subtitle) so the project
    // description is captured for EVERY create path — including the one-click
    // GitHub-org buttons below, which would otherwise bypass a field placed
    // lower in the form.
    const descLabel = el('p', { class: 'xb-desc', style: 'margin-bottom:6px;margin-top:4px' },
      'Team / project description (optional — helps the AI contextualize your team):');
    setHtml(body, title, desc, descLabel, descInput, githubSection, divider, nameInput, orgSelect, errorDiv, resultDiv, actionDiv);

    // Helper: probe whether a team with the given slug already exists in xbrain,
    // regardless of whether the current user is auto-verified as a member.
    // Used to distinguish "Case C: team exists but install missing" from
    // "Case D: no team yet, allow create".
    async function findTeamBySlug(slug) {
      try {
        const r = await apiCall('GET', `/v1/teams/search?name=${encodeURIComponent(slug)}`, null, token);
        if (!r.ok) return null;
        const list = await r.json();
        const lower = slug.toLowerCase();
        return list.find(t => (t.slug || '').toLowerCase() === lower) || null;
      } catch { return null; }
    }

    // Fetch GitHub orgs (LibreChat endpoint — uses user's own token → includes private orgs)
    // + matched xbrain teams in parallel
    githubSection.innerHTML = '<span class="xb-spinner"></span>';

    Promise.all([
      fetch('/api/xbrain/github-orgs', {
        headers: { Authorization: 'Bearer ' + _libreToken },
      }).then(r => r.ok ? r.json() : []).catch(() => []),
      apiCall('GET', '/v1/teams/github-matches', null, token)
        .then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then(async ([orgs, matches]) => {
      githubSection.innerHTML = '';

      // Populate the manual org-select now that we have the org list.
      if (orgs && orgs.length > 0) {
        orgs.forEach(org => {
          orgSelect.appendChild(el('option', { value: org.login }, org.login));
        });
      }

      if (!orgs || orgs.length === 0) {
        nameInput.placeholder = 'Team name or slug...';
        const ghBtn = el('button', { class: 'xb-btn xb-btn-secondary', style: 'margin-bottom:12px;width:100%' },
          '🔗 Authorize GitHub org access');
        ghBtn.onclick = () => { location.href = '/oauth/github'; };
        githubSection.appendChild(ghBtn);
        return;
      }

      // Build map: github_org login → xbrain team (if already exists)
      const orgTeamMap = {};
      if (matches) matches.forEach(t => { if (t.github_org) orgTeamMap[t.github_org] = t; });

      githubSection.appendChild(el('p', { class: 'xb-desc', style: 'margin-bottom:8px' },
        'Your GitHub organizations:'));

      // Render each org. Use Promise.all so the per-org probe for "Case C"
      // (team exists but membership not auto-verified) runs in parallel.
      await Promise.all(orgs.map(async org => {
        const xbrainTeam = orgTeamMap[org.login];
        const btn = el('button', { class: 'xb-team-btn', style: 'margin-bottom:8px' });
        githubSection.appendChild(btn);

        if (xbrainTeam) {
          // Case A/B: confirmed member of the org + team exists → Join or Request
          btn.appendChild(el('strong', {}, xbrainTeam.display_name));
          btn.appendChild(document.createTextNode(
            xbrainTeam.visibility === 'open' ? ' — 🔓 Join' : ' — 🔒 Request access',
          ));
          btn.onclick = () => joinTeam(xbrainTeam, errorDiv, btn);
          return;
        }

        // Probe whether a team exists for this org slug, even if the user is
        // not auto-verified as a member yet. Distinguishes:
        //   Case C — team exists, install missing → "Install xbrain"
        //   Case D — no team yet                   → "Create team"
        btn.appendChild(el('strong', {}, org.login));
        const placeholder = document.createTextNode(' — checking...');
        btn.appendChild(placeholder);
        btn.disabled = true;

        const existing = await findTeamBySlug(org.login);

        btn.textContent = '';
        btn.disabled = false;
        btn.appendChild(el('strong', {}, org.login));

        if (existing) {
          if (existing.visibility === 'open') {
            // TEMP stopgap (2026-06-04): open team → direct Join, no install /
            // backend membership check needed. Remove when task #145 (the
            // LibreChat->memory-api token bridge) lands.
            btn.appendChild(document.createTextNode(' — 🔓 Join'));
            btn.onclick = () => joinTeam(existing, errorDiv, btn);
            return;
          }
          // Case C — team exists but cannot auto-verify membership.
          // The xbrain GitHub App likely isn't installed on this org yet;
          // install grants the backend permission to confirm membership and
          // auto-join the user. Closed teams may still require admin approval
          // after install.
          btn.appendChild(document.createTextNode(' — 🔧 Install xbrain to join →'));
          btn.onclick = () => window.open(INSTALL_URL, '_blank');
          // Secondary affordance: let the user request manual access without
          // requiring the org admin to install the App right now.
          const secondary = el('button', {
            class: 'xb-team-btn-secondary',
            style: 'margin-top:4px;font-size:11.5px;color:var(--muted);'
              + 'background:none;border:0;cursor:pointer;text-align:left;padding:2px 8px;',
          }, '↳ or request manual access');
          secondary.onclick = () => joinTeam(existing, errorDiv, secondary);
          githubSection.appendChild(secondary);
        } else {
          // Case D — no team yet for this org → create
          btn.appendChild(document.createTextNode(' — Create team →'));
          const orgName = org.login.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
          btn.onclick = () => createTeam(orgName, errorDiv, btn, org.login, descInput.value.trim());
        }
      }));

      divider.style.display = '';
    });
  }

  // ── Mount ────────────────────────────────────────────────────────────────

  function mount(renderFn = renderPicker) {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const overlay = el('div', { id: 'xbrain-onboarding-overlay' });
    const modal = el('div', { id: 'xbrain-onboarding-modal' });
    const bodyDiv = el('div', { class: 'xb-body' });
    const logoutBtn = el('button', { class: 'xb-logout' }, 'Wrong account? Log out');
    logoutBtn.onclick = () => {
      fetch('/api/auth/logout', {
        method: 'POST',
        headers: _libreToken ? { Authorization: 'Bearer ' + _libreToken } : {},
      }).finally(() => { location.href = '/'; });
    };
    modal.appendChild(bodyDiv);
    modal.appendChild(logoutBtn);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    renderFn();
  }

  // ── Boot ─────────────────────────────────────────────────────────────────

  async function boot() {
    if (document.readyState === 'loading') {
      await new Promise(r => document.addEventListener('DOMContentLoaded', r));
    }

    // Poll for the LibreChat JWT captured by the XHR interceptor above.
    // LibreChat (axios) fires its first authenticated request within ~1-2s of React
    // mounting. After SPA login (no full page reload), the next axios call triggers
    // the interceptor and _libreToken is set. Max wait: ~2 min.
    let retries = 0;
    while (retries < 40) {
      await new Promise(r => setTimeout(r, retries === 0 ? 500 : 2000));
      token = await getToken(); // returns null while _libreToken is still null
      if (token) break;
      retries++;
    }

    if (!token) return;

    const team = await checkTeam(token);
    if (team) {
      sessionStorage.setItem(STORAGE_KEY, '1');
      return;
    }

    // Check if this user has GitHub orgs available (GitHub-connected user).
    // Google-only users get a solo workspace created automatically.
    const orgs = await fetch('/api/xbrain/github-orgs', {
      headers: { Authorization: 'Bearer ' + _libreToken },
    }).then(r => r.ok ? r.json() : []).catch(() => []);

    if (!orgs || orgs.length === 0) {
      const soloTeam = await createSoloTeam();
      if (soloTeam) {
        selectedTeam = soloTeam;
        mount(renderSoloWelcome);
        return;
      }
    }

    mount();
  }

  boot();
})();

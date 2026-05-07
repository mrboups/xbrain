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
  `;

  // ── State ────────────────────────────────────────────────────────────────

  const PROVIDERS = ['anthropic', 'openai', 'xai', 'google', 'mistral', 'cohere'];
  let token = null;
  let step = 1;
  let searchResults = [];
  let selectedTeam = null;
  let apiKeys = [{ provider: 'anthropic', key: '' }];

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

  // ── Render steps ─────────────────────────────────────────────────────────

  function renderStep1(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Step 1 of 4');
    const title = el('h2', {}, 'Join or create a team');
    const desc = el('p', { class: 'xb-desc' }, 'xbrain organizes memory by team. Join yours to get started.');

    const nameInput = el('input', {
      class: 'xb-input', type: 'text', placeholder: 'Team name or slug...',
    });
    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const resultDiv = el('div', {});

    let searchTimer = null;
    nameInput.oninput = () => {
      clearTimeout(searchTimer);
      const q = nameInput.value.trim();
      if (q.length < 2) { resultDiv.innerHTML = ''; return; }
      searchTimer = setTimeout(() => doSearch(q, resultDiv, errorDiv), 400);
    };

    const hasGithub = false; // TODO: check if user has github linked via /v1/me
    const hint = hasGithub
      ? el('p', { class: 'xb-desc', style: 'margin-bottom:12px' },
          'Your GitHub organizations are listed below.')
      : el('p', { class: 'xb-desc', style: 'margin-bottom:12px' },
          'Type your team name to search, or create a new one.');

    const createBtn = el('button', {
      class: 'xb-btn xb-btn-secondary',
      style: 'margin-left:0',
      onclick: () => { selectedTeam = null; goStep(2); },
    }, '+ Create a new team');

    setHtml(body, indicator, title, desc, hint, errorDiv, nameInput, resultDiv, createBtn);
  }

  async function doSearch(q, resultDiv, errorDiv) {
    resultDiv.innerHTML = '<span class="xb-spinner"></span> Searching...';
    try {
      const r = await apiCall('GET', `/v1/teams/search?name=${encodeURIComponent(q)}`, null, token);
      if (!r.ok) throw new Error('search failed');
      searchResults = await r.json();
      resultDiv.innerHTML = '';
      if (searchResults.length === 0) {
        resultDiv.appendChild(el('p', { class: 'xb-desc', style: 'margin-bottom:0' },
          'No team found. You can create one below.'));
        return;
      }
      searchResults.forEach(t => {
        const btn = el('button', {
          class: 'xb-team-btn',
          onclick: () => { selectedTeam = t; goStep(2); },
        },
          el('strong', {}, t.display_name),
          document.createTextNode(` — ${t.visibility === 'open' ? '🔓 Open' : '🔒 Private'}`),
        );
        resultDiv.appendChild(btn);
      });
    } catch {
      errorDiv.textContent = 'Search failed. Please try again.';
      errorDiv.style.display = 'block';
      resultDiv.innerHTML = '';
    }
  }

  function renderStep2(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Step 2 of 4');

    if (selectedTeam) {
      // Join flow
      const title = el('h2', {}, `Join "${selectedTeam.display_name}"?`);
      const desc = el('p', { class: 'xb-desc' },
        selectedTeam.visibility === 'open'
          ? 'This team is open. You can join directly.'
          : 'This team is private. Your request will be reviewed by an admin.',
      );
      const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
      const joinBtn = el('button', { class: 'xb-btn xb-btn-primary' },
        selectedTeam.visibility === 'open' ? 'Join team' : 'Request access',
      );
      const backBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(1) }, '← Back');
      joinBtn.onclick = async () => {
        joinBtn.disabled = true;
        errorDiv.style.display = 'none';
        try {
          if (selectedTeam.visibility === 'open') {
            const r = await apiCall('POST', `/v1/teams/${selectedTeam.id}/join`, null, token);
            if (!r.ok && r.status !== 204) throw new Error('join failed');
            goStep(3);
          } else {
            const r = await apiCall('POST', `/v1/teams/${selectedTeam.id}/join-request`, null, token);
            if (!r.ok) throw new Error('request failed');
            showPendingConfirmation(selectedTeam.display_name);
          }
        } catch {
          errorDiv.textContent = 'Something went wrong. Please try again.';
          errorDiv.style.display = 'block';
          joinBtn.disabled = false;
        }
      };
      setHtml(body, indicator, title, desc, errorDiv, joinBtn, backBtn);
    } else {
      // Create flow
      const title = el('h2', {}, 'Create your team');
      const desc = el('p', { class: 'xb-desc' }, 'You will become the founding admin of this team.');
      const nameInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Team name (e.g. Acme)',
      });
      const slugInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Team slug (e.g. acme)',
      });
      nameInput.oninput = () => {
        slugInput.value = nameInput.value.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
      };
      const visSelect = el('select', { class: 'xb-select' },
        el('option', { value: 'closed' }, '🔒 Private (requires approval)'),
        el('option', { value: 'open' }, '🔓 Open (anyone can join)'),
      );
      const orgInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'GitHub organization (optional, e.g. your-github-org)',
      });
      const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
      const createBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Create team');
      const backBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(1) }, '← Back');

      createBtn.onclick = async () => {
        const slug = slugInput.value.trim();
        const display = nameInput.value.trim();
        if (!slug || !display) {
          errorDiv.textContent = 'Name and slug are required.';
          errorDiv.style.display = 'block';
          return;
        }
        createBtn.disabled = true;
        errorDiv.style.display = 'none';
        try {
          const r = await apiCall('POST', '/v1/teams/self', {
            slug,
            display_name: display,
            visibility: visSelect.value,
            github_org: orgInput.value.trim() || null,
          }, token);
          if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || 'create failed');
          }
          selectedTeam = await r.json();
          goStep(3);
        } catch (e) {
          errorDiv.textContent = e.message || 'Something went wrong. Please try again.';
          errorDiv.style.display = 'block';
          createBtn.disabled = false;
        }
      };

      setHtml(body, indicator, title, desc, nameInput, slugInput, visSelect, orgInput, errorDiv, createBtn, backBtn);
    }
  }

  function renderStep3(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Step 3 of 4');
    const title = el('h2', {}, 'Team API keys (optional)');
    const desc = el('p', { class: 'xb-desc' }, 'Set shared API keys for your team members. You can add more later.');

    const keysList = el('div', {});

    function renderKeys() {
      keysList.innerHTML = '';
      apiKeys.forEach((k, i) => {
        const provSelect = el('select', { class: 'xb-select', style: 'margin-bottom:0' });
        PROVIDERS.forEach(p => {
          const opt = el('option', { value: p }, p);
          if (p === k.provider) opt.selected = true;
          provSelect.appendChild(opt);
        });
        provSelect.onchange = () => { apiKeys[i].provider = provSelect.value; };
        const keyInput = el('input', {
          class: 'xb-input', type: 'password', placeholder: 'sk-...', style: 'margin-bottom:0',
          value: k.key,
        });
        keyInput.oninput = () => { apiKeys[i].key = keyInput.value; };
        const delBtn = el('button', {}, '✕');
        delBtn.onclick = () => { apiKeys.splice(i, 1); renderKeys(); };
        keysList.appendChild(el('div', { class: 'xb-key-row' }, provSelect, keyInput, delBtn));
      });
    }
    renderKeys();

    const addBtn = el('button', { class: 'xb-btn xb-btn-secondary', style: 'margin-left:0;margin-bottom:16px' }, '+ Add a key');
    addBtn.onclick = () => { apiKeys.push({ provider: 'openai', key: '' }); renderKeys(); };

    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const saveBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Save and continue');
    const skipBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(4) }, 'Skip →');

    saveBtn.onclick = async () => {
      const filled = apiKeys.filter(k => k.key.trim());
      if (filled.length === 0) { goStep(4); return; }
      saveBtn.disabled = true;
      errorDiv.style.display = 'none';
      try {
        const r = await apiCall('PUT', `/v1/teams/${selectedTeam.id}/api-keys`, {
          keys: filled.map(k => ({ provider: k.provider, api_key: k.key })),
        }, token);
        if (!r.ok) throw new Error('save failed');
        goStep(4);
      } catch {
        errorDiv.textContent = 'Failed to save. Try again or skip this step.';
        errorDiv.style.display = 'block';
        saveBtn.disabled = false;
      }
    };

    setHtml(body, indicator, title, desc, keysList, addBtn, errorDiv, saveBtn, skipBtn);
  }

  function renderStep4(body) {
    const title = el('h2', {}, `Welcome to "${selectedTeam?.display_name || 'your team'}"! 🎉`);
    const desc = el('p', { class: 'xb-desc' }, 'Your team is set up. You can now use xbrain with your teammates.');
    const startBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Get started →');
    startBtn.onclick = () => {
      sessionStorage.setItem(STORAGE_KEY, '1');
      document.getElementById('xbrain-onboarding-overlay').remove();
    };
    setHtml(body, title, desc, startBtn);
  }

  function showPendingConfirmation(teamName) {
    const modal = document.getElementById('xbrain-onboarding-modal');
    const body = modal.querySelector('.xb-body');
    const title = el('h2', {}, 'Request sent');
    const desc = el('p', { class: 'xb-desc' }, `Your request to join "${teamName}" has been submitted. An admin will review it shortly.`);
    const okBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Got it');
    okBtn.onclick = () => {
      sessionStorage.setItem(STORAGE_KEY, '1');
      document.getElementById('xbrain-onboarding-overlay').remove();
    };
    setHtml(body, title, desc, okBtn);
  }

  function goStep(n) {
    step = n;
    const modal = document.getElementById('xbrain-onboarding-modal');
    const body = modal.querySelector('.xb-body');
    if (n === 1) renderStep1(body);
    else if (n === 2) renderStep2(body);
    else if (n === 3) renderStep3(body);
    else if (n === 4) renderStep4(body);
  }

  // ── Mount ────────────────────────────────────────────────────────────────

  function mount() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const overlay = el('div', { id: 'xbrain-onboarding-overlay' });
    const modal = el('div', { id: 'xbrain-onboarding-modal' });
    const bodyDiv = el('div', { class: 'xb-body' });
    modal.appendChild(bodyDiv);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    renderStep1(bodyDiv);
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

    mount();
  }

  boot();
})();

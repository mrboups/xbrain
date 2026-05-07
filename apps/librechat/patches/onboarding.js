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

  async function getToken() {
    try {
      const r = await fetch('/api/xbrain/token', { credentials: 'include' });
      if (!r.ok) return null;
      const { token } = await r.json();
      return token;
    } catch {
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
      background: rgba(0,0,0,0.7);
      display: flex; align-items: center; justify-content: center;
      font-family: system-ui, sans-serif;
    }
    #xbrain-onboarding-modal {
      background: #1e1e2e; color: #cdd6f4;
      border-radius: 12px; padding: 32px; width: 480px; max-width: 95vw;
      box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    }
    #xbrain-onboarding-modal h2 { margin: 0 0 8px; font-size: 20px; color: #89b4fa; }
    #xbrain-onboarding-modal p  { margin: 0 0 20px; font-size: 14px; color: #a6adc8; }
    .xb-input {
      width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #45475a;
      background: #313244; color: #cdd6f4; font-size: 14px; box-sizing: border-box;
      margin-bottom: 12px;
    }
    .xb-input:focus { outline: none; border-color: #89b4fa; }
    .xb-select {
      width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #45475a;
      background: #313244; color: #cdd6f4; font-size: 14px; box-sizing: border-box;
      margin-bottom: 12px;
    }
    .xb-btn {
      padding: 10px 20px; border-radius: 8px; border: none; font-size: 14px;
      cursor: pointer; font-weight: 600;
    }
    .xb-btn-primary { background: #89b4fa; color: #1e1e2e; }
    .xb-btn-secondary {
      background: transparent; color: #a6adc8; border: 1px solid #45475a; margin-left: 8px;
    }
    .xb-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .xb-key-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
    .xb-key-row select, .xb-key-row input { flex: 1; }
    .xb-key-row button { background: none; border: none; color: #f38ba8; cursor: pointer; font-size: 16px; }
    .xb-error { color: #f38ba8; font-size: 13px; margin-bottom: 12px; }
    .xb-team-btn {
      display: block; width: 100%; text-align: left; padding: 12px 16px; margin-bottom: 8px;
      border-radius: 8px; border: 1px solid #45475a; background: #313244;
      color: #cdd6f4; cursor: pointer; font-size: 14px;
    }
    .xb-team-btn:hover { border-color: #89b4fa; }
    .xb-step-indicator { font-size: 12px; color: #6c7086; margin-bottom: 20px; }
    .xb-spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #45475a;
      border-top-color: #89b4fa; border-radius: 50%; animation: xb-spin 0.7s linear infinite; margin-right: 8px; }
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
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Étape 1 sur 4');
    const title = el('h2', {}, 'Rejoindre ou créer une équipe');
    const desc = el('p', {}, 'xbrain organise la mémoire par équipe. Rejoignez la vôtre pour commencer.');

    const nameInput = el('input', {
      class: 'xb-input', type: 'text', placeholder: 'Nom ou slug de l\'équipe...',
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
    const githubHint = hasGithub
      ? el('p', { style: 'font-size:13px;color:#a6adc8;margin-bottom:12px' },
          'Vos équipes GitHub sont listées ci-dessous.')
      : el('p', { style: 'font-size:13px;color:#a6adc8;margin-bottom:12px' },
          'Tapez le nom de votre équipe, ou créez-en une nouvelle.');

    const createBtn = el('button', {
      class: 'xb-btn xb-btn-secondary',
      onclick: () => { selectedTeam = null; goStep(2); },
    }, '+ Créer une nouvelle équipe');

    setHtml(body, indicator, title, desc, githubHint, errorDiv, nameInput, resultDiv, createBtn);
  }

  async function doSearch(q, resultDiv, errorDiv) {
    resultDiv.innerHTML = '<span class="xb-spinner"></span> Recherche...';
    try {
      const r = await apiCall('GET', `/v1/teams/search?name=${encodeURIComponent(q)}`, null, token);
      if (!r.ok) throw new Error('search failed');
      searchResults = await r.json();
      resultDiv.innerHTML = '';
      if (searchResults.length === 0) {
        resultDiv.appendChild(el('p', { style: 'color:#a6adc8;font-size:13px' },
          'Aucune équipe trouvée. Vous pouvez en créer une.'));
        return;
      }
      searchResults.forEach(t => {
        const btn = el('button', {
          class: 'xb-team-btn',
          onclick: () => { selectedTeam = t; goStep(2); },
        },
          el('strong', {}, t.display_name),
          document.createTextNode(` — ${t.visibility === 'open' ? '🔓 Ouverte' : '🔒 Fermée'}`),
        );
        resultDiv.appendChild(btn);
      });
    } catch {
      errorDiv.textContent = 'Erreur lors de la recherche. Réessayez.';
      errorDiv.style.display = 'block';
      resultDiv.innerHTML = '';
    }
  }

  function renderStep2(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Étape 2 sur 4');

    if (selectedTeam) {
      // Join flow
      const title = el('h2', {}, `Rejoindre "${selectedTeam.display_name}" ?`);
      const desc = el('p', {},
        selectedTeam.visibility === 'open'
          ? 'Cette équipe est ouverte. Vous pouvez rejoindre directement.'
          : 'Cette équipe est privée. Votre demande sera examinée par un admin.',
      );
      const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
      const joinBtn = el('button', { class: 'xb-btn xb-btn-primary' },
        selectedTeam.visibility === 'open' ? 'Rejoindre' : 'Demander l\'accès',
      );
      const backBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(1) }, '← Retour');
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
            // Show pending state — jump to step 4 with special message
            showPendingConfirmation(selectedTeam.display_name);
          }
        } catch {
          errorDiv.textContent = 'Erreur — réessayez.';
          errorDiv.style.display = 'block';
          joinBtn.disabled = false;
        }
      };
      setHtml(body, indicator, title, desc, errorDiv, joinBtn, backBtn);
    } else {
      // Create flow
      const title = el('h2', {}, 'Créer votre équipe');
      const desc = el('p', {}, 'Vous deviendrez l\'administrateur fondateur de cette équipe.');
      const nameInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Nom de l\'équipe (ex: Acme)',
      });
      const slugInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Identifiant (ex: acme)',
      });
      nameInput.oninput = () => {
        slugInput.value = nameInput.value.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
      };
      const visSelect = el('select', { class: 'xb-select' },
        el('option', { value: 'closed' }, '🔒 Fermée (approbation requise)'),
        el('option', { value: 'open' }, '🔓 Ouverte (rejoindre librement)'),
      );
      const orgInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Organisation GitHub (optionnel, ex: your-github-org)',
      });
      const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
      const createBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Créer l\'équipe');
      const backBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(1) }, '← Retour');

      createBtn.onclick = async () => {
        const slug = slugInput.value.trim();
        const display = nameInput.value.trim();
        if (!slug || !display) {
          errorDiv.textContent = 'Nom et identifiant requis.';
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
          errorDiv.textContent = e.message || 'Erreur — réessayez.';
          errorDiv.style.display = 'block';
          createBtn.disabled = false;
        }
      };

      setHtml(body, indicator, title, desc, nameInput, slugInput, visSelect, orgInput, errorDiv, createBtn, backBtn);
    }
  }

  function renderStep3(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Étape 3 sur 4');
    const title = el('h2', {}, 'Clés API de l\'équipe (optionnel)');
    const desc = el('p', {}, 'Définissez des clés partagées pour les membres de l\'équipe. Vous pourrez en ajouter plus tard.');

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

    const addBtn = el('button', { class: 'xb-btn xb-btn-secondary', style: 'margin-bottom:16px' }, '+ Ajouter une clé');
    addBtn.onclick = () => { apiKeys.push({ provider: 'openai', key: '' }); renderKeys(); };

    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const saveBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Enregistrer et continuer');
    const skipBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(4) }, 'Passer →');

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
        errorDiv.textContent = 'Erreur — réessayez ou passez cette étape.';
        errorDiv.style.display = 'block';
        saveBtn.disabled = false;
      }
    };

    setHtml(body, indicator, title, desc, keysList, addBtn, errorDiv, saveBtn, skipBtn);
  }

  function renderStep4(body) {
    const title = el('h2', {}, `🎉 Bienvenue dans "${selectedTeam?.display_name || 'votre équipe'}" !`);
    const desc = el('p', {}, 'Votre équipe est configurée. Vous pouvez maintenant utiliser xbrain avec vos collègues.');
    const startBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Commencer →');
    startBtn.onclick = () => {
      sessionStorage.setItem(STORAGE_KEY, '1');
      document.getElementById('xbrain-onboarding-overlay').remove();
    };
    setHtml(body, title, desc, startBtn);
  }

  function showPendingConfirmation(teamName) {
    const modal = document.getElementById('xbrain-onboarding-modal');
    const body = modal.querySelector('.xb-body');
    const title = el('h2', {}, 'Demande envoyée');
    const desc = el('p', {}, `Votre demande pour rejoindre "${teamName}" a été soumise. Un administrateur l'examinera prochainement.`);
    const okBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Compris');
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

    // LibreChat is a React SPA: the initial page load may be the login screen.
    // After login, React Router navigates client-side (no full reload), so this
    // IIFE does not re-execute. We poll until we get a valid token (= user is
    // authenticated), then check team membership once.
    let retries = 0;
    const POLL_INTERVAL = 3000; // 3s between checks
    const MAX_RETRIES = 40;     // ~2 min max wait

    while (retries < MAX_RETRIES) {
      await new Promise(r => setTimeout(r, retries === 0 ? 1500 : POLL_INTERVAL));
      token = await getToken();
      if (token) break;
      retries++;
    }

    if (!token) return; // User never authenticated in the time window

    const team = await checkTeam(token);
    if (team) {
      sessionStorage.setItem(STORAGE_KEY, '1');
      return;
    }

    mount();
  }

  boot();
})();

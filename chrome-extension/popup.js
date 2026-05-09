/**
 * xbrain Web Clipper — Popup script
 *
 * Flux :
 *  1. Au chargement : demander le texte sélectionné au content script via GET_SELECTION
 *  2. Sur click "Envoyer au brain" :
 *     a. Demander l'ID token au background service worker (GET_ID_TOKEN)
 *     b. Construire le payload avec les champs du formulaire
 *     c. Envoyer SEND_TO_BRAIN au background avec le token + payload
 *     d. Afficher le statut (succès ou erreur)
 */

"use strict";

/** Utilitaire : afficher un message de statut */
function showStatus(message, type) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.className = type; // "success" | "error" | "loading"
}

function hideStatus() {
  const el = document.getElementById("status");
  el.className = "";
  el.textContent = "";
}

/**
 * Obtenir le tab actif de la fenêtre courante.
 * Retourne null si aucun tab accessible (ex: chrome:// pages).
 */
async function getActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab || null;
  } catch {
    return null;
  }
}

/**
 * Demander au content script le texte sélectionné + URL + titre.
 * Retourne { selectedText, url, title } ou null si le content script
 * n'est pas accessible (pages chrome://, extensions://, etc.).
 */
async function getSelectionFromPage(tab) {
  if (!tab || !tab.id) return null;
  // Les content scripts ne fonctionnent pas sur les pages chrome:// ou about://
  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("about:")) {
    return null;
  }

  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, { type: "GET_SELECTION" }, (response) => {
      if (chrome.runtime.lastError) {
        // Content script non injecté (ex: page chargée avant installation de l'extension)
        resolve(null);
        return;
      }
      resolve(response || null);
    });
  });
}

/**
 * Extraire le hostname d'une URL pour le champ source.
 * Ex: "https://example.com/page" → "example.com"
 */
function hostnameFromUrl(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "unknown";
  }
}

const MEMORY_API_BASE = "https://api.grooveos.app";

/** État partagé */
let currentTabUrl = "";
let currentTabTitle = "";

/**
 * Charger les équipes de l'utilisateur via GET /v1/teams/my-teams.
 * Peuple le <select id="teamScope"> avec les vraies équipes du compte connecté.
 */
async function loadUserTeams(idToken) {
  const teamSelect = document.getElementById("teamScope");
  try {
    const res = await fetch(`${MEMORY_API_BASE}/v1/teams/my-teams`, {
      headers: { Authorization: `Bearer ${idToken}` },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const teams = await res.json();

    teamSelect.innerHTML = "";
    if (teams.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "— aucune équipe —";
      opt.disabled = true;
      opt.selected = true;
      teamSelect.appendChild(opt);
      return;
    }

    for (const t of teams) {
      const opt = document.createElement("option");
      opt.value = t.slug;
      opt.textContent = t.display_name || t.slug;
      teamSelect.appendChild(opt);
    }
    // Auto-sélectionner si une seule équipe
    if (teams.length === 1) teamSelect.selectedIndex = 0;
  } catch (err) {
    // Fail-soft : laisser le champ vide avec un message
    teamSelect.innerHTML = `<option value="" disabled selected>Erreur chargement équipes</option>`;
    console.warn("loadUserTeams failed:", err);
  }
}

/** Initialisation au chargement du popup */
document.addEventListener("DOMContentLoaded", async () => {
  const contentArea = document.getElementById("content");
  const sourceHint = document.getElementById("sourceHint");
  const sendBtn = document.getElementById("sendBtn");
  const teamSelect = document.getElementById("teamScope");

  // Placeholder pendant le chargement
  teamSelect.innerHTML = `<option value="" disabled selected>Connexion…</option>`;
  sendBtn.disabled = true;

  // 1. Récupérer le tab actif + la sélection
  const tab = await getActiveTab();
  if (tab) {
    currentTabUrl = tab.url || "";
    currentTabTitle = tab.title || "";
  }

  const selection = await getSelectionFromPage(tab);
  if (selection && selection.selectedText && selection.selectedText.trim()) {
    contentArea.value = selection.selectedText.trim();
  }
  if (currentTabUrl) {
    sourceHint.textContent = `Source : ${hostnameFromUrl(currentTabUrl)}`;
  }

  // 2. Authentification + chargement des équipes en parallèle
  const tokenResponse = await chrome.runtime.sendMessage({ type: "GET_ID_TOKEN" });
  if (tokenResponse && tokenResponse.idToken) {
    await loadUserTeams(tokenResponse.idToken);
    sendBtn.disabled = false;
  } else {
    teamSelect.innerHTML = `<option value="" disabled selected>Non connecté</option>`;
    showStatus("Connexion Google requise — réessayez.", "error");
  }

  // 3. Écoute du bouton "Envoyer au brain"
  sendBtn.addEventListener("click", handleSend);
});

async function handleSend() {
  const sendBtn = document.getElementById("sendBtn");
  const content = document.getElementById("content").value.trim();
  const teamScope = document.getElementById("teamScope").value;
  const projectScope = document.getElementById("projectScope").value.trim() || null;
  const truthLevel = document.querySelector('input[name="truthLevel"]:checked')?.value || "EPHEMERAL";

  if (!content) {
    showStatus("Le contenu ne peut pas être vide.", "error");
    return;
  }
  if (!teamScope) {
    showStatus("Sélectionnez une équipe.", "error");
    return;
  }

  sendBtn.disabled = true;
  showStatus("Authentification en cours…", "loading");

  try {
    // a. Obtenir l'ID token depuis le background service worker
    const tokenResponse = await chrome.runtime.sendMessage({ type: "GET_ID_TOKEN" });

    if (!tokenResponse || tokenResponse.error) {
      const errMsg = tokenResponse?.error || "Impossible d'obtenir le token Google.";
      showStatus(`Erreur d'authentification : ${errMsg}`, "error");
      sendBtn.disabled = false;
      return;
    }

    const { idToken } = tokenResponse;

    showStatus("Envoi au brain…", "loading");

    // b. Construire le payload selon le contrat de tagging xbrain
    const hostname = hostnameFromUrl(currentTabUrl);
    const payload = {
      content,
      team_scope: teamScope,
      project_scope: projectScope,
      visibility: "team",
      confidence: 1.0,
      truth_level: truthLevel,
      source: `chrome:${hostname}`,
      validation_status: "pending",
    };

    // c. Envoyer via le background service worker
    const sendResponse = await chrome.runtime.sendMessage({
      type: "SEND_TO_BRAIN",
      idToken,
      payload,
    });

    if (!sendResponse || sendResponse.error) {
      const errMsg = sendResponse?.error || "Erreur réseau inconnue.";

      // Si token expiré (401), vider le cache et suggérer de réessayer
      if (errMsg.includes("401")) {
        await chrome.storage.session.remove(["xbrain_id_token", "xbrain_id_token_expiry"]);
        showStatus("Token expiré — veuillez réessayer pour vous reconnecter.", "error");
      } else {
        showStatus(`Erreur : ${errMsg}`, "error");
      }
    } else {
      // d. Succès
      showStatus("Envoyé au brain !", "success");
      // Réinitialiser le contenu après envoi réussi
      document.getElementById("content").value = "";
    }
  } catch (err) {
    showStatus(`Erreur inattendue : ${err.message}`, "error");
  } finally {
    sendBtn.disabled = false;
  }
}

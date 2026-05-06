/**
 * xbrain Web Clipper — Content Script
 *
 * Script léger injecté dans toutes les pages web (<all_urls>).
 * Responsabilité unique : répondre aux requêtes GET_SELECTION
 * en retournant le texte sélectionné + l'URL + le titre de la page.
 *
 * Pas de lecture automatique du DOM, pas d'envoi spontané de données.
 * (T-05-04-05 accepted — content.js minimal, écoute seulement)
 */

"use strict";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "GET_SELECTION") {
    sendResponse({
      selectedText: window.getSelection().toString(),
      url: window.location.href,
      title: document.title,
    });
    return true; // réponse synchrone OK, mais return true pour cohérence
  }
  // Ignorer les messages non reconnus
});

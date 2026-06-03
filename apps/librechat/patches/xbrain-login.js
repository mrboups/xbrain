/**
 * xbrain-login.js — Groove OS login-page rebrand.
 *
 * On the auth pages only (/login, /register, /), hides:
 *   - the email/password login form (keep social / GitHub login),
 *   - the "OR" divider,
 *   - the "Don't have an account? Sign up" prompt,
 *   - the LibreChat logo image.
 *
 * SPA-safe: re-applies on every DOM mutation via MutationObserver, and removes
 * its injected style when navigating away from the auth pages so the rest of
 * the app is untouched.
 */
(function () {
  var STYLE_ID = 'xbrain-login-style';
  var CSS =
    'form:has(input[name="email"]){display:none!important}' +
    'img[src*="logo.svg"]{display:none!important}';

  function onAuthPage() {
    var p = location.pathname;
    return p === '/login' || p === '/register' || p === '/';
  }

  function apply() {
    var existing = document.getElementById(STYLE_ID);
    if (!onAuthPage()) {
      if (existing) existing.remove();
      return;
    }
    if (!existing && document.head) {
      var s = document.createElement('style');
      s.id = STYLE_ID;
      s.textContent = CSS;
      document.head.appendChild(s);
    }
    // Text-based work CSS cannot do. Also handles the login heading text
    // client-side so it is CDN-cache-proof (the hashed locale bundle that holds
    // "Welcome back" is cached by Cloudflare by URL; editing it in the image is
    // not enough until the edge cache expires — so we override the DOM here).
    var all = document.querySelectorAll('h1, h2, h3, a, p, span, div');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var leaf = el.children.length === 0;
      var txt = (el.textContent || '').trim();
      if (leaf && txt === 'Welcome back') {
        el.textContent = 'Login Groove OS';
      } else if (leaf && txt === 'OR') {
        // Hide the whole "—— OR ——" divider: the OR text's immediate parent IS
        // the flex row that also holds the two border lines.
        (el.parentElement || el).style.display = 'none';
      } else if (/^Don't have an account\?/i.test(txt) && el.querySelector('a')) {
        el.style.display = 'none';
      }
    }
  }

  function start() {
    apply();
    try {
      new MutationObserver(apply).observe(document.documentElement, {
        childList: true,
        subtree: true,
      });
    } catch (e) {}
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();

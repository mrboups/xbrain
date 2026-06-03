/**
 * xbrain-login.js — Groove OS login-page rebrand.
 *
 * On the auth pages only (/login, /register, /), it:
 *   - sets the heading to "Groove OS" and adds a "Join Team or Sign Up" subtitle,
 *   - hides the email/password login form (keeps social / GitHub + Google),
 *   - removes the "—— OR ——" divider,
 *   - hides the "Don't have an account? Sign up" prompt,
 *   - hides the LibreChat logo image.
 *
 * Done client-side so it is CDN-cache-proof (the hashed locale bundle holding
 * "Welcome back" is cached by Cloudflare; overriding the DOM here wins).
 * SPA-safe: re-applies on every DOM mutation and cleans up off the auth pages.
 */
(function () {
  var STYLE_ID = 'xbrain-login-style';
  var SUB_ID = 'xbrain-login-subtitle';
  var TITLE = 'Groove OS';
  var SUBTITLE = 'Join Team or Sign Up';
  var CSS =
    'form:has(input[name="email"]){display:none!important}' +
    'img[src*="logo.svg"]{display:none!important}';

  function onAuthPage() {
    var p = location.pathname;
    return p === '/login' || p === '/register' || p === '/';
  }

  function cleanup() {
    var st = document.getElementById(STYLE_ID);
    if (st) st.remove();
    var sub = document.getElementById(SUB_ID);
    if (sub) sub.remove();
  }

  function apply() {
    if (!onAuthPage()) {
      cleanup();
      return;
    }
    if (!document.getElementById(STYLE_ID) && document.head) {
      var s = document.createElement('style');
      s.id = STYLE_ID;
      s.textContent = CSS;
      document.head.appendChild(s);
    }

    var all = document.querySelectorAll('h1, h2, h3, a, p, span, div');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var leaf = el.children.length === 0;
      var txt = (el.textContent || '').trim();

      if (leaf && (txt === 'Welcome back' || txt === 'Login Groove OS')) {
        // Rename the heading + add a subtitle right after it (once).
        if (el.textContent !== TITLE) el.textContent = TITLE;
        if (!document.getElementById(SUB_ID)) {
          var sub = document.createElement('div');
          sub.id = SUB_ID;
          sub.textContent = SUBTITLE;
          sub.style.cssText =
            'text-align:center;margin-top:6px;font-size:15px;opacity:.7;font-weight:500;';
          el.insertAdjacentElement('afterend', sub);
        }
      } else if (leaf && txt === 'OR') {
        // Climb to the topmost ancestor whose full text is still exactly "OR"
        // (its other children are the two decorative lines, which carry no
        // text) — that IS the divider row. Hide it, lines included.
        var row = el;
        while (
          row.parentElement &&
          (row.parentElement.textContent || '').trim() === 'OR'
        ) {
          row = row.parentElement;
        }
        row.style.display = 'none';
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

/**
 * xbrain-routes.js — mounted on LibreChat's Express app at image build time.
 *
 * Routes:
 *   GET /api/xbrain/token          — short-lived bridge JWT for the onboarding modal.
 *   GET /api/xbrain/media/:itemId  — same-origin proxy that streams brain media bytes
 *                                    from memory-api.  Sidesteps LibreChat's Helmet CSP
 *                                    (same origin) and the 1-hour media-token expiry
 *                                    (proxy mints a fresh bridge JWT per request; the
 *                                    markdown URL stored in the system addendum is stable).
 *
 * Security: all routes require requireJwtAuth (LibreChat's session middleware).
 * Bridge JWTs are signed with BRIDGE_SHARED_SECRET (HS256).
 */

const jwt = require('jsonwebtoken');

// node-fetch is available in the LibreChat image (Node 18+ has global fetch).
// Use global fetch where available; fall back to require('node-fetch') for
// older Node builds.
const _fetch = typeof fetch !== 'undefined' ? fetch : (() => {
  try { return require('node-fetch'); } catch { return null; }
})();

module.exports = function mountXbrainRoutes(app) {
  const secret = process.env.BRIDGE_SHARED_SECRET;
  const memoryApiUrl = (process.env.MEMORY_API_URL || 'http://memory-api:8000').replace(/\/$/, '');

  if (!secret) {
    console.warn('[xbrain] BRIDGE_SHARED_SECRET not set — /api/xbrain/* routes will return 503');
  }
  if (!_fetch) {
    console.warn('[xbrain] fetch unavailable — /api/xbrain/media will return 503');
  }

  let requireJwtAuth;
  try {
    requireJwtAuth = require('~/middleware/requireJwtAuth');
  } catch {
    try {
      requireJwtAuth = require('./middleware/requireJwtAuth');
    } catch {
      console.warn('[xbrain] requireJwtAuth not found — /api/xbrain/* routes unprotected, returning 503');
      requireJwtAuth = (_req, _res, next) => next();
    }
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  /**
   * Mint a short-lived bridge JWT (HS256) that memory-api accepts as a
   * service principal (kind='bridge').  Mirrors librechat-bridge/bridge_token.py.
   *
   * @param {string} sub       - OIDC sub of the acting user
   * @param {string} teamScope - team slug to embed in the JWT
   * @returns {string} signed JWT
   */
  function makeBridgeJwt(sub, teamScope) {
    const now = Math.floor(Date.now() / 1000);
    return jwt.sign(
      {
        iss: 'librechat-bridge',
        sub: String(sub),
        team_scope: teamScope,
        scope: 'bridge',
        iat: now,
        exp: now + 300,
      },
      secret,
      { algorithm: 'HS256' },
    );
  }

  /**
   * Resolve the primary team_scope for a user sub via memory-api's internal
   * endpoint GET /v1/internal/resolve-team-scope.
   *
   * Mirrors memory_api_client.py::resolve_team_scope().  Uses a placeholder
   * team_scope ('default') in the bridge JWT because the team is unknown at
   * call time — memory-api accepts any valid bridge JWT for this endpoint
   * (internal.py:26-44, no X-Team-Scope required).
   *
   * @param {string} sub  - source_user_id or email of the LibreChat user
   * @returns {Promise<string|null>}
   */
  async function resolveTeamScope(sub) {
    const token = makeBridgeJwt(sub, 'default');
    const url = `${memoryApiUrl}/v1/internal/resolve-team-scope?sub=${encodeURIComponent(sub)}`;
    try {
      const r = await _fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) return null;
      const body = await r.json();
      return body.team_scope || null;
    } catch (err) {
      console.warn('[xbrain] resolve-team-scope error:', err?.message);
      return null;
    }
  }

  app.get('/api/xbrain/github-orgs', requireJwtAuth, async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    try {
      const mongoose = require('mongoose');
      const userId = req.user._id || req.user.id;
      const user = await mongoose.connection.collection('users').findOne({ _id: userId });
      const ghToken = user?.github_access_token;
      if (!ghToken) return res.json([]);
      const r = await fetch('https://api.github.com/user/orgs?per_page=100', {
        headers: {
          Authorization: `Bearer ${ghToken}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': 'xbrain-librechat',
        },
      });
      if (!r.ok) return res.json([]);
      const orgs = await r.json();
      res.json(Array.isArray(orgs) ? orgs.map(o => ({ login: o.login, description: o.description || null })) : []);
    } catch (err) {
      console.warn('[xbrain] github-orgs error:', err?.message);
      res.json([]);
    }
  });

  // Phase 8 D7 — ROADMAP success criterion 7: dashboard GitHub dynamique
  // Returns the user's repos including private + org repos. Requires GitHub OAuth scope `repo`.
  app.get('/api/xbrain/github-repos', requireJwtAuth, async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    try {
      const mongoose = require('mongoose');
      const userId = req.user._id || req.user.id;
      const user = await mongoose.connection.collection('users').findOne({ _id: userId });
      const ghToken = user?.github_access_token;
      if (!ghToken) {
        return res.json({ repos: [], linked: false });
      }
      const url = 'https://api.github.com/user/repos?per_page=100&sort=updated&visibility=all&affiliation=owner,collaborator,organization_member';
      const r = await fetch(url, {
        headers: {
          Authorization: `Bearer ${ghToken}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': 'xbrain-librechat',
        },
      });
      if (!r.ok) {
        console.warn('[xbrain] github-repos upstream error:', r.status);
        return res.status(502).json({ repos: [], linked: true, error: 'github_api_error', status: r.status });
      }
      const repos = await r.json();
      const mapped = Array.isArray(repos)
        ? repos.map(repo => ({
            id: repo.id,
            name: repo.name,
            full_name: repo.full_name,
            owner_login: repo.owner?.login || null,
            private: !!repo.private,
            html_url: repo.html_url,
            description: repo.description || null,
            updated_at: repo.updated_at,
            language: repo.language || null,
            archived: !!repo.archived,
          }))
        : [];
      res.json({ repos: mapped, linked: true });
    } catch (err) {
      console.warn('[xbrain] github-repos error:', err?.message);
      res.status(500).json({ repos: [], linked: false, error: 'internal' });
    }
  });

  app.get('/api/xbrain/token', requireJwtAuth, (req, res) => {
    if (!secret) return res.status(503).json({ error: 'BRIDGE_SHARED_SECRET not configured' });

    const user = req.user;
    if (!user || !user.email) {
      return res.status(401).json({ error: 'no authenticated user' });
    }

    const now = Math.floor(Date.now() / 1000);
    const githubId = user.githubId || user.github_id || null;
    const token = jwt.sign(
      {
        iss: 'librechat-onboarding',
        sub: String(user._id || user.id),
        email: user.email,
        ...(githubId ? { github_id: String(githubId) } : {}),
        scope: 'bridge',
        iat: now,
        exp: now + 300,
      },
      secret,
      { algorithm: 'HS256' },
    );

    res.json({ token });
  });

  // ---------------------------------------------------------------------------
  // GET /api/xbrain/media/:itemId
  //
  // Same-origin proxy that streams brain media bytes from memory-api.
  //
  // Auth flow (BL-003 slice 5):
  //   1. requireJwtAuth ensures the browser caller is a signed-in LibreChat user.
  //   2. We resolve the user's team_scope via memory-api's internal endpoint
  //      GET /v1/internal/resolve-team-scope (bridge JWT, no X-Team-Scope needed).
  //   3. We call GET /v1/media/{itemId}/raw with a bridge JWT where
  //      team_scope = resolved scope AND X-Team-Scope: <scope>.
  //      memory-api/deps.py:349-351 (get_team_scope for kind='bridge') validates
  //      that the JWT's team_scope claim == the X-Team-Scope header value, then
  //      returns the scope — so the bridge JWT is accepted cleanly for /raw.
  //   4. The response bytes are piped through; Content-Type + Content-Disposition
  //      are forwarded so browsers render images inline.
  //
  // On any failure we return 404 to avoid leaking internals to the browser.
  // ---------------------------------------------------------------------------

  // UUID v4 pattern — reject obviously invalid itemIds early.
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  app.get('/api/xbrain/media/:itemId', requireJwtAuth, async (req, res) => {
    if (!secret || !_fetch) {
      return res.status(503).end();
    }

    const { itemId } = req.params;
    if (!UUID_RE.test(itemId)) {
      return res.status(404).end();
    }

    const user = req.user;
    if (!user || !user.email) {
      return res.status(401).end();
    }

    try {
      // Step 1 — resolve the user's team scope.
      // Use email as the sub so memory-api can look up by email when
      // source_user_id uses a different format (e.g. GitHub-primary subs).
      const sub = user.email;
      const teamScope = await resolveTeamScope(sub);
      if (!teamScope) {
        console.warn(`[xbrain] media proxy: no team_scope resolved for ${sub}`);
        return res.status(404).end();
      }

      // Step 2 — fetch bytes from memory-api /raw with a scoped bridge JWT.
      const bridgeToken = makeBridgeJwt(sub, teamScope);
      const rawUrl = `${memoryApiUrl}/v1/media/${itemId}/raw`;
      const upstreamRes = await _fetch(rawUrl, {
        headers: {
          Authorization: `Bearer ${bridgeToken}`,
          'X-Team-Scope': teamScope,
        },
      });

      if (!upstreamRes.ok) {
        return res.status(404).end();
      }

      // Step 3 — pipe bytes to the browser with safe headers.
      const contentType = upstreamRes.headers.get('content-type') || 'application/octet-stream';
      const disposition = upstreamRes.headers.get('content-disposition') || 'inline';

      res.setHeader('Content-Type', contentType);
      res.setHeader('Content-Disposition', disposition);
      res.setHeader('Cache-Control', 'private, max-age=3600');
      res.setHeader('X-Content-Type-Options', 'nosniff');

      // Buffer the body — upstream bodies are capped at 25 MB (upload limit).
      const arrayBuffer = await upstreamRes.arrayBuffer();
      res.end(Buffer.from(arrayBuffer));
    } catch (err) {
      console.warn('[xbrain] media proxy error:', err?.message);
      return res.status(404).end();
    }
  });
};

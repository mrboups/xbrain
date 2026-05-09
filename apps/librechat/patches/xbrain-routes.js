/**
 * xbrain-routes.js — mounted on LibreChat's Express app at image build time.
 *
 * Exposes GET /api/xbrain/token — returns a short-lived bridge JWT for the
 * authenticated LibreChat user. Used by the onboarding modal to call memory-api.
 *
 * Security: requires requireJwtAuth (LibreChat's session middleware).
 * Token is signed with BRIDGE_SHARED_SECRET, carries iss=librechat-onboarding.
 */

const jwt = require('jsonwebtoken');

module.exports = function mountXbrainRoutes(app) {
  const secret = process.env.BRIDGE_SHARED_SECRET;
  if (!secret) {
    console.warn('[xbrain] BRIDGE_SHARED_SECRET not set — /api/xbrain/token will return 503');
  }

  let requireJwtAuth;
  try {
    requireJwtAuth = require('~/middleware/requireJwtAuth');
  } catch {
    try {
      requireJwtAuth = require('./middleware/requireJwtAuth');
    } catch {
      console.warn('[xbrain] requireJwtAuth not found — /api/xbrain/token unprotected, returning 503');
      requireJwtAuth = (_req, _res, next) => next();
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
};

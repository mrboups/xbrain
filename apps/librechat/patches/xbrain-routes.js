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

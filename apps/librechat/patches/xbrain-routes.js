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

  app.get('/api/xbrain/token', requireJwtAuth, (req, res) => {
    if (!secret) return res.status(503).json({ error: 'BRIDGE_SHARED_SECRET not configured' });

    const user = req.user;
    if (!user || !user.email) {
      return res.status(401).json({ error: 'no authenticated user' });
    }

    const now = Math.floor(Date.now() / 1000);
    const token = jwt.sign(
      {
        iss: 'librechat-onboarding',
        sub: String(user._id || user.id),
        email: user.email,
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

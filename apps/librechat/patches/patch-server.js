/**
 * patch-server.js — run at Docker image build time to inject xbrain routes.
 *
 * Appends `require('./xbrain-routes')(app)` to LibreChat's server entry point.
 * Entry point: /app/api/server/index.js in LibreChat v0.8.x
 */

const fs = require('fs');

const serverIndexPath = '/app/api/server/index.js';

if (!fs.existsSync(serverIndexPath)) {
  console.error(`[patch-server] ${serverIndexPath} not found`);
  process.exit(1);
}

const content = fs.readFileSync(serverIndexPath, 'utf8');

if (content.includes('xbrain-routes')) {
  console.log('[patch-server] already patched — skipping');
  process.exit(0);
}

// LibreChat v0.8.x exports the Express app as module.exports
const patch = `\n// xbrain onboarding routes (injected by patch-server.js)\ntry {\n  const xbrainRoutes = require('./xbrain-routes');\n  xbrainRoutes(module.exports);\n  console.log('[xbrain] routes mounted on /api/xbrain/token');\n} catch (e) {\n  console.warn('[xbrain] route mount failed:', e.message);\n}\n`;

fs.writeFileSync(serverIndexPath, content + patch);
console.log(`[patch-server] patched ${serverIndexPath}`);

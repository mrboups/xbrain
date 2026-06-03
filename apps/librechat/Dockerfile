# apps/librechat/Dockerfile
# Custom LibreChat image — bakes xbrain patches into v0.8.5.
#
# Patches:
#   1a. api/strategies/socialLogin.js   — OAuth cross-provider account linking
#   1b. api/strategies/githubStrategy.js — adds read:org scope to GitHub OAuth
#   2.  api/server/routes/xbrain-routes.js — /api/xbrain/token endpoint
#   3.  api/server/index.js             — patched via patch-server.js at build time
#   4.  client/dist/index.html          — <script defer> tag injected
#   5.  client/dist/onboarding.js       — 4-step team onboarding modal
#
# Paths verified against running container on VM __VM_HOST__ (2026-05-07):
#   - /app/client/dist/index.html
#   - /app/api/server/index.js

FROM ghcr.io/danny-avila/librechat:v0.8.5

# Patch 1a: OAuth cross-provider account linking
COPY patches/socialLogin.js /app/api/strategies/socialLogin.js

# Patch 1b: Add read:org scope to GitHub OAuth (required for /api/xbrain/github-orgs)
# oauth.js hardcodes scope in passport.authenticate() calls, overriding githubStrategy.js
COPY patches/githubStrategy.js /app/api/strategies/githubStrategy.js
RUN sed -i "s/scope: \['user:email', 'read:user'\]/scope: ['user:email', 'read:user', 'read:org']/g" \
    /app/api/server/routes/oauth.js

# Patch 2: xbrain token endpoint
# Note: placed directly in /app/api/server/ so patch-server.js can require('./xbrain-routes')
COPY patches/xbrain-routes.js /app/api/server/xbrain-routes.js

# Patch 3: Register xbrain routes in server index
COPY patches/patch-server.js /tmp/patch-server.js
RUN node /tmp/patch-server.js

# Patch 4 + 5: Onboarding modal
COPY patches/onboarding.js /app/client/dist/onboarding.js
RUN sed -i 's|</head>|<script defer src="/onboarding.js?v=9"></script></head>|' \
    /app/client/dist/index.html

# Patch 6: Rebrand login heading "Welcome back" -> "Login Groove OS".
# The string lives in the hashed locale bundle (client/dist/assets/locales*.js)
# as the value of the com_auth_welcome_back i18n key. Anchor on the key so only
# the login heading changes.
RUN sed -i 's/com_auth_welcome_back:"Welcome back"/com_auth_welcome_back:"Login Groove OS"/g' \
    /app/client/dist/assets/locales*.js

# Verify all patches applied
RUN grep -q "xbrain-routes" /app/api/server/index.js && \
    grep -q "onboarding.js?v=9" /app/client/dist/index.html && \
    grep -rq "Login Groove OS" /app/client/dist/assets/ && \
    echo "All patches verified OK"

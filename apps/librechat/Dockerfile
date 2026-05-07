# apps/librechat/Dockerfile
# Custom LibreChat image — bakes xbrain patches into v0.8.5.
#
# Patches:
#   1. api/strategies/socialLogin.js   — OAuth cross-provider account linking
#   2. api/server/routes/xbrain-routes.js — /api/xbrain/token endpoint
#   3. api/server/index.js             — patched via patch-server.js at build time
#   4. client/dist/index.html          — <script defer> tag injected
#   5. client/dist/onboarding.js       — 4-step team onboarding modal
#
# Paths verified against running container on VM __VM_HOST__ (2026-05-07):
#   - /app/client/dist/index.html
#   - /app/api/server/index.js

FROM ghcr.io/danny-avila/librechat:v0.8.5

# Patch 1: OAuth cross-provider account linking
COPY patches/socialLogin.js /app/api/strategies/socialLogin.js

# Patch 2: xbrain token endpoint
# Note: placed directly in /app/api/server/ so patch-server.js can require('./xbrain-routes')
COPY patches/xbrain-routes.js /app/api/server/xbrain-routes.js

# Patch 3: Register xbrain routes in server index
COPY patches/patch-server.js /tmp/patch-server.js
RUN node /tmp/patch-server.js

# Patch 4 + 5: Onboarding modal
COPY patches/onboarding.js /app/client/dist/onboarding.js
RUN sed -i 's|</head>|<script defer src="/onboarding.js?v=3"></script></head>|' \
    /app/client/dist/index.html

# Verify all patches applied
RUN grep -q "xbrain-routes" /app/api/server/index.js && \
    grep -q "onboarding.js?v=3" /app/client/dist/index.html && \
    echo "All patches verified OK"

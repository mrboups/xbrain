# xbrain-hocuspocus — the collaborative-board Yjs server (Hocuspocus 4.4.0).
#
# Multi-stage, node:22-alpine throughout (Hocuspocus engines: node >=22). The LAST stage
# is `runtime`, so a plain `docker build` and the compose `build:` produce the production
# image with NO devDependencies. The `gate` stage keeps devDeps (the @hocuspocus/provider
# CLIENT) for plan 26-07's two-client convergence proof — build it with `--target gate`.
#
# ARCH: dev hosts here are arm64, prod is amd64. This image is built by CI on amd64
# (26-06 wires the compose `build:` bake). NEVER `docker build` on the arm64 dev host and
# ship the artifact — it fails with an exec-format error on the amd64 VM. Building locally
# only to PROVE this Dockerfile works is fine and is what 26-07 does.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- gate: devDeps INCLUDED — the 26-07 gate's client image (two-client convergence) ---
FROM deps AS gate
WORKDIR /app
COPY src ./src
COPY tests ./tests
CMD ["node", "tests/run_tests.mjs"]

# --- runtime: LAST stage = the default build target; devDep-free, non-root ---
FROM node:22-alpine AS runtime
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
ENV NODE_ENV=production
USER node
EXPOSE 8108
CMD ["node", "src/server.mjs"]

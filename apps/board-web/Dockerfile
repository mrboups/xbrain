# apps/board-web/Dockerfile — the board SPA image, built in Docker only.
#
# (a) Node is a BUILD-TIME dependency ONLY. The `build` stage runs `npm ci` +
#     `vite build`; the `runtime` stage is nginx serving static files and
#     contains NO Node and NO node_modules (D-26-01 — no Node runtime is added to
#     the API path).
# (b) This image is built by CI on amd64 via the compose `build:` key (26-06). It
#     is NEVER built on the arm64 dev host and shipped — an arm64 artifact on the
#     amd64 VM fails with `exec format error` (26-RESEARCH Pitfall 5). A local
#     `docker build` here is a PROOF that the Dockerfile is correct, nothing more.
# (c) This one container serves the SPA in BOTH editions. There is no Firebase
#     dependency and no OSS/SaaS divergence (26-RESEARCH open question 5).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8107
# nginx:alpine's default CMD (nginx -g 'daemon off;') is correct; do not override it.

# syntax=docker/dockerfile:1.7
# Imagen del frontend: export estático de Next.js (out/) servido con nginx sin root.
# Contexto de build: apps/web.
#   docker build -t trading-maxxing-web apps/web
#   docker run --rm -p 3000:3000 trading-maxxing-web
# En la entrega final el mismo out/ se publica en Cloudflare Pages (#45,
# .github/workflows/deploy-web.yml); esta imagen es para correrlo en contenedor.

ARG NODE_IMAGE=node:24-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.28-alpine

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

FROM ${NODE_IMAGE} AS build
WORKDIR /app
RUN corepack enable
ENV NEXT_TELEMETRY_DISABLED=1
# Variables públicas: Next.js las incrusta en el bundle durante el build, así que cambiar
# de entorno implica reconstruir la imagen.
#   NEXT_PUBLIC_ENV         ambiente del badge del encabezado (local | dev | prod)
#   NEXT_PUBLIC_COMMIT_SHA  commit del build (se muestra corto en el badge)
#   NEXT_PUBLIC_API_BASE    origen de la API. En Pages va vacío (mismo origen, la Pages
#                           Function reenvía /api/*). En la imagen, nginx no tiene proxy,
#                           así que apúntalo al Worker local: http://localhost:8787
#                           (wrangler dev --env dev, AUTH_MODE=dev y CORS para :3000).
ARG NEXT_PUBLIC_ENV="local"
ARG NEXT_PUBLIC_COMMIT_SHA=""
ARG NEXT_PUBLIC_API_BASE="http://localhost:8787"
ENV NEXT_PUBLIC_ENV=$NEXT_PUBLIC_ENV \
    NEXT_PUBLIC_COMMIT_SHA=$NEXT_PUBLIC_COMMIT_SHA \
    NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build && pnpm check:export

FROM ${NGINX_IMAGE} AS runtime
# La imagen base ya corre como el usuario nginx (uid 101), sin root.
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/out /usr/share/nginx/html
USER 101
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/healthz >/dev/null || exit 1

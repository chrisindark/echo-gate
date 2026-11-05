# Echo Gate web UI — production image.
#
#   docker build -t echo-gate-web:local web/
#   docker compose --profile ui up -d   # from deploy/compose/
#
# NEXT_PUBLIC_API_BASE_URL is inlined at build time (Next.js bakes it into the
# client bundle), so changing the gateway URL means rebuilding. It defaults to
# http://localhost:8000, the value the browser uses when the gateway is
# published on the host.
#
# syntax=docker/dockerfile:1

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# next start needs the production server + built output; run as non-root.
COPY --from=deps /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY package.json ./
# `next` reads PORT (default 3000). Bind to 0.0.0.0 inside the container so the
# compose port mapping can reach it.
ENV HOSTNAME=0.0.0.0 PORT=3000
EXPOSE 3000
USER node
CMD ["npm", "run", "start"]

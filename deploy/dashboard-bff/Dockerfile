FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
WORKDIR /app
COPY deploy/dashboard-bff/package*.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund --fetch-timeout=20000 --fetch-retries=1
COPY openapi.json ./
COPY server/live ./server/live
ENV NODE_ENV=production DASHBOARD_HOST=0.0.0.0 DASHBOARD_PORT=5281
EXPOSE 5281
CMD ["node", "server/live/server.ts"]

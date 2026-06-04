# Stage 1 — build the static site. The api-ref / test-matrix / simulations
# scripts run inside this stage; if any HTTP fetch fails the script falls
# back to scripts/snapshots/ so the build stays deterministic offline.
FROM node:20-alpine AS build
WORKDIR /app

# Install deps first so the layer caches across content-only changes.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copy the rest and build. Build args expose service URLs to the auto-gen
# scripts. Compose passes them in from the same network so DNS resolution
# inside the container reaches each parser at its service name.
ARG GATEWAY_URL=http://gateway:8000
ARG TABLEAU_URL=http://tableau-parser:8000
ARG TWS_URL=http://tws-parser:8000
ARG QLIKVIEW_URL=http://qlikview-parser:8000
ARG SPARK_URL=http://spark-parser:8000
ENV GATEWAY_URL=$GATEWAY_URL \
    TABLEAU_URL=$TABLEAU_URL \
    TWS_URL=$TWS_URL \
    QLIKVIEW_URL=$QLIKVIEW_URL \
    SPARK_URL=$SPARK_URL \
    NODE_ENV=production

COPY . .
RUN npm run build

# Stage 2 — nginx serves the static bundle. Smaller image, sub-second start.
FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
# Hit 127.0.0.1 explicitly — `localhost` resolves to [::1] inside the
# alpine container and nginx is IPv4-only by default.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1
CMD ["nginx", "-g", "daemon off;"]

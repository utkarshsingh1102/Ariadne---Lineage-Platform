#!/usr/bin/env bash
#
# start.sh — bring up the full Lineage Platform stack.
#
#   docker compose: neo4j, postgres, tableau / tws / qlikview / spark parsers
#   host processes: gateway (uvicorn) on :8000, frontend (next dev) on :3000
#
# Idempotent — running it twice in a row is safe.
# Stop everything with ./stop.sh.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="${REPO_ROOT}/lineage-platform"
GATEWAY_DIR="${PLATFORM_DIR}/apps/gateway"
FRONTEND_DIR="${PLATFORM_DIR}/apps/frontend"
RUN_DIR="${PLATFORM_DIR}/.run"
VENV_DIR="${PLATFORM_DIR}/.venv"
VENV_PY="${VENV_DIR}/bin/python"

GATEWAY_PORT=8000
FRONTEND_PORT=3000
NEO4J_BROWSER_PORT=7475
NEO4J_BOLT_PORT=7688
POSTGRES_PORT=5432
TABLEAU_PORT=8001
TWS_PORT=8002
QLIKVIEW_PORT=8003
SPARK_PORT=8004

GATEWAY_PID_FILE="${RUN_DIR}/gateway.pid"
FRONTEND_PID_FILE="${RUN_DIR}/frontend.pid"
GATEWAY_LOG="${RUN_DIR}/gateway.log"
FRONTEND_LOG="${RUN_DIR}/frontend.log"

# Colors (only if stdout is a TTY)
if [[ -t 1 ]]; then
  G=$'\033[0;32m'; Y=$'\033[1;33m'; R=$'\033[0;31m'; C=$'\033[0;36m'; D=$'\033[2m'; N=$'\033[0m'
else
  G=""; Y=""; R=""; C=""; D=""; N=""
fi

log()  { printf "%s[start]%s %s\n" "$C" "$N" "$1"; }
ok()   { printf "%s[ ok ]%s %s\n" "$G" "$N" "$1"; }
warn() { printf "%s[warn]%s %s\n" "$Y" "$N" "$1"; }
err()  { printf "%s[fail]%s %s\n" "$R" "$N" "$1" >&2; }

mkdir -p "${RUN_DIR}"

# ---------------------------------------------------------------------------
# Prereqs
# ---------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { err "$1 is not installed or not on PATH"; exit 1; }; }
need docker
need python3
need npm
need curl
need lsof

if ! docker info >/dev/null 2>&1; then
  err "Docker daemon is not running. Start Docker Desktop and re-run ./start.sh."
  exit 1
fi
ok "Docker daemon is running."

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
wait_for_health() {
  # Poll `docker inspect <container> .State.Health.Status` until it's "healthy".
  local container="$1" tries=60
  for ((i=1; i<=tries; i++)); do
    local s
    s=$(docker inspect "$container" --format='{{.State.Health.Status}}' 2>/dev/null || echo "missing")
    if [[ "$s" == "healthy" ]]; then return 0; fi
    sleep 2
  done
  err "$container never became healthy (last status: $s)"
  return 1
}

wait_for_http() {
  local url="$1" tries=40
  for ((i=1; i<=tries; i++)); do
    if curl -sf "$url" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

port_in_use() { lsof -ti:"$1" >/dev/null 2>&1; }

kill_pid_file() {
  local f="$1"
  if [[ -f "$f" ]]; then
    local pid; pid=$(cat "$f" 2>/dev/null || true)
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}

# ---------------------------------------------------------------------------
# 1. Infra (Neo4j + Postgres)
# ---------------------------------------------------------------------------
log "Starting Neo4j + Postgres..."
( cd "$PLATFORM_DIR" && docker compose up -d neo4j postgres >/dev/null )

log "Waiting for Neo4j (this can take up to ~30s on first boot)..."
wait_for_health lineage-neo4j
ok "Neo4j healthy"

log "Waiting for Postgres..."
wait_for_health lineage-postgres
ok "Postgres healthy"

log "Applying shared Cypher constraints..."
( cd "$PLATFORM_DIR" && docker compose run --rm neo4j-init >/dev/null 2>&1 ) || warn "neo4j-init reported a non-zero exit — constraints may already be applied."
ok "Constraints applied"

# ---------------------------------------------------------------------------
# 2. Parsers
# ---------------------------------------------------------------------------
log "Starting parsers (Tableau, TWS, QlikView, Spark)..."
# --build ensures local code changes in any parser source tree are
# picked up. Docker's build cache makes the no-change path fast
# (~2s); the only time it's slow is when the source actually changed,
# which is exactly when you want the rebuild to happen.
( cd "$PLATFORM_DIR" && docker compose up -d --build tableau-parser tws-parser qlikview-parser spark-parser >/dev/null )

for c in lineage-tableau-parser lineage-tws-parser lineage-qlikview-parser lineage-spark-parser; do
  log "  Waiting on $c..."
  wait_for_health "$c"
  ok "  $c healthy"
done

# ---------------------------------------------------------------------------
# 3. Gateway deps + start
# ---------------------------------------------------------------------------
kill_pid_file "$GATEWAY_PID_FILE"
if port_in_use "$GATEWAY_PORT"; then
  warn "Port $GATEWAY_PORT is still busy. Killing the listener so the gateway can bind..."
  lsof -ti:"$GATEWAY_PORT" | xargs -r kill 2>/dev/null || true
  sleep 1
fi

if [[ ! -x "${VENV_PY}" ]]; then
  log "Creating Python virtualenv at ${VENV_DIR} (first run only)..."
  python3 -m venv "${VENV_DIR}"
  "${VENV_PY}" -m pip install --quiet --upgrade pip
  ok "Virtualenv created"
fi

if ! "${VENV_PY}" -c "import lineage_gateway, fastapi, neo4j, asyncpg, httpx, multipart" 2>/dev/null; then
  log "Installing gateway dependencies into the venv (first run only)..."
  ( cd "$GATEWAY_DIR" && "${VENV_PY}" -m pip install --quiet -e ".[dev]" )
  ok "Gateway dependencies installed"
fi

log "Starting gateway on :$GATEWAY_PORT..."
(
  cd "$GATEWAY_DIR"
  NEO4J_URI="bolt://localhost:${NEO4J_BOLT_PORT}" \
  NEO4J_USER="neo4j" \
  NEO4J_PASSWORD="lineagepass" \
  POSTGRES_HOST="localhost" \
  POSTGRES_PORT="${POSTGRES_PORT}" \
  POSTGRES_USER="lineage" \
  POSTGRES_PASSWORD="lineagepass" \
  POSTGRES_DB="lineage" \
  CORS_ALLOWED_ORIGINS="http://localhost:${FRONTEND_PORT}" \
  PARSER_TABLEAU_URL="http://localhost:${TABLEAU_PORT}" \
  PARSER_TWS_URL="http://localhost:${TWS_PORT}" \
  PARSER_QLIKVIEW_URL="http://localhost:${QLIKVIEW_PORT}" \
  PARSER_SPARK_URL="http://localhost:${SPARK_PORT}" \
  nohup "${VENV_PY}" -m uvicorn lineage_gateway.main:app \
    --host 127.0.0.1 --port "${GATEWAY_PORT}" --log-level info \
    >"${GATEWAY_LOG}" 2>&1 &
  echo $! > "${GATEWAY_PID_FILE}"
) >/dev/null 2>&1

if wait_for_http "http://localhost:${GATEWAY_PORT}/health"; then
  ok "Gateway responding on :${GATEWAY_PORT}"
else
  err "Gateway did not respond on :${GATEWAY_PORT}. Check ${GATEWAY_LOG}."
  exit 1
fi

# ---------------------------------------------------------------------------
# 4. Frontend deps + start
# ---------------------------------------------------------------------------
kill_pid_file "$FRONTEND_PID_FILE"
if port_in_use "$FRONTEND_PORT"; then
  warn "Port $FRONTEND_PORT is still busy. Killing the listener so the frontend can bind..."
  lsof -ti:"$FRONTEND_PORT" | xargs -r kill 2>/dev/null || true
  sleep 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  log "Installing frontend dependencies (first run only — this takes a couple of minutes)..."
  ( cd "$FRONTEND_DIR" && npm install --no-audit --no-fund --silent )
  ok "Frontend dependencies installed"
fi

log "Starting frontend on :$FRONTEND_PORT..."
(
  cd "$FRONTEND_DIR"
  NEXT_PUBLIC_GATEWAY_URL="http://localhost:${GATEWAY_PORT}" \
  nohup npx next dev -p "${FRONTEND_PORT}" -H 127.0.0.1 \
    >"${FRONTEND_LOG}" 2>&1 &
  echo $! > "${FRONTEND_PID_FILE}"
) >/dev/null 2>&1

if wait_for_http "http://localhost:${FRONTEND_PORT}/"; then
  ok "Frontend responding on :${FRONTEND_PORT}"
else
  err "Frontend did not respond on :${FRONTEND_PORT}. Check ${FRONTEND_LOG}."
  exit 1
fi

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
cat <<EOF

${G}=====================================================================${N}
${G} Lineage Platform is up${N}
${G}=====================================================================${N}

  ${C}Frontend${N}        http://localhost:${FRONTEND_PORT}
  ${C}Gateway${N}         http://localhost:${GATEWAY_PORT}
  ${C}Gateway docs${N}    http://localhost:${GATEWAY_PORT}/docs       ${D}(OpenAPI / Swagger UI)${N}

  ${C}Parsers${N}
    Tableau         http://localhost:${TABLEAU_PORT}/health
    TWS             http://localhost:${TWS_PORT}/health
    QlikView        http://localhost:${QLIKVIEW_PORT}/health
    Spark           http://localhost:${SPARK_PORT}/health

  ${C}Data stores${N}
    Neo4j Browser   http://localhost:${NEO4J_BROWSER_PORT}            ${D}(login: neo4j / lineagepass)${N}
    Neo4j Bolt      bolt://localhost:${NEO4J_BOLT_PORT}
    Postgres        postgresql://lineage:lineagepass@localhost:${POSTGRES_PORT}/lineage

  ${C}Logs${N}
    Gateway         ${GATEWAY_LOG}
    Frontend        ${FRONTEND_LOG}
    Parser logs     docker compose logs -f <service>

  ${C}Stop everything${N}    ./stop.sh

${G}=====================================================================${N}
EOF

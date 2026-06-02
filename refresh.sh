#!/usr/bin/env bash
#
# refresh.sh — reload all parser containers + host gateway + frontend
# with the latest source code. NO data is touched.
#
#   - Rebuilds the four parser images (uses Docker's build cache; only
#     the services whose source actually changed take real time).
#   - Force-recreates the parser containers so the new image is live.
#   - Leaves the neo4j + postgres containers running and untouched —
#     volumes (and therefore the graph + Postgres rows) are preserved.
#   - Leaves the uploads/ directory untouched.
#   - Restarts the host-side gateway (uvicorn) + frontend (next dev)
#     processes by reusing start.sh's launcher path.
#
# Use this after editing parser source / gateway source / frontend
# source. It is NOT a database reset — see notes in start.sh / stop.sh
# for that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="${REPO_ROOT}/lineage-platform"
GATEWAY_DIR="${PLATFORM_DIR}/apps/gateway"
FRONTEND_DIR="${PLATFORM_DIR}/apps/frontend"
RUN_DIR="${PLATFORM_DIR}/.run"
VENV_PY="${PLATFORM_DIR}/.venv/bin/python"

GATEWAY_PORT=8000
FRONTEND_PORT=3000
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

PARSER_SERVICES=(tableau-parser tws-parser qlikview-parser spark-parser)

# Colors (only if stdout is a TTY)
if [[ -t 1 ]]; then
  G=$'\033[0;32m'; Y=$'\033[1;33m'; R=$'\033[0;31m'; C=$'\033[0;36m'; D=$'\033[2m'; N=$'\033[0m'
else
  G=""; Y=""; R=""; C=""; D=""; N=""
fi
log()  { printf "%s[refresh]%s %s\n" "$C" "$N" "$1"; }
ok()   { printf "%s[ ok ]%s %s\n" "$G" "$N" "$1"; }
warn() { printf "%s[warn]%s %s\n" "$Y" "$N" "$1"; }
err()  { printf "%s[fail]%s %s\n" "$R" "$N" "$1" >&2; }

mkdir -p "${RUN_DIR}"

# ---------------------------------------------------------------------------
# Pre-flight: confirm Docker is up and infra containers are running.
# We refuse to refresh into a half-broken stack.
# ---------------------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
  err "Docker daemon is not running. Start Docker Desktop and re-run ./refresh.sh."
  exit 1
fi

for c in lineage-neo4j lineage-postgres; do
  if ! docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null | grep -q true; then
    err "$c is not running. Refresh assumes the stack is up — run ./start.sh first."
    exit 1
  fi
done
ok "Docker + infra containers are up (Neo4j + Postgres preserved)."

# ---------------------------------------------------------------------------
# 1. Stop host-side gateway + frontend (containers stay; we recreate them
#    once the new parser images are built).
# ---------------------------------------------------------------------------
stop_pid() {
  local f="$1" name="$2"
  if [[ -f "$f" ]]; then
    local pid; pid=$(cat "$f" 2>/dev/null || true)
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      log "Stopping $name (pid $pid)..."
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}
stop_pid "$GATEWAY_PID_FILE" "gateway"
stop_pid "$FRONTEND_PID_FILE" "frontend"

# Belt-and-braces: kill any leftover process on those ports.
for p in "$GATEWAY_PORT" "$FRONTEND_PORT"; do
  if lsof -ti:"$p" >/dev/null 2>&1; then
    log "Freeing port $p..."
    lsof -ti:"$p" | xargs -r kill 2>/dev/null || true
    sleep 1
    lsof -ti:"$p" | xargs -r kill -9 2>/dev/null || true
  fi
done
ok "Host processes stopped."

# ---------------------------------------------------------------------------
# 2. Rebuild parser images. Docker's build cache makes the no-change
#    path quick (~2-5s per parser); only services whose source actually
#    changed pay the full build cost.
# ---------------------------------------------------------------------------
log "Rebuilding parser images (cache-friendly — unchanged services are near-instant)..."
( cd "$PLATFORM_DIR" && docker compose build "${PARSER_SERVICES[@]}" )
ok "Parser images rebuilt."

# ---------------------------------------------------------------------------
# 3. Force-recreate parser containers so the new image is live. The
#    --no-deps flag keeps Docker from cascading into neo4j/postgres.
# ---------------------------------------------------------------------------
log "Recreating parser containers..."
( cd "$PLATFORM_DIR" && docker compose up -d --force-recreate --no-deps "${PARSER_SERVICES[@]}" >/dev/null )

wait_health() {
  local name="$1" tries=60
  for ((i=1; i<=tries; i++)); do
    local s
    s=$(docker inspect "$name" --format='{{.State.Health.Status}}' 2>/dev/null || echo "missing")
    [[ "$s" == "healthy" ]] && return 0
    sleep 2
  done
  err "$name never became healthy (last status: $s)"
  return 1
}
for c in lineage-tableau-parser lineage-tws-parser lineage-qlikview-parser lineage-spark-parser; do
  log "  Waiting on $c..."
  wait_health "$c"
  ok "  $c healthy"
done

# ---------------------------------------------------------------------------
# 4. Reinstall gateway deps if pyproject changed, then launch gateway.
# ---------------------------------------------------------------------------
if [[ ! -x "${VENV_PY}" ]]; then
  err "Gateway venv missing at ${VENV_PY}. Run ./start.sh once to bootstrap it."
  exit 1
fi
if ! "${VENV_PY}" -c "import lineage_gateway" 2>/dev/null; then
  log "Reinstalling gateway package into the venv..."
  ( cd "$GATEWAY_DIR" && "${VENV_PY}" -m pip install --quiet -e ".[dev]" )
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

wait_http() {
  local url="$1" tries=40
  for ((i=1; i<=tries; i++)); do
    curl -sf "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
if wait_http "http://localhost:${GATEWAY_PORT}/health"; then
  ok "Gateway responding on :${GATEWAY_PORT}"
else
  err "Gateway did not respond. Check ${GATEWAY_LOG}."
  exit 1
fi

# ---------------------------------------------------------------------------
# 5. Frontend.
# ---------------------------------------------------------------------------
log "Starting frontend on :$FRONTEND_PORT..."
(
  cd "$FRONTEND_DIR"
  NEXT_PUBLIC_GATEWAY_URL="http://localhost:${GATEWAY_PORT}" \
  nohup npx next dev -p "${FRONTEND_PORT}" -H 127.0.0.1 \
    >"${FRONTEND_LOG}" 2>&1 &
  echo $! > "${FRONTEND_PID_FILE}"
) >/dev/null 2>&1

if wait_http "http://localhost:${FRONTEND_PORT}/"; then
  ok "Frontend responding on :${FRONTEND_PORT}"
else
  err "Frontend did not respond. Check ${FRONTEND_LOG}."
  exit 1
fi

cat <<EOF

${G}=====================================================================${N}
${G} Refresh complete${N}
${G}=====================================================================${N}

  ${C}Rebuilt + recreated${N}   ${PARSER_SERVICES[*]}
  ${C}Restarted${N}            gateway, frontend
  ${C}Preserved${N}            Neo4j data, Postgres data, uploads/

  ${C}Frontend${N}    http://localhost:${FRONTEND_PORT}
  ${C}Gateway${N}     http://localhost:${GATEWAY_PORT}/health

${G}=====================================================================${N}
EOF

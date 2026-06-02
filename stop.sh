#!/usr/bin/env bash
#
# stop.sh — tear down everything start.sh brought up.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="${REPO_ROOT}/lineage-platform"
RUN_DIR="${PLATFORM_DIR}/.run"
GATEWAY_PID_FILE="${RUN_DIR}/gateway.pid"
FRONTEND_PID_FILE="${RUN_DIR}/frontend.pid"

GATEWAY_PORT=8000
FRONTEND_PORT=3000

if [[ -t 1 ]]; then
  G=$'\033[0;32m'; Y=$'\033[1;33m'; C=$'\033[0;36m'; N=$'\033[0m'
else
  G=""; Y=""; C=""; N=""
fi
log() { printf "%s[stop]%s %s\n" "$C" "$N" "$1"; }
ok()  { printf "%s[ ok ]%s %s\n" "$G" "$N" "$1"; }

stop_pid_file() {
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

stop_pid_file "$GATEWAY_PID_FILE" "gateway"
stop_pid_file "$FRONTEND_PID_FILE" "frontend"

# Belt-and-braces: clean up anything else still bound to the dev ports.
for p in "$GATEWAY_PORT" "$FRONTEND_PORT"; do
  if lsof -ti:"$p" >/dev/null 2>&1; then
    log "Killing leftover process on port $p..."
    lsof -ti:"$p" | xargs -r kill 2>/dev/null || true
    sleep 1
    lsof -ti:"$p" | xargs -r kill -9 2>/dev/null || true
  fi
done

log "Stopping docker compose stack..."
( cd "$PLATFORM_DIR" && docker compose down ) >/dev/null 2>&1 || true
ok "All services stopped."

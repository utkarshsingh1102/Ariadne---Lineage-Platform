#!/usr/bin/env bash
set -euo pipefail

PARSER_URL="${PARSER_URL:-http://localhost:8002}"
FIXTURE="${FIXTURE:-/data/inputs/minimal_daily.txt}"

echo "==> /health"
curl -fsS "$PARSER_URL/health" | sed 's/^/  /'
echo

echo "==> /version"
curl -fsS "$PARSER_URL/version" | sed 's/^/  /'
echo

echo "==> /parse $FIXTURE"
curl -fsS -X POST "$PARSER_URL/parse" \
    -H 'Content-Type: application/json' \
    -d "{\"input_path\":\"$FIXTURE\",\"overwrite\":true}" | sed 's/^/  /'
echo

echo "==> 5:30-6:30 SQL query (Postgres view)"
PGPASSWORD=lineagepass psql -h localhost -U lineage -d lineage \
    -c "SELECT job_name, schedule_name, script_path, start_time
        FROM tws.v_runtime_window
        WHERE start_time >= '05:30' AND start_time < '06:30';" || true

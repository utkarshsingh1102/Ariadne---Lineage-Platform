#!/usr/bin/env bash
# Manual end-to-end smoke check. Hits the running parser container against the
# bundled minimal fixture and prints stats. Use it to confirm Phase 1 is wired
# end-to-end before plugging the user's own test suite in.
#
# Assumes lineage-platform docker-compose is up (neo4j + tableau-parser).

set -euo pipefail

PARSER_URL="${PARSER_URL:-http://localhost:8001}"
FIXTURE="${FIXTURE:-/data/inputs/minimal_single_datasource.twb}"

echo "==> Parser health"
curl -fsS "$PARSER_URL/health" | sed 's/^/  /'
echo

echo "==> Parser version"
curl -fsS "$PARSER_URL/version" | sed 's/^/  /'
echo

echo "==> Parsing $FIXTURE"
curl -fsS -X POST "$PARSER_URL/parse" \
    -H 'Content-Type: application/json' \
    -d "{\"file_path\":\"$FIXTURE\",\"overwrite\":true}" | sed 's/^/  /'
echo

echo "==> Done. Inspect at http://localhost:7474 (neo4j / lineagepass)"

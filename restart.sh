#!/usr/bin/env bash
#
# restart.sh — bounce the full Lineage Platform stack.
#
# Runs ./stop.sh first to tear everything down, waits for it to exit
# cleanly, then runs ./start.sh to bring the stack back up. Forwards any
# CLI args (e.g. ``./restart.sh --no-cache``) to start.sh.

set -euo pipefail

# Resolve to the directory this script lives in so it works regardless of
# the caller's working directory.
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

if [[ ! -x ./stop.sh ]]; then
  echo "error: ./stop.sh not found or not executable in $SCRIPT_DIR" >&2
  exit 1
fi
if [[ ! -x ./start.sh ]]; then
  echo "error: ./start.sh not found or not executable in $SCRIPT_DIR" >&2
  exit 1
fi

echo "=== restart.sh: stopping stack ==="
./stop.sh

echo
echo "=== restart.sh: starting stack ==="
exec ./start.sh "$@"

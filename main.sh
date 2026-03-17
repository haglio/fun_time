#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python >/dev/null 2>&1; then
  exec python -m fun_time.orchestrator "$@"
fi

if command -v py >/dev/null 2>&1; then
  exec py -3 -m fun_time.orchestrator "$@"
fi

echo "Could not find python or py on PATH." >&2
exit 1
#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# The project venv, never a python from PATH: fun_time imports its sibling
# packages -- app_support, player_core -- and those are editable installs that
# exist only here. A PATH python finds the sibling repo directories as namespace
# packages instead and dies while importing, before the orchestrator has
# configured any logging, so the launch fails without saying anything.
VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
if [[ ! -f "$VENV_PYTHON" ]]; then
  echo "Fun Time's virtual environment is missing: $VENV_PYTHON" >&2
  exit 1
fi

exec "$VENV_PYTHON" -m fun_time.orchestrator "$@"

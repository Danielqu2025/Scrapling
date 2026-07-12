#!/usr/bin/env bash
# Start Shanghai EIA web app on Linux/macOS (loads .env if present).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  echo "Loaded .env"
fi

export SH_EIA_HOST="${SH_EIA_HOST:-0.0.0.0}"
export SH_EIA_PORT="${SH_EIA_PORT:-8080}"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/../../.venv/bin/python" ]]; then
  PYTHON="$ROOT/../../.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "Starting sh_eia on ${SH_EIA_HOST}:${SH_EIA_PORT} (auth=${SH_EIA_AUTH_ENABLED:-0})"
exec "$PYTHON" "$ROOT/04_run_server.py"

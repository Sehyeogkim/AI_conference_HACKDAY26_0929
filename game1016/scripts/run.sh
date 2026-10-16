#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "Run bash scripts/setup.sh first." >&2
  exit 1
fi
exec .venv/bin/python -m coffee_game.server --host 127.0.0.1 --port "${COFFEE_PORT:-8786}"

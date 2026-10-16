#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
if [[ ! -f .env.cloud ]]; then
  python3 - <<'TOKEN'
from pathlib import Path
import secrets
Path('.env.cloud').write_text('COFFEE_ACCESS_TOKEN='+secrets.token_urlsafe(32)+'\n')
TOKEN
fi
set -a
source .env.cloud
set +a
: "${COFFEE_ACCESS_TOKEN:?Missing private demo token}"
export MUJOCO_GL=egl PYOPENGL_PLATFORM=egl
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
exec .venv/bin/python -m coffee_game.server --host 0.0.0.0 --port "${COFFEE_PORT:-8786}"

#!/usr/bin/env bash
set -euo pipefail
GAME_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$GAME_ROOT"
command -v uv >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
mkdir -p vendor
RLDX_SHA=cf67c31f7f168ebbca6d06e8c3c23914cb94435c
SUITE_SHA=5ce6643f3092639d08f7b0f90ed1c6a84f50552c
if [[ ! -d vendor/RLDX-1/.git ]]; then
  git clone --filter=blob:none --no-checkout https://github.com/RLWRLD/RLDX-1.git vendor/RLDX-1
  git -C vendor/RLDX-1 sparse-checkout set external_dependencies/robocasa rldx/eval/sim/robocasa run_scripts/eval/robocasa_kitchen rldx/configs/data
  git -C vendor/RLDX-1 checkout "$RLDX_SHA"
fi
if [[ ! -d vendor/robosuite/.git ]]; then
  git clone --filter=blob:none --no-checkout https://github.com/ARISE-Initiative/robosuite.git vendor/robosuite
  git -C vendor/robosuite checkout "$SUITE_SHA"
fi
[[ "$(git -C vendor/RLDX-1 rev-parse HEAD)" == "$RLDX_SHA" ]] || { echo 'Unexpected RLDX source revision'; exit 1; }
[[ "$(git -C vendor/robosuite rev-parse HEAD)" == "$SUITE_SHA" ]] || { echo 'Unexpected robosuite source revision'; exit 1; }
[[ -d .venv ]] || uv venv .venv --python 3.11
case "$(uname -s)" in
  Darwin) GAME_REQUIREMENTS=requirements.lock ;;
  Linux) GAME_REQUIREMENTS=requirements.txt ;;
  *) echo 'Supported setup targets: macOS or Linux'; exit 1 ;;
esac
uv pip install --python .venv/bin/python -r "$GAME_REQUIREMENTS"
uv pip install --python .venv/bin/python --no-deps -e vendor/robosuite -e vendor/RLDX-1/external_dependencies/robocasa
.venv/bin/python scripts/install_assets.py
.venv/bin/python -c 'import numpy, mujoco, pyarrow; print("Dependencies:", numpy.__version__, mujoco.__version__, pyarrow.__version__)'
mkdir -p logs
uv pip freeze --python .venv/bin/python --exclude-editable > "logs/requirements-$(uname -s).resolved.txt"
echo 'Ready: .venv/bin/python -m coffee_game.server --host 127.0.0.1 --port 8786'

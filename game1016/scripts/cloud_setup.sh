#!/usr/bin/env bash
# Ubuntu/Debian NVIDIA GPU pod prerequisites, followed by the pinned game setup.
# Run this inside the already-created pod; it does not provision infrastructure.
set -euo pipefail
GAME_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$GAME_ROOT"
[[ "$(uname -s)" == Linux ]] || { echo 'cloud_setup.sh targets Debian/Ubuntu Linux'; exit 1; }
command -v apt-get >/dev/null || { echo 'Use an Ubuntu/Debian-based GPU container'; exit 1; }
GAME_APT=()
if [[ "$(id -u)" != 0 ]]; then
  command -v sudo >/dev/null || { echo 'apt prerequisites require root or sudo'; exit 1; }
  GAME_APT=(sudo)
fi
"${GAME_APT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update
"${GAME_APT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  git curl ca-certificates build-essential pkg-config libudev-dev libevdev2 \
  libgl1 libegl1 libgles2 libglfw3 libosmesa6 libglib2.0-0 \
  libx11-6 libxi6 libxrandr2 libxinerama1 libxcursor1 libxxf86vm1 libsm6 libxext6

export UV_CACHE_DIR="$GAME_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$GAME_ROOT/.tools/python"
if ! command -v uv >/dev/null; then
  mkdir -p .tools
  curl -LsSf https://astral.sh/uv/install.sh -o .tools/install-uv.sh
  UV_INSTALL_DIR="$GAME_ROOT/.tools/bin" UV_NO_MODIFY_PATH=1 sh .tools/install-uv.sh
  export PATH="$GAME_ROOT/.tools/bin:$PATH"
fi
export MUJOCO_GL=egl
export PYOPENGL_PLATFORM=egl
bash scripts/setup.sh

# This checks that the GPU runtime exposes the driver, not that scene rendering
# works. Verify the actual game from outside the pod before reporting deployment.
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
else
  echo 'Warning: NVIDIA driver utilities are not visible in this container.'
fi
cat <<'EOF'
Setup complete. Launch one process on the HTTP port exposed by the pod:
MUJOCO_GL=egl PYOPENGL_PLATFORM=egl .venv/bin/python -m coffee_game.server --host 0.0.0.0 --port 8786

The pod must expose NVIDIA graphics/EGL drivers (NVIDIA_DRIVER_CAPABILITIES=all).
Do not apt-install host GPU drivers inside the container. Test /api/status and a
real rendered gameplay/recording request from the external proxy URL.
EOF

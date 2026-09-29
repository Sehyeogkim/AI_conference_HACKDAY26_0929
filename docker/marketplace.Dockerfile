# Marketplace web app (agriphilo/): Python server plus Node, because server-side QA replays each
# submitted recording with web/tools/replayRecording.ts. Compose bind-mounts the repository at /app
# and shadows web/node_modules with an anonymous volume filled from this image.
FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv "$VIRTUAL_ENV"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund

WORKDIR /app
CMD ["python", "-m", "agriphilo.web", "--host", "0.0.0.0", "--port", "8765", "--demo"]

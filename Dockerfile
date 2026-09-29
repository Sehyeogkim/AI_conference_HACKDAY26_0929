# Python runtime for the jobs service (World Labs generation and world measurement).
# Compose bind-mounts the repository at /app, so containers always run the current code;
# the editable install below only registers the package and its dependencies.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

RUN useradd --create-home --uid 1001 wefarm
USER wefarm

CMD ["python", "-m", "wefarm.check_secrets"]

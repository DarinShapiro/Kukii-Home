# Shared Dockerfile for all SentiHome Python services.
# Build context is the repo root. The actual service entry point is selected
# at runtime via the compose `command` directive.
#
# Usage (from repo root):
#   docker build -f infrastructure/docker/python-service.Dockerfile .

FROM python:3.12-slim AS base

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# System deps needed by OpenCV / ffmpeg / numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy workspace metadata first (so dependency layer caches)
COPY pyproject.toml uv.lock ./
COPY shared/lib-python/pyproject.toml shared/lib-python/
COPY services/core/pyproject.toml services/core/
COPY services/preprocessor/pyproject.toml services/preprocessor/
COPY services/detector/pyproject.toml services/detector/
COPY services/vlm-router/pyproject.toml services/vlm-router/
COPY services/memory/pyproject.toml services/memory/
COPY services/ha-agent/pyproject.toml services/ha-agent/
COPY services/notify/pyproject.toml services/notify/
COPY adapters/nvr-rtsp-direct/pyproject.toml adapters/nvr-rtsp-direct/
COPY adapters/nvr-agent-dvr/pyproject.toml adapters/nvr-agent-dvr/
COPY adapters/nvr-frigate/pyproject.toml adapters/nvr-frigate/
COPY adapters/nvr-blueiris/pyproject.toml adapters/nvr-blueiris/
COPY adapters/nvr-synology/pyproject.toml adapters/nvr-synology/
COPY adapters/nvr-qnap/pyproject.toml adapters/nvr-qnap/
COPY adapters/nvr-unifi/pyproject.toml adapters/nvr-unifi/
COPY ha-integration/pyproject.toml ha-integration/

# Then source code
COPY shared/ shared/
COPY services/ services/
COPY adapters/ adapters/
COPY ha-integration/ ha-integration/

# Install all workspace packages
RUN uv sync --all-packages --frozen --no-dev

# uv installs into .venv; put it on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Default entry point is per-service via compose `command`
CMD ["python", "-c", "print('Specify a service via compose command directive')"]

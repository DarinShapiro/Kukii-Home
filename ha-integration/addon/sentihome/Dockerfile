ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

# Add-on metadata (visible to Supervisor).
LABEL \
    io.hass.name="SentiHome" \
    io.hass.description="AI-powered home security and presence understanding" \
    io.hass.type="addon" \
    io.hass.version="0.1.0" \
    maintainer="Darin Shapiro <darin@example.com>"

ENV \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    SENTIHOME_ENV=addon

# System packages SentiHome services need at runtime.
# - libffi/openssl: pydantic + asyncpg
# - ffmpeg: rtsp-direct adapter, frame extraction
# - jq: run.sh uses it to read /data/options.json
RUN apk add --no-cache \
        bash \
        ffmpeg \
        jq \
        libffi \
        openssl \
        tini

# Install uv (matches CI version).
RUN pip install --no-cache-dir uv==0.11.16

WORKDIR /app

# Copy the workspace. Order: lockfiles + pyprojects first for layer cache,
# then sources.
COPY pyproject.toml uv.lock /app/
COPY shared /app/shared
COPY services /app/services
COPY adapters /app/adapters

# Install every workspace member system-wide.
RUN uv sync --frozen --no-dev

# s6-overlay service definitions + run.sh entrypoint.
COPY ha-integration/addon/sentihome/rootfs /

RUN chmod +x /etc/services.d/*/run /etc/cont-init.d/*

# s6-overlay (provided by the homeassistant base image) takes over PID 1.
CMD ["/init"]

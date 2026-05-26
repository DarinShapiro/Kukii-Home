# BUILD_FROM has no default on purpose: build.yaml maps each arch to its
# correct base image and Supervisor passes the right one via --build-arg.
# Building manually? Set BUILD_FROM explicitly:
#   docker build \
#     --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20 \
#     ...
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Add-on metadata (visible to Supervisor).
LABEL \
    io.hass.name="SentiHome" \
    io.hass.description="AI-powered home security and presence understanding" \
    io.hass.type="addon" \
    io.hass.version="0.1.3" \
    maintainer="Darin Shapiro <darin@example.com>"

ENV \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    SENTIHOME_ENV=addon

# System packages SentiHome services need at runtime.
# - git: clones the workspace inside the image (Supervisor's build context
#   is the add-on directory, not the repo root, so we can't COPY workspace
#   sources in. This is the canonical pattern for HA add-ons that need a
#   larger source tree than their own directory.)
# - libffi/openssl: pydantic + asyncpg
# - ffmpeg: rtsp-direct adapter, frame extraction
# - jq: run.sh uses it to read /data/options.json
RUN apk add --no-cache \
        bash \
        ffmpeg \
        git \
        jq \
        libffi \
        openssl \
        tini

# Install uv (matches CI version).
RUN pip install --no-cache-dir uv==0.11.16

# Clone the SentiHome workspace. SENTIHOME_REF defaults to main but can be
# overridden at build time for testing against a branch:
#   docker build --build-arg SENTIHOME_REF=feature/foo ...
ARG SENTIHOME_REPO=https://github.com/DarinShapiro/SentiHome.git
ARG SENTIHOME_REF=main

WORKDIR /app
RUN git clone --depth=1 --branch "${SENTIHOME_REF}" "${SENTIHOME_REPO}" /app \
    && rm -rf /app/.git

# Install every workspace member system-wide.
RUN uv sync --frozen --no-dev

# s6-overlay service definitions + run.sh entrypoint live in this add-on
# directory (the build context).
COPY rootfs /

RUN chmod +x /etc/services.d/*/run /etc/cont-init.d/*

# s6-overlay (provided by the homeassistant base image) takes over PID 1.
CMD ["/init"]

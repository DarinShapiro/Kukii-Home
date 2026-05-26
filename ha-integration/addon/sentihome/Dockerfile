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
    io.hass.version="0.1.9" \
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

# Docker layer cache can't tell that "git clone main" returns different
# content on different days — so without a cache-buster, Supervisor's
# image rebuild would serve stale source. Two cache-busters belt-and-
# suspenders:
#   1. ADD on a URL whose body changes when main advances. Some BuildKit
#      versions don't interpolate ARGs in ADD URLs reliably, so we
#      hardcode the path here.
#   2. An ARG that callers (the addon-build CI workflow) pass the commit
#      SHA into, so even if (1) doesn't bust, this does.
ADD https://api.github.com/repos/DarinShapiro/SentiHome/commits/main /tmp/.sentihome_main_head
ARG CACHEBUST=0

WORKDIR /app
RUN echo "Cache key: ${CACHEBUST}" \
    && git clone --depth=1 --branch "${SENTIHOME_REF}" "${SENTIHOME_REPO}" /app \
    && rm -rf /app/.git

# Install the workspace members we actually run in the add-on.
#
# We can NOT use `uv sync --all-packages` because that pulls in
# sentihome-detector, which depends on onnxruntime — and onnxruntime
# only ships glibc (manylinux) wheels, not musllinux. The HA base-python
# image is alpine, so onnxruntime install fails with "no wheel for the
# current platform". The detector service is facade+stubs in v1 and not
# yet run; skipping it is correct.
#
# When the detector graduates from facade to real ML inference, we'll
# either:
#   - switch this Dockerfile to a debian-based HA base (homeassistant/
#     <arch>-base-debian) which is glibc-compatible, OR
#   - use onnxruntime's source build inside the image (slow but works
#     on musllinux), OR
#   - keep detector as a separate add-on with its own debian image
RUN uv sync --frozen --no-dev \
    --package sentihome-shared \
    --package sentihome-core \
    --package sentihome-memory \
    --package sentihome-ha-agent \
    --package sentihome-notify \
    --package sentihome-vlm-router \
    --package sentihome-preprocessor \
    --package sentihome-adapter-rtsp-direct \
    --package sentihome-adapter-agent-dvr \
    --package sentihome-adapter-frigate \
    --package sentihome-adapter-blueiris \
    --package sentihome-adapter-synology \
    --package sentihome-adapter-qnap \
    --package sentihome-adapter-unifi

# Verify the workspace installed correctly. If any of these imports fail,
# the s6 services would crash-loop at runtime with "No module named ..." —
# fail the BUILD instead so the misconfig surfaces in install/CI.
RUN /app/.venv/bin/python -c "import sentihome_shared.topology; \
    import sentihome_core; \
    import sentihome_memory; \
    import sentihome_ha_agent; \
    import sentihome_notify; \
    import sentihome_vlm_router; \
    import sentihome_preprocessor; \
    print('SentiHome packages OK:', sentihome_ha_agent.__version__)"

# s6-overlay service definitions + run.sh entrypoint live in this add-on
# directory (the build context).
COPY rootfs /

RUN chmod +x /etc/services.d/*/run /etc/cont-init.d/*

# s6-overlay (provided by the homeassistant base image) takes over PID 1.
CMD ["/init"]

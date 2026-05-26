# BUILD_FROM is supplied per-arch by build.yaml. Standalone builds must
# pass it explicitly:
#   docker build \
#     --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
#     ...
ARG BUILD_FROM
FROM ${BUILD_FROM}

LABEL \
    io.hass.name="SentiHome" \
    io.hass.description="AI-powered home security and presence understanding" \
    io.hass.type="addon" \
    io.hass.version="0.1.10" \
    maintainer="Darin Shapiro <darin@example.com>"

ENV \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    DEBIAN_FRONTEND=noninteractive \
    SENTIHOME_ENV=addon

# System packages: debian-bookworm flavor.
# - python3 + venv + pip: SentiHome runtime
# - git: cloning the workspace inside the image (build context is the
#   add-on directory, not the repo root, so we can't COPY workspace
#   sources in)
# - ffmpeg: rtsp-direct adapter
# - jq: cont-init bootstrap reads /data/options.json
# - libffi-dev / libssl-dev / build-essential: present so any source-build
#   fallback (uncommon on debian since manylinux wheels exist for almost
#   everything) succeeds
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        jq \
        libffi8 \
        libssl3 \
        python3 \
        python3-pip \
        python3-venv \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Install uv (matches the version pinned by CI).
RUN pip install --no-cache-dir --break-system-packages uv==0.11.16

# Cache-busters (belt + suspenders). Some BuildKit versions don't
# reliably interpolate ARGs in ADD URLs, so the URL is hardcoded; the
# CACHEBUST ARG is a second mechanism callers (CI) can use.
ADD https://api.github.com/repos/DarinShapiro/SentiHome/commits/main /tmp/.sentihome_main_head
ARG CACHEBUST=0

ARG SENTIHOME_REPO=https://github.com/DarinShapiro/SentiHome.git
ARG SENTIHOME_REF=main

WORKDIR /app
RUN echo "Cache key: ${CACHEBUST}" \
    && git clone --depth=1 --branch "${SENTIHOME_REF}" "${SENTIHOME_REPO}" /app \
    && rm -rf /app/.git

# Install every workspace member into /app/.venv. The debian base lets
# us use --all-packages safely; manylinux wheels for opencv-python and
# onnxruntime install cleanly here (they don't on alpine/musllinux).
RUN uv sync --frozen --no-dev --all-packages

# Build-time verification: import every SentiHome package via the venv
# python (the s6 run scripts use the same path). If anything's missing,
# fail HERE instead of crash-looping at runtime in your add-on log.
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

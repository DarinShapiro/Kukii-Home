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
    io.hass.version="0.3.5" \
    maintainer="Darin Shapiro <darin@example.com>"

ENV \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    DEBIAN_FRONTEND=noninteractive \
    SENTIHOME_ENV=addon

# System packages (debian-bookworm flavor).
# Notably absent: `tini`. The HA base image already arranges for s6's
# /init to be PID 1 via ENTRYPOINT; installing tini in the apt set was
# fine but would conflict if Docker ever applied --init implicitly.
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
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages uv==0.11.16

# Cache-busters (belt + suspenders).
ADD https://api.github.com/repos/DarinShapiro/SentiHome/commits/main /tmp/.sentihome_main_head
ARG CACHEBUST=0

ARG SENTIHOME_REPO=https://github.com/DarinShapiro/SentiHome.git
ARG SENTIHOME_REF=main

WORKDIR /app
RUN echo "Cache key: ${CACHEBUST}" \
    && git clone --depth=1 --branch "${SENTIHOME_REF}" "${SENTIHOME_REPO}" /app \
    && rm -rf /app/.git

# Install every workspace member into /app/.venv (debian base = glibc =
# every manylinux wheel works including opencv + onnxruntime).
RUN uv sync --frozen --no-dev --all-packages

# Build-time verification: import every SentiHome package via the venv
# python. If anything's missing, fail HERE instead of crash-looping in
# your add-on log.
RUN /app/.venv/bin/python -c "import sentihome_shared.topology; \
    import sentihome_core; \
    import sentihome_memory; \
    import sentihome_ha_agent; \
    import sentihome_notify; \
    import sentihome_vlm_router; \
    import sentihome_preprocessor; \
    print('SentiHome packages OK:', sentihome_ha_agent.__version__)"

# s6-overlay service definitions + cont-init scripts.
# v0.1.11: only ha-agent runs under s6 in this build — the other five
# services have no runtime daemons yet (in-process Python only), so
# spinning them under s6 just produces noise. They re-enter when the
# bus runtime wires up in Epic 10+.
COPY rootfs /
RUN chmod +x /etc/services.d/*/run /etc/cont-init.d/*

# Do NOT set CMD ["/init"] — the HA base image already has
# ENTRYPOINT ["/init"]. Adding CMD makes Docker run `/init /init`, which
# breaks PID 1 semantics and produces:
#   s6-overlay-suexec: fatal: can only run as pid 1
# Letting the base ENTRYPOINT run with no args is correct.

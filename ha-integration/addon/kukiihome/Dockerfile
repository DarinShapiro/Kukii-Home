# BUILD_FROM is supplied per-arch by build.yaml. Standalone builds must
# pass it explicitly:
#   docker build \
#     --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
#     ...
ARG BUILD_FROM
FROM ${BUILD_FROM}

LABEL \
    io.hass.name="Kukii-Home" \
    io.hass.description="AI-powered home security and presence understanding" \
    io.hass.type="addon" \
    io.hass.version="0.4.0" \
    maintainer="Darin Shapiro <darin@example.com>"

ENV \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    DEBIAN_FRONTEND=noninteractive \
    KUKIIHOME_ENV=addon

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
ADD https://api.github.com/repos/DarinShapiro/Kukii-Home/commits/main /tmp/.kukiihome_main_head
ARG CACHEBUST=0

ARG KUKIIHOME_REPO=https://github.com/DarinShapiro/Kukii-Home.git
ARG KUKIIHOME_REF=main

WORKDIR /app
# Fetch the requested ref via init+fetch+checkout rather than
# `clone --branch`. `clone --branch` only accepts a branch/tag NAME,
# but CI pins KUKIIHOME_REF to a commit SHA (github.sha) so PR/CI
# builds verify the exact commit. fetch-by-ref handles BOTH a branch
# (Supervisor's default `main`) and a bare SHA (GitHub allows fetching
# a reachable commit), so the real Supervisor build is unaffected.
RUN echo "Cache key: ${CACHEBUST}" \
    && git init /app \
    && git -C /app remote add origin "${KUKIIHOME_REPO}" \
    && git -C /app fetch --depth=1 origin "${KUKIIHOME_REF}" \
    && git -C /app checkout --detach FETCH_HEAD \
    && rm -rf /app/.git

# Epic 10.8.2: only install the ha-agent package + its deps. The
# preprocessor (with torch + onnxruntime + openvino + insightface,
# ~3GB of inference stack) runs on a separate inference box, not on
# Yellow. The add-on calls the preprocessor's REST endpoints via the
# LAN. See docs/architecture/ for the deployment topology.
RUN uv sync --frozen --no-dev --package kukiihome-ha-agent

# Build-time verification: import the packages the add-on actually
# uses at runtime. If anything's missing, fail HERE instead of
# crash-looping in the add-on log.
RUN /app/.venv/bin/python -c "import kukiihome_shared.topology; \
    import kukiihome_shared.motion; \
    import kukiihome_ha_agent; \
    print('Kukii-Home packages OK:', kukiihome_ha_agent.__version__)"

# Stamp the add-on version into the image so /debug/version can return it.
ARG ADDON_VERSION=unknown
RUN echo "${ADDON_VERSION}" > /app/.kukiihome_addon_version

# Epic 10.2: memory-graph Neo4j sidecar (OPTIONAL, default OFF).
# Installs OpenJDK 17 + Neo4j Community so the add-on CAN run a local
# graph DB when the operator sets `neo4j_enabled: true`. The whole block
# is BEST-EFFORT / non-fatal: if the apt repo or aarch64 package isn't
# available at build time, the build still succeeds and the add-on ships
# WITHOUT the sidecar — the ha-agent's graph factory then falls back to
# its in-process in-memory backend. So this never breaks the build for
# the (default) users who never enable Neo4j. The s6 neo4j service also
# checks the option flag + the binary's presence before starting, so a
# skipped install degrades cleanly to in-memory at runtime too.
RUN ( \
        apt-get update \
        && apt-get install -y --no-install-recommends \
            gnupg openjdk-17-jre-headless wget \
        && wget -qO /tmp/neo4j.gpg https://debian.neo4j.com/neotechnology.gpg.key \
        && gpg --dearmor < /tmp/neo4j.gpg > /usr/share/keyrings/neo4j.gpg \
        && echo "deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5" \
            > /etc/apt/sources.list.d/neo4j.list \
        && apt-get update \
        && apt-get install -y --no-install-recommends neo4j \
        && rm -rf /var/lib/apt/lists/* \
        && echo "[build] Neo4j sidecar installed" \
    ) || echo "[build] Neo4j sidecar install SKIPPED — add-on will use in-memory graph"

# s6-overlay service definitions + cont-init scripts.
# v0.1.11: only ha-agent runs under s6 in this build — the other five
# services have no runtime daemons yet (in-process Python only), so
# spinning them under s6 just produces noise. They re-enter when the
# bus runtime wires up in Epic 10+.
# Epic 10.2: a `neo4j` s6 service is added; it self-idles unless the
# add-on option neo4j_enabled=true AND the neo4j binary is present.
COPY rootfs /
RUN chmod +x /etc/services.d/*/run /etc/cont-init.d/*

# Do NOT set CMD ["/init"] — the HA base image already has
# ENTRYPOINT ["/init"]. Adding CMD makes Docker run `/init /init`, which
# breaks PID 1 semantics and produces:
#   s6-overlay-suexec: fatal: can only run as pid 1
# Letting the base ENTRYPOINT run with no args is correct.

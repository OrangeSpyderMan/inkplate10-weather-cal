FROM python:3.14-slim-trixie AS base

ARG BUILD_DATE
ARG VCS_REF
ARG VERSION
ARG USERNAME=inkplate
ARG HOMEDIR=/srv/inkplate

LABEL org.opencontainers.image.title="Inkplate 10 Weather Calendar" \
    org.opencontainers.image.description="Server renderer for the Inkplate 10 weather calendar" \
    org.opencontainers.image.url="https://github.com/OrangeSpyderMan/inkplate10-weather-cal" \
    org.opencontainers.image.source="https://github.com/OrangeSpyderMan/inkplate10-weather-cal" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.version="${VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    INKPLATE_LOG_CONFIG=/srv/inkplate/server/logging.service.ini

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates isc-dhcp-client; \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --user-group --home-dir "${HOMEDIR}" "${USERNAME}"

WORKDIR ${HOMEDIR}

COPY server/requirements-common.txt server/requirements-pillow.txt /tmp/requirements/
RUN pip install --upgrade pip setuptools wheel \
    && pip install \
        -r /tmp/requirements/requirements-common.txt \
        -r /tmp/requirements/requirements-pillow.txt \
    && rm -rf /tmp/requirements

COPY --chown=${USERNAME}:${USERNAME} ./server ${HOMEDIR}/server
COPY --chown=${USERNAME}:${USERNAME} ./.version.json ${HOMEDIR}/.version.json
RUN mkdir -p ${HOMEDIR}/server/config ${HOMEDIR}/server/data \
    && chmod 0755 ${HOMEDIR}/server/container_entrypoint.py \
    && chown -R ${USERNAME}:${USERNAME} ${HOMEDIR}/server

EXPOSE 8080
CMD ["/srv/inkplate/server/container_entrypoint.py"]


FROM base AS pillow

ARG USERNAME=inkplate

LABEL org.opencontainers.image.description="Low-footprint Pillow renderer for the Inkplate 10 weather calendar" \
    org.opencontainers.image.variant="pillow"

ENV INKPLATE_CONTAINER_FLAVOUR=pillow

USER ${USERNAME}


FROM base AS full

ARG GECKOVERSION=v0.37.0
ARG TARGETPLATFORM
ARG USERNAME=inkplate

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl firefox-esr; \
    arch_from_uname="$(uname -m)"; \
    case "${TARGETPLATFORM:-}" in \
        "") target="${arch_from_uname}" ;; \
        *) target="${TARGETPLATFORM#*/}" ;; \
    esac; \
    case "$target" in \
        amd64|x86_64) gecko_arch=linux64 ;; \
        arm64|aarch64) gecko_arch=linux-aarch64 ;; \
        *) echo "Unsupported target architecture: $target" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/mozilla/geckodriver/releases/download/${GECKOVERSION}/geckodriver-${GECKOVERSION}-${gecko_arch}.tar.gz"; \
    echo "Downloading geckodriver from: $url"; \
    curl -fsSL "$url" | tar xz -C /usr/local/bin; \
    chmod +x /usr/local/bin/geckodriver; \
    /usr/local/bin/geckodriver --version; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

COPY server/requirements-firefox.txt /tmp/requirements-firefox.txt
RUN pip install -r /tmp/requirements-firefox.txt \
    && rm /tmp/requirements-firefox.txt

LABEL org.opencontainers.image.variant="full"

ENV GECKODRIVER_PATH=/usr/local/bin/geckodriver \
    INKPLATE_CONTAINER_FLAVOUR=full

USER ${USERNAME}

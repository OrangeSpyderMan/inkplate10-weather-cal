# Uses the python slim image
FROM python:slim

# Set up the debconfig to be non-interactive
ENV DEBIAN_FRONTEND=noninteractive

# Install the packages we need, then clean up apt files to save space
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
    --no-install-recommends \
    firefox-esr \
    curl 

# GECKOVERSION can be set to a specific tag (eg. "v0.36.0") to pin a release.
# If unset or empty, the Dockerfile will fetch the latest GitHub release via
# the releases/latest/download redirect and pick the correct asset for the
# build platform.
ARG GECKOVERSION=

# When building with buildx the build system exposes TARGETPLATFORM (eg
# linux/amd64, linux/arm64). Fall back to uname -m if TARGETPLATFORM is not
# available.
ARG TARGETPLATFORM

RUN set -eux; \
        arch_from_uname="$(uname -m)"; \
        case "${TARGETPLATFORM:-""}" in \
            "" ) target="${arch_from_uname}" ;; \
            * ) target="${TARGETPLATFORM#*/}" ;; \
        esac; \
        case "$target" in \
            amd64|x86_64) gecko_arch=linux64 ;; \
            arm64|aarch64) gecko_arch=linux-aarch64 ;; \
            *) echo "Unsupported target architecture: $target" >&2; exit 1 ;; \
        esac; \
        if [ -n "${GECKOVERSION}" ]; then \
            tag="${GECKOVERSION}"; \
        else \
            # Query GitHub API for the latest release tag_name
            tag=$(curl -fsSL https://api.github.com/repos/mozilla/geckodriver/releases/latest \
                | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p'); \
            if [ -z "$tag" ]; then echo "Failed to determine latest geckodriver tag" >&2; exit 1; fi; \
        fi; \
        url="https://github.com/mozilla/geckodriver/releases/download/${tag}/geckodriver-${tag}-${gecko_arch}.tar.gz"; \
        echo "Downloading geckodriver from: $url"; \
        curl -fsSL "$url" | tar xz -C /usr/local/bin; \
        # Ensure binary is executable and verify installation
        chmod +x /usr/local/bin/geckodriver || true; \
        if /usr/local/bin/geckodriver --version >/dev/null 2>&1; then \
            echo "geckodriver installed:"; \
            /usr/local/bin/geckodriver --version; \
        else \
            echo "ERROR: geckodriver did not install or is not runnable" >&2; \
            ls -l /usr/local/bin || true; \
            exit 1; \
        fi; \
        apt-get purge -y curl; \
        rm -rf /var/lib/apt/lists/*

ARG USERNAME=inkplate
ARG HOMEDIR=/srv/inkplate
RUN useradd -m ${USERNAME} -d ${HOMEDIR}


# Switch to the new unprivileged user, in the server directory
USER ${USERNAME}
WORKDIR ${HOMEDIR}
RUN mkdir ${HOMEDIR}/server

COPY --chown=${USERNAME}:${USERNAME} ./server ${HOMEDIR}/server

ENV PATH="${HOMEDIR}/.local/bin:/usr/local/bin:$PATH"

# Then install the modules we need from the requirements files we copied earlier

RUN pip install -U pip setuptools wheel
RUN pip install -r ${HOMEDIR}/server/requirements.txt

EXPOSE 8080
# Uncomment the below if using the MQTT logging
# EXPOSE 1883

# Start the server code
CMD ["python3", "server/server.py"]

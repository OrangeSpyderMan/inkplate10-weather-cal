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

# GECKOVERSION should be adjusted to use the latest release of geckodriver, or at least a compatible release for the version of
# firefox that is being installed.
# See : https://github.com/mozilla/geckodriver/releases/

# GECKOARCH should be set to 
# linux64 for x86_64 systems 
# OR 
# linux-aarch64 for linux on ARM64

ARG GECKOVERSION=v0.36.0
ARG GECKOARCH=linux-aarch64

RUN curl -L https://github.com/mozilla/geckodriver/releases/download/${GECKOVERSION}/geckodriver-${GECKOVERSION}-${GECKOARCH}.tar.gz | tar xz -C /usr/local/bin \
    && apt-get purge -y curl \
    && rm -rf /var/lib/apt/lists/*

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

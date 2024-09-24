# Uses the python slim Debian Boookworm Image
FROM python:3.13-rc-slim

# Set up the debconfig to be non-interactive
ENV DEBIAN_FRONTEND=noninteractive

# Install the packages we need, then clean up apt files to save space
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
    wget \
    unattended-upgrades 

RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
RUN apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

ARG USERNAME=inkplate
ARG HOMEDIR=/srv/inkplate
RUN useradd -m ${USERNAME} -d ${HOMEDIR}


# Switch to the new unprivileged user, in the server directory
USER ${USERNAME}
WORKDIR ${HOMEDIR}
RUN mkdir ${HOMEDIR}/server

COPY --chown=${USERNAME}:${USERNAME} ./server ${HOMEDIR}/server

ENV PATH="${HOMEDIR}/.local/bin:$PATH"

# Then install the modules we need from the requirements files we copied earlier

RUN pip install -U pip setuptools wheel
RUN pip install -r /srv/inkplate/server/requirements.txt

EXPOSE 8080
# EXPOSE 1883

# Start the server code
CMD ["python3", "server/server.py"]

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

# Create the directory that we'll use for the server code
RUN mkdir /srv/inkplate
RUN mkdir /srv/inkplate/server

# Copy across the directories for the server 
COPY ./server /srv/inkplate/server

# Change this if you want a different username
ARG USERNAME=inkplate

# Create a user to avoid running as root, then have that user own the directory the server will run in
RUN useradd -m $USERNAME 
RUN chown -R $USERNAME:$USERNAME /srv/inkplate

# Switch to the new unprivileged user, in the server directory
USER $USERNAME
WORKDIR /srv/inkplate

# Create a python venv to install the additional python modules we need
#ENV VIRTUAL_ENV=/srv/inkplate/inkplate_venv
#RUN python3 -m venv $VIRTUAL_ENV
#ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Then install the modules we need from the requirements files we copied earlier
RUN pip install -U pip setuptools wheel
RUN pip install -r /srv/inkplate/server/requirements.txt

EXPOSE 8080
# EXPOSE 1883

# Start the server code
CMD ["python3", "server/server.py"]

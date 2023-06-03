FROM debian:bookworm-backports

ENV DEBIAN_FRONTEND=noninteractive
VOLUME /srv/inkplate/server

RUN apt-get update && \
    apt-get install -y \
    chromium-driver \
    python3 \
    python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /srv/inkplate
RUN mkdir /srv/inkplate/server

ARG USERNAME=inkplate

RUN useradd -m $USERNAME 
RUN chown -R $USERNAME:$USERNAME /srv/inkplate


# Create a cronjob to run the server ever hour - this might be excessive, but shouldn't be problematic

RUN echo '0 * * * * inkplate /srv/inkplate//inkplate_venv/bin/activate && /srv/inkplate/inkplate_venv/python /srv/inkplate/server/server.py' >/etc/cron.d/inkplate

# Setup the server files by copying the "server" directory from the source as the newly create inkplate user

USER $USERNAME
WORKDIR /srv/inkplate
COPY ./server/requirements.txt /srv/inkplate/installed-requirements.txt
ENV VIRTUAL_ENV=/srv/inkplate/inkplate_venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install dependencies:
RUN pip install -r installed-requirements.txt

RUN python3 -m venv inkplate_venv
# RUN . ./inkplate_venv/bin/activate

RUN ./inkplate_venv/bin/pip install -r /srv/inkplate/installed-requirements.txt

EXPOSE 8080
EXPOSE 1883

CMD ["python", "server/server.py"]
# CMD . ./inkplate_venv/bin/activate && /srv/inkplate/inkplate_venv/python3 ./server/server.py



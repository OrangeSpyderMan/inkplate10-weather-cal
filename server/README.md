# Inkplate 10 Weather Calendar Server

A service for the weather calendar client written in Python3, backed by [Airium](https://pypi.org/project/airium/) and [Firefox](https://www.mozilla.org/firefox/).



Example 1                  | Example 2                 | Example 3
:-------------------------:|:-------------------------:|:-------------------------:
<img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/c37e6b65-a226-40d7-b1c7-cb3d72973054 width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/71958bcb-839d-447a-b671-a4cb5fbca25e width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/90608c9f-c16e-4d56-9edc-13b9d85ef659 width=300 />

<img width="1044" alt="Screenshot 2023-05-17 at 01 07 53" src="https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/e02e672b-7ad0-431d-8a29-c2740857a4d7">



- Uses [AccuWeather](https://developer.accuweather.com/) or [OpenWeatherMap](https://openweathermap.org/api) APIs for weather data.
- Uses Google's [StaticMaps API](https://developers.google.com/maps/documentation/maps-static/overview) to generate a static map of your area.
- Uses [Airium](https://pypi.org/project/airium/) then [Selenium](https://pypi.org/project/selenium/) / [Geckodriver](https://github.com/mozilla/geckodriver) / [Firefox](https://www.mozilla.org/firefox/) to generate HTML and save it as PNG files for image serving.
- Uses [Flask](https://flask.palletsprojects.com/en/2.3.x/) to serve images and
  a browser/PWA viewer.

## Setup

### Recommended interactive install

From the repository root, run:

```bash
./bin/install_server
```

The installer walks through Docker Compose or native systemd installation. It
uses the same defaults as the server code and example config: OpenWeatherMap v3,
port `8080`, six forecast slots, a three-hour refresh interval, `825x1200`
images, and MQTT disabled.

It prompts for the location, weather API key, Google Static Maps API key, Google
Static Maps Map ID, optional Netatmo credentials, optional MQTT weather
publishing, and whether to start the service or container. Secrets are written
outside committed YAML:

- Docker: `.env` plus `server/config/config.yaml`
- systemd: `/etc/inkplate/weather.env`,
  `/srv/inkplate/server/config/config.yaml`, and `/srv/inkplate/inkplate_venv`

Docker mode runs as the current user and expects that user to be able to run
`docker compose`. Native systemd mode needs root privileges for package
installation, `/srv/inkplate`, `/etc/inkplate/weather.env`, Geckodriver, and
systemd service management. Run it as root or as a user that can elevate with
`sudo`, `doas`, or `run0`; the installer checks this before making system
changes. Docker mode checks that `docker compose` is available and that the
current user can talk to the Docker daemon before starting the container.

Use dry-run mode to preview actions:

```bash
./bin/install_server --dry-run
```

For CI or repeatable testing, use the example JSON answers file:

```bash
./bin/install_server --dry-run --non-interactive --answers bin/install_server.answers.example.json
```

The example answers file contains placeholder secrets and is intended for
dry-run testing. Copy it and replace those values before using it for a real
non-interactive install.

Re-run the installer to update an existing install. It will detect existing
Docker or systemd files and offer to update the application while preserving
config/secrets, reconfigure config/secrets, or abort.

Logs:

```bash
docker compose logs -f
sudo journalctl -u inkplate -f
```

If Docker reports socket permission errors after adding a user to the `docker`
group, start a new login session or run `newgrp docker` before retrying.

### AccuWeather API

This is the least tested API.
 
In order to obtain an API Key, you will need to:
1. Sign up to [developer.accuweather.com](https://developer.accuweather.com/).
2. Create an app in [https://developer.accuweather.com/user/me/apps](https://developer.accuweather.com/user/me/apps).
3. Enter some details about the app's usage and purpose.
4. Generate API key.

Make sure you update the config `weather.apikey` with your generated api key and update `weather.service` to `accuweather`.

### OpenWeatherMap API

[DEPRECATED]
This provider is no longer supported in this version.  Please use [OpenWeatherMapv3](#openweathermapv3-api) that works with the OneCall V3 API [described here](https://openweathermap.org/api/one-call-3).

### OpenWeatherMapv3 API

This is the API that has had the most testing.

In order to obtain an API Key, you will need to sign up to OpenWeatherMap and [generate an API key](https://home.openweathermap.org/api_keys).

The server currently samples the hourly forecast at three-hour intervals. The number of forecast slots is configured with `weather.num_hourly_forecasts`; the example config uses 6. Larger values may need layout tuning so the forecast row remains readable on the Inkplate display.

### Current temperature source

By default the current temperature shown on the display comes from the configured weather provider:

```yaml
current_temperature:
  source: weather
```

You can optionally use a Netatmo Weather Station for only the current temperature while keeping the configured weather provider for the icon, min/max temperature, hourly forecast, and rain chart:

```yaml
current_temperature:
  source: netatmo
  netatmo:
    client_id: ${NETATMO_CLIENT_ID:-}
    client_secret: ${NETATMO_CLIENT_SECRET:-}
    refresh_token: ${NETATMO_REFRESH_TOKEN:-}
    token_file: netatmo-token.json
    device_id: ${NETATMO_DEVICE_ID:-} # optional; omit to use the first station
    module_id: ${NETATMO_MODULE_ID:-} # optional; omit to use the station indoor temperature
```

The Netatmo integration uses the refresh token to request access tokens and stores refreshed token data in `token_file`. If `module_id` is set, the temperature is read from that module. Otherwise the station `dashboard_data.Temperature` value is used.

### MQTT weather publishing

The server can optionally publish the same normalized weather snapshot used by
the renderer to MQTT. This is intended for lightweight local clients such as
ESP32 displays, Pico-class devices, Home Assistant, or Node-RED flows that need
weather data without talking directly to the weather provider APIs.

```yaml
mqtt:
  enabled: true
  host: localhost
  port: 1883
  base_topic: inkplate/weather-calendar
  retain: true
  qos: 0
```

When enabled, the server publishes retained JSON payloads after a successful
weather refresh:

```text
inkplate/weather-calendar
inkplate/weather-calendar/current
inkplate/weather-calendar/hourly
inkplate/weather-calendar/status
```

Publishing failures are logged but do not stop image generation or HTTP
serving.

### Secrets

Config values can reference environment variables with `${VARIABLE_NAME}`. Use `${VARIABLE_NAME:-default}` when the value is optional or when a provider is configured but not currently selected.

This lets you keep committed YAML files free of secrets and inject sensitive values from the runtime environment. In GitHub Actions, store those values as repository or environment secrets and pass them to the relevant step with `env:`. For local Docker runs, use an ignored `.env` file or shell environment variables.

### Image dimensions

The `image.width` and `image.height` config values set the browser capture
target for the generated PNG. The current HTML/CSS layout is tuned for Inkplate
10 portrait output at `825x1200`; those options are not a general layout scaling
system. Changing them may produce cropped, stretched, or poorly spaced output
unless the layout is also retuned.

### Browser and PWA viewer

The server also exposes a lightweight browser client for phones, tablets, and
desktop browsers. It does not reimplement the calendar layout; it displays the
same rendered PNG that the Inkplate uses.

Open the viewer at:

```text
http://<server-host>:8080/app
```

The root URL also opens the same viewer:

```text
http://<server-host>:8080/
```

The viewer refreshes the image every 15 minutes and whenever the browser tab or
installed app becomes visible. It fetches the image from:

```text
http://<server-host>:8080/app/calendar.png
```

This browser-facing image route is intentionally separate from the Inkplate
route:

- `/calendar.png` keeps the existing attachment response and increments the
  Inkplate serve counter used by one-shot server mode.
- `/app/calendar.png` serves the same file inline for browsers and does not
  increment the Inkplate serve counter.

For the browser/PWA viewer, keep the server running continuously:

```yaml
server:
  alwayson: true
```

Without `server.alwayson: true`, the server can shut down after the configured
one-shot lifetime or after the Inkplate has fetched `/calendar.png`. Docker
Compose and the example Docker config already use `server.alwayson: true`;
one-shot mode is mainly useful for scheduled Inkplate-only refresh workflows.

To use it like an app, open `/app` on the device and use the browser's install
or "Add to Home Screen" action. The client is intentionally simple: it caches
the app shell with a service worker, but always asks the server for the latest
calendar image.

### Google StaticMaps API

<img src="https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/b3f2efd0-23c0-4b9f-81e6-5684fc470ecc" width="800" />

In order to generate a static map of your area you will need to sign up to [Google's developer console](https://developers.google.com/):

1. Create a new project.
2. Go to Google Maps Platform → `Maps Static API` → `Enable`.
3. Go to `Credentials` → `Create Credentials` → `API Key`
4. After generating your API key, copy and update `google.apikey` in `config.yaml`
5. (Optional) add restriction to API Key and limit only to the `Maps Static API` service.

This will give us access to the Static Maps API service. In order to re-create the static map in the picture above, we first need to create a map style:

1. In Google Maps Platform → `Map styles` → `Create style`
2. To replicate the style used above, select `Import JSON` and paste the contents of [map-style.json](google/staticmaps/map-style.json.DEFAULT) into the text field. Google has changed this UI over time, so treat this file as a starting point if the import flow has moved or the rendered style differs.
3. Click `Save` and assign a name to the map style.

You can now use the map style to create a map ID that we can reference in our server:

1. In Google Maps Platform → `Map management` → `Create Map ID`.
2. Give the Map ID a name and make sure `map type` is set to `static`, then click `Save`.
3. Update the `associated map style` to the name of the map style created in the steps earlier.
4. Copy the `Map ID` and update the `google.staticmaps_mapid` field in `config.yaml`.

At startup the server fetches this static map, converts it to a dithered grayscale PNG under `server/views/html/map.png`, and then uses that local image in the rendered calendar page. The generated HTML does not embed your Google API key.

### Manual native server setup

Ensure Python3 is installed on your system
```
python3 --version
Python 3.11.2
```

Download project and install dependencies. The default main branch is the latest
stable branch. The next branch contains changes planned for the next release -
it should be OK to use, but may be less tested.
```
git clone https://github.com/OrangeSpyderMan/inkplate10-weather-cal
cd inkplate10-weather-cal
cp server/config/EXAMPLE_config.yaml server/config/config.yaml
python3 -m pip install -r server/requirements.txt
```

Edit `server/config/config.yaml` before starting the server. At minimum, set the weather provider, API keys, Google Static Maps Map ID, and location. Environment variable placeholders such as `${WEATHER_API_KEY}` are expanded at runtime.

By default the server looks for config in:

1. the file named by `INKPLATE_CONFIG_FILE`, when set
2. `server/config/config.yaml`
3. `server/config.yaml`

The `server/config/config.yaml` path is the preferred layout for new installs
because it can be mounted as a config directory without hiding application code.
The legacy `server/config.yaml` path remains supported for existing installs
for now, but it is deprecated and will be removed in a future release. Move
existing configs to `server/config/config.yaml` as soon as practical:

```bash
mkdir -p server/config
mv server/config.yaml server/config/config.yaml
```

Run the server manually:
```
python3 server/server.py
```

Run the server 9am each day:
```
crontab -e
```
Add this line:
```
0 9 * * * /usr/bin/python3 /path/to/inkplate10-weather-cal/server/server.py
```
`/path/to/inkplate10-weather-cal` should be updated to the absolute path of your checkout.

For a managed native service, prefer `./bin/install_server` unless you need to
repair individual systemd pieces manually.

## Running in Docker

The repository root contains the Dockerfile and `docker-compose.yml`. The image
installs Firefox, Geckodriver, and the required Python modules, then runs the
server as an unprivileged `inkplate` user.

### Configure

Create a Docker server config from the example:

```bash
cp server/config/EXAMPLE_config.yaml server/config/config.yaml
```

For Docker, keep `server.alwayson: true`. The compose file uses
`restart: unless-stopped`; one-shot server mode exits after serving or timing
out and would otherwise restart repeatedly.

Create a local `.env` file in the repository root. Docker Compose reads this
file and passes the values into the container:

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
WEATHER_API_KEY=...
GOOGLE_API_KEY=...
GOOGLE_STATICMAPS_MAPID=...

# Optional, only when current_temperature.source is netatmo
NETATMO_CLIENT_ID=
NETATMO_CLIENT_SECRET=
NETATMO_REFRESH_TOKEN=
NETATMO_DEVICE_ID=
NETATMO_MODULE_ID=
```

The compose file mounts `server/config` read-only and stores mutable
runtime data in the named `inkplate-data` volume. The Docker example sets the
Netatmo token file to `data/netatmo-token.json` so refreshed tokens survive
container replacement.

The published OCI images are:

```text
ghcr.io/orangespyderman/inkplate10-weather-cal:main
ghcr.io/orangespyderman/inkplate10-weather-cal:next
```

Use `:main` for stable deployments. Use `:next` to test changes before they are
promoted to `main`.

Do not commit `server/config/config.yaml`, deprecated legacy
`server/config.yaml`, or `.env`; keep API keys and refresh tokens in local
files or runtime environment variables.

Docker dependency updates are split across two mechanisms. Dependabot updates
the Python packages, GitHub Actions, and Docker base image on the `next` branch.
Geckodriver is pinned with the `GECKOVERSION` build argument, so a scheduled
GitHub Actions workflow checks Mozilla's latest release and opens a pull request
when that pin needs updating.

### Starting the container

Run from the root of your cloned repository:

```bash
docker compose up --build
```

Or detach it:

```bash
docker compose up --build -d
```

To use the published image instead of building locally, replace the Compose
service `build:` block with:

```yaml
image: ghcr.io/orangespyderman/inkplate10-weather-cal:main
```

The server listens on port `8080` and serves the generated image from:

```text
http://localhost:8080/calendar.png
```

The browser/PWA viewer is available from:

```text
http://localhost:8080/app
```

`localhost` is correct when testing from the Docker host. The Inkplate firmware
must use the host's LAN hostname or IP address in `calendar.url`.

If MQTT weather publishing is enabled, set `mqtt.host` in
`server/config/config.yaml` to a host that is reachable from inside the
container. On Docker Desktop, `host.docker.internal` usually points to the host.
On Linux, you may prefer to run the MQTT broker as another Compose service or
use the host's LAN IP.

There is a sample crontab called [docker-errorlog](docker-errorlog) that can be
used to check the Docker logs for ERROR messages. By default that runs every
hour, on the hour, but may need local tweaking.

## Running from a Published OCI Image

The image is a normal OCI image and can be run by Docker, Podman, or platforms
that consume OCI images directly.

Example Podman/Docker run:

```bash
podman run -d \
  --name inkplate-weather-cal \
  --env-file .env \
  -p 8080:8080 \
  -v ./server/config:/srv/inkplate/server/config:ro \
  -v inkplate-data:/srv/inkplate/server/data \
  ghcr.io/orangespyderman/inkplate10-weather-cal:main
```

Use `docker run` with the same arguments if you prefer Docker.

### Proxmox VE 9.1 OCI LXC

Proxmox VE 9.1 can create LXC containers from OCI images. This image should be
usable as an OCI source, but treat this as newer and less tested than Docker
Compose until it has been exercised on your Proxmox host.

Recommended image:

```text
ghcr.io/orangespyderman/inkplate10-weather-cal:main
```

Use the `:next` tag only when you want to test upcoming changes.

If the Proxmox host pulls directly from GHCR without registry credentials, make
the GitHub package public. Otherwise, configure registry credentials in Proxmox
before creating the container.

Configure the container with:

- environment variables from `.env.example`
- port `8080` exposed to your LAN
- network configured with `ip=dhcp` or a static IPv4 address if the container
  should be reachable over IPv4
- a read-only config directory mounted at `/srv/inkplate/server/config`, with
  the file available as `/srv/inkplate/server/config/config.yaml`
- persistent storage mounted at `/srv/inkplate/server/data`

Do not mount a directory over `/srv/inkplate/server`; that path contains the
application code copied into the image. Mount only the config directory and data
directory.

When using Proxmox host-managed DHCP, Proxmox starts the container and runs a
DHCP client inside the container namespace. The image includes Debian's
`isc-dhcp-client` package for this path. If the container still starts without a
usable IPv4 address, check the Proxmox network line includes `ip=dhcp`; a config
with only `ip6=dhcp` requests IPv6 DHCP but does not request an IPv4 lease.

The repository includes [config/EXAMPLE_config.yaml](config/EXAMPLE_config.yaml)
as a starting point for this directory-mount layout. Put the edited file on the
Proxmox host as `config.yaml`, then mount the containing directory read-only to
`/srv/inkplate/server/config`.

The generated PNG is served from:

```text
http://<container-ip-or-hostname>:8080/calendar.png
```

The browser/PWA viewer is served from:

```text
http://<container-ip-or-hostname>:8080/app
```

Known caveats:

- Proxmox OCI support is newer than standard Docker/Podman workflows.
- Proxmox mount and environment-variable management is not the same as Compose.
- Firefox/Geckodriver should be tested on the target Proxmox host.
- The renderer is tuned for Inkplate 10 portrait output at `825x1200`.

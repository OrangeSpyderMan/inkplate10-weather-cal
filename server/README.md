# Inkplate 10 Weather Calendar Server

A service for the weather calendar client written in Python3, backed by [Airium](https://pypi.org/project/airium/) and [Firefox](https://www.mozilla.org/firefox/).



Example 1                  | Example 2                 | Example 3
:-------------------------:|:-------------------------:|:-------------------------:
<img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/c37e6b65-a226-40d7-b1c7-cb3d72973054 width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/71958bcb-839d-447a-b671-a4cb5fbca25e width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/90608c9f-c16e-4d56-9edc-13b9d85ef659 width=300 />

<img width="1044" alt="Screenshot 2023-05-17 at 01 07 53" src="https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/e02e672b-7ad0-431d-8a29-c2740857a4d7">



- Uses [AccuWeather](https://developer.accuweather.com/) or [OpenWeatherMap](https://openweathermap.org/api) APIs for weather data.
- Uses Google's [StaticMaps API](https://developers.google.com/maps/documentation/maps-static/overview) to generate a static map of your area.
- Uses [Airium](https://pypi.org/project/airium/) then [Selenium](https://pypi.org/project/selenium/) / [Geckodriver](https://github.com/mozilla/geckodriver) / [Firefox](https://www.mozilla.org/firefox/) to generate HTML and save it as PNG files for image serving.
- Uses [Gunicorn](https://gunicorn.org/) and
  [Flask](https://flask.palletsprojects.com/en/2.3.x/) to serve images, the
  weather API, and a browser/PWA viewer independently from artifact generation.

The runtime has three roles:

- `server.py` retrieves weather, renders outputs, publishes MQTT data, and
  atomically updates the shared artifact directory.
- `web_server.py` launches Gunicorn, which serves only persisted artifacts and
  does not load weather providers or Selenium.
- `mqtt_diagnostics_server.py` independently subscribes to Inkplate diagnostic
  messages when that feature is enabled.

Docker Compose runs these roles as separate services sharing the
`inkplate-data` volume. Native installs use `inkplate-producer.service`,
`inkplate.service`, and `inkplate-diagnostics.service`. The standalone OCI
image supervises all three processes for single-container platforms such as
Proxmox.

Gunicorn defaults to one worker with two threads to keep the steady-state
memory footprint suitable for small systems. `INKPLATE_WEB_WORKERS` and
`INKPLATE_WEB_THREADS` can raise those values for larger deployments.

## Setup

### Recommended interactive install

From the repository root, run:

```bash
./bin/install_server
```

The installer walks through Docker Compose, Podman Compose, or native systemd
installation, and links to the dedicated experimental Proxmox VE 9 installer.
It uses the same defaults as the server code and example config: OpenWeatherMap
v3, port `8080`, six forecast slots, a three-hour refresh interval, `825x1200`
images, and MQTT disabled.

It prompts for the server bind IP and port, location, weather API key, Google
Static Maps API key, Google Static Maps Map ID, optional Netatmo credentials,
optional MQTT weather publishing, optional MQTT diagnostic listening, and
whether to start the service or container. Secrets are written outside
committed YAML:

- Docker or Podman: `.env` plus `server/config/config.yaml`
- systemd: `/etc/inkplate/weather.env`,
  `/srv/inkplate/server/config/config.yaml`, and `/srv/inkplate/inkplate_venv`

For Compose installs, `.env` remains owner-only (`0600`) because it contains
secrets. The generated YAML contains environment placeholders rather than
secret values and is written as `0644` so the non-root application user inside
either Docker or Podman can read the bind-mounted file.

Docker and Podman modes run as the current user. Docker expects `docker compose`
and daemon access. Podman expects either `podman compose` or `podman-compose`
and supports rootless operation. Podman automatically layers
`docker-compose.podman.yml` over the main Compose file so containers use the
Podman-supported `journald` log driver instead of Docker's `local` driver and
applies a private SELinux relabel (`:Z`) to the read-only config bind mount.
For both runtimes, the installer writes `INKPLATE_SERVER_PORT` to `.env`, and
Compose uses it for both the published host port and container target port.
Before building, the installer checks that this host port is free unless this
Compose project's web container is already running there. If another process
owns the port, choose a different Server port or stop the existing listener.
Native systemd mode needs root privileges for
package installation, `/srv/inkplate`, `/etc/inkplate/weather.env`,
Geckodriver, and systemd service management. Run it as root or as a user that
can elevate with `sudo`, `doas`, or `run0`; the installer checks this before
making system changes. Container modes validate their selected runtime before
starting.

Use dry-run mode to preview actions:

```bash
./bin/install_server --dry-run
```

### Remote installation

`bin/install_remote` can run the Proxmox or systemd installer on another host
over SSH without keeping a permanent repository checkout there:

```bash
./bin/install_remote root@pve1 --mode proxmox
./bin/install_remote admin@server1 --mode systemd
```

It uses normal SSH host-key verification, streams a temporary archive containing
only Git-tracked files, and never copies local `.env` or generated server
configuration. An answers file can be included as a protected `0600` file:

```bash
./bin/install_remote root@pve1 \
  --mode proxmox \
  --answers deployment.json \
  --non-interactive \
  --yes
```

Interactive runs allocate a remote TTY. Non-interactive systemd deployments
therefore need passwordless privilege elevation if the remote account is not
root. Proxmox deployment either logs in as root or uses `sudo`; interactive
runs can prompt for the sudo password, while `--non-interactive` requires
passwordless sudo. A successful run removes the temporary remote workspace; a
failed run retains it and prints the path. Use `--dry-run` for a connection-free
local preview, or `--remote-dry-run` to connect and exercise the target
installer's own dry-run checks.

For CI or repeatable testing, use the example JSON answers file:

```bash
./bin/install_server --dry-run --non-interactive --answers bin/install_server.answers.example.json
```

The example answers file contains placeholder secrets and is intended for
dry-run testing. Copy it and replace those values before using it for a real
non-interactive install.

Re-run the installer to update an existing install. It will detect existing
Docker, Podman, or systemd files and offer to update the application while
preserving config/secrets, update and reconfigure together, reconfigure
config/secrets only, or abort. Use the combined option when a release changes
both application code and configuration keys.

Logs:

```bash
docker compose logs -f
podman compose logs -f
sudo journalctl -u inkplate-producer -u inkplate -u inkplate-diagnostics -f
```

If Docker reports socket permission errors after adding a user to the `docker`
group, start a new login session or run `newgrp docker` before retrying.

### AccuWeather API

This provider follows AccuWeather's current Core Weather API contract, using
HTTPS and Bearer authentication. It is covered by mocked contract tests but is
not exercised against the live API in CI.
 
In order to obtain an API Key, you will need to:
1. Sign up to [developer.accuweather.com](https://developer.accuweather.com/).
2. Select a Core Weather API plan and generate an API key.

Make sure you update the config `weather.apikey` with your generated api key and update `weather.service` to `accuweather`.

### OpenWeatherMapv3 API

This is the API that has had the most testing.

In order to obtain an API Key, you will need to sign up to OpenWeatherMap and [generate an API key](https://home.openweathermap.org/api_keys).

The server samples the hourly forecast at local wall-clock boundaries. The
spacing is configured with `weather.forecastslicehours`, defaulting to 3, so
the default slots are midnight, 3am, 6am, 9am, and so on. Values that do not
divide evenly into 24 still use exact spacing across day boundaries; for
example, 7-hour slices move through the wall-clock hours over multiple days.
The number of forecast slots is configured with `weather.num_hourly_forecasts`
and is treated as an exact count. The example config uses 6, which is the
recommended maximum for the current Inkplate 10 portrait layout. Larger values
need layout tuning so the forecast row remains readable.

`weather.forecastleadminutes` defaults to 15. It shifts the forecast selection
cutoff forward before choosing slots, so an artifact rendered shortly before a
slice boundary does not show that nearly expired slot after the Inkplate wakes.

### OpenWeatherMapv4 API

One Call API 4.0 is available as an opt-in provider:

```yaml
weather:
  service: openweathermapv4
  apikey: ${WEATHER_API_KEY}
  num_hourly_forecasts: 6
  forecastslicehours: 3
  forecastleadminutes: 15
  metric: true
```

The provider uses the v4 current, one-day timeline, and one-hour timeline
endpoints. It follows hourly `next` links when necessary so the default six
three-hour forecast slots are complete. OpenWeather requires a separate
One Call by Call subscription for API 4.0; check its current pricing and free
allowance before enabling this provider.

When the v4 current response contains weather alert IDs, the display shows a
warning indicator and the normalized snapshot includes:

```json
{
  "current": {
    "alerts": {
      "active": true,
      "ids": ["alert-id"]
    }
  }
}
```

This is provider-specific optional data. It is available from
`GET /api/v1/weather`, the base MQTT weather topic, and the MQTT `/current`
topic. The IDs identify alerts active for the current record; alert detail
lookup is not currently performed.

OpenWeatherMap v3 remains the default while v4 output is compared on real
servers.

### Realtime conditions source

By default current conditions come from the configured forecast provider:

```yaml
current_conditions:
  source: weather
```

You can overlay realtime Netatmo measurements while keeping the configured
provider for icons, min/max temperature, and hourly forecasts:

```yaml
current_conditions:
  source: netatmo
  netatmo:
    client_id: ${NETATMO_CLIENT_ID:-}
    client_secret: ${NETATMO_CLIENT_SECRET:-}
    refresh_token: ${NETATMO_REFRESH_TOKEN:-}
    token_file: data/netatmo-token.json
    device_id: ${NETATMO_DEVICE_ID:-} # optional; omit to use the first station
    module_id: ${NETATMO_MODULE_ID:-} # optional temperature/humidity module
    wind_module_id: ${NETATMO_WIND_MODULE_ID:-} # optional wind gauge
    rain_module_id: ${NETATMO_RAIN_MODULE_ID:-} # optional rain gauge
```

The Netatmo integration uses the refresh token to request access tokens and
stores refreshed token data in `token_file`. Temperature and humidity are read
from `module_id`, or from the station when it is omitted. Optional wind and rain
modules add normalized wind speed, gust, direction, current rain, one-hour
rainfall, and 24-hour rainfall to the API and MQTT snapshot. The existing
`current_temperature` configuration key remains accepted for migration, but new
configurations should use `current_conditions`.

### Provider interfaces

Forecast providers implement the normalized `ForecastProvider` interface:

- `fetch() -> ForecastData`

Realtime providers implement `RealtimeProvider.get_current_conditions()` and
return a partial `CurrentConditions` overlay. The supported provider boundary
uses dataclasses for temperature, wind, rain, current conditions, hourly
forecasts, and the complete forecast. Renderers and external API clients still
receive the normalized schema `2.0` dictionary representation.

Providers are registered in
`weather/providers.py`, which keeps construction, supported names, and lazy
imports in one place. OpenWeatherMap v2 is no longer registered; supported
OpenWeatherMap providers are v3 and v4.

Normalized wind measurements use `value` and `unit`, with optional `gust` and
`direction`. Normalized rain measurements use `value` and `unit`, with optional
`last_hour` and `last_24_hours`.

### MQTT weather publishing

The server can optionally publish the same normalized weather snapshot used by
the renderer to MQTT. This is intended for lightweight local clients such as
ESP32 displays, Pico-class devices, Home Assistant, or Node-RED flows that need
weather data without talking directly to the weather provider APIs.

```yaml
mqtt:
  weather:
    enabled: true
    broker: localhost
    port: 1883
    base_topic: inkplate/weather-calendar
    retain: true
    qos: 0
  diagnostics:
    enabled: true
    broker: localhost
    port: 1883
    topic: inkplate/weather-calendar/diagnostics
    qos: 0
```

When enabled, the server publishes retained JSON payloads after a successful
weather refresh:

```text
inkplate/weather-calendar
inkplate/weather-calendar/generated_at
inkplate/weather-calendar/current
inkplate/weather-calendar/hourly
inkplate/weather-calendar/status
inkplate/weather-calendar/current/rain
inkplate/weather-calendar/current/wind
inkplate/weather-calendar/server/status
```

Publishing failures are logged but do not stop image generation or HTTP
serving. See [MQTT Weather and Diagnostics](../docs/mqtt.md) for broker setup,
payload details, topic examples, and example clients.

### HTTP API and outputs

Each successful weather retrieval is persisted and exposed through a versioned
JSON endpoint:

```text
GET /api/v1/weather
```

The response uses the same payload as the base MQTT weather topic and includes
`schema_version`. It provides `ETag` and `Last-Modified` validators. The
endpoint returns `503` until a snapshot is available. Provider-specific
optional fields are preserved; for example, OpenWeatherMap v4 active alert IDs
are exposed under `current.alerts`.

The current payload schema is `2.0`. Wind measurements use `wind.value`;
the OpenWeatherMap v4-specific `wind.real` field from schema `1.0` has been
removed.

Operational producer state is exposed separately:

```text
GET /api/v1/status
GET /status
```

The JSON endpoint reports refresh state and timestamps, provider names,
artifact readiness, MQTT publication state, runtime metadata, and the latest
sanitized error. The HTML dashboard polls that endpoint every 10 seconds.
Status data never includes provider credentials, tokens, or tracebacks.

Generated display artifacts use named output profiles:

```text
GET /outputs/inkplate10-portrait/calendar.png
```

Battery-powered clients can check a small content-hash manifest before
downloading an output:

```text
GET /api/v1/outputs/inkplate10-portrait/status
```

The response includes the output URL and a SHA-256 of its content. The hash
changes only when the rendered image bytes change, unlike timestamp-based HTTP
validators.

Output profiles are configured independently:

```yaml
outputs:
  default: inkplate10-portrait
  profiles:
    inkplate10-portrait:
      enabled: true
      renderer: firefox
      width: 825
      height: 1200
      filename: calendar.png
```

Each enabled profile has its own URL, dimensions, renderer, and filename.
Multiple profiles can be rendered in one producer cycle for different display
types. The `default` profile backs `/calendar.png` and `/app/calendar.png`.
Legacy `image.width` and `image.height` settings still configure the default
Inkplate 10 profile when `outputs.profiles` is absent.

Profiles may also contain an `options` mapping for renderer-specific layout or
style settings. Unknown options are left to the selected renderer.

Only the `firefox` renderer is implemented currently. The registry allows a
future `pillow` or device-specific renderer without changing artifact storage,
readiness, or HTTP routing.

The existing `/calendar.png` and `/app/calendar.png` routes remain compatibility
aliases for the same image. `/calendar.png` retains its attachment response for
existing Inkplate firmware.

Operational endpoints are:

```text
GET /api/v1/health
GET /api/v1/ready
GET /api/v1/outputs/<profile>/status
```

Health reports whether the Gunicorn web application is running. Readiness
returns `200` only when the snapshot and output signatures and SHA-256 hashes
match the completion marker written at the end of a successful producer cycle.
During an update or after an incomplete first cycle it returns `503`. Readiness
markers created before hashes were added are upgraded in place when their
recorded file signatures still match, so deployment does not require an
immediate successful weather-provider request.

The per-output status endpoint returns the completed output's SHA-256 and
generation time. Firmware can compare that hash with its retained value and
avoid downloading and driving an unchanged image to the e-paper panel.

Snapshots and rendered outputs use stable paths and are atomically replaced, so
the data directory does not accumulate historical versions. On startup, the
producer removes temporary artifact files older than 24 hours that may remain
after an interrupted write. Valid snapshots and outputs are never age-pruned.

The diagnostic listener runs as an independent process from weather production
and publishing. It records non-retained messages from the Inkplate through the
`MQTT` logger and resubscribes after reconnecting. Native installations use
`inkplate-diagnostics.service`; Compose uses the `diagnostics` service. When
diagnostics are disabled, that process exits successfully. The matching
firmware topic is configured in the Inkplate `mqtt_logger` section, either on
the SD card or in an embedded firmware configuration.

### Secrets

Config values can reference environment variables with `${VARIABLE_NAME}`. Use `${VARIABLE_NAME:-default}` when the value is optional or when a provider is configured but not currently selected.

This lets you keep committed YAML files free of secrets and inject sensitive values from the runtime environment. In GitHub Actions, store those values as repository or environment secrets and pass them to the relevant step with `env:`. For local Docker runs, use an ignored `.env` file or shell environment variables.

The expected runtime environment variable names are documented in
`.env.example` and listed here for container UIs that require values to be added
manually:

```text
WEATHER_API_KEY
GOOGLE_API_KEY
GOOGLE_STATICMAPS_MAPID
NETATMO_CLIENT_ID
NETATMO_CLIENT_SECRET
NETATMO_REFRESH_TOKEN
NETATMO_DEVICE_ID
NETATMO_MODULE_ID
NETATMO_WIND_MODULE_ID
NETATMO_RAIN_MODULE_ID
```

Empty runtime values do not satisfy required config placeholders such as
`${WEATHER_API_KEY}`. Required values must be provided by the runtime
environment. Optional placeholders such as `${NETATMO_CLIENT_ID:-}` continue to
expand to an empty string when unset.

### Output dimensions

Each profile's `width` and `height` values set its capture target. The current
Firefox HTML/CSS layout is tuned for Inkplate 10 portrait output at `825x1200`;
dimensions alone are not a general layout scaling system. A different device
size may require its own renderer or renderer `options` to avoid cropped,
stretched, or poorly spaced output.

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

The compatibility routes preserve their response behavior:

- `/calendar.png` keeps the existing attachment response.
- `/app/calendar.png` serves the same file inline for browsers and does not
  affect producer lifecycle. It follows the configured default output profile.
  Other clients should use a named output route when they require a specific
  profile.

For automatic weather refresh, keep the producer running continuously:

```yaml
server:
  alwayson: true
```

Without `server.alwayson: true`, the producer generates one complete artifact
set and exits. Gunicorn remains independent and continues serving the last
successful artifact set. Docker Compose and the example Docker config use
`server.alwayson: true`; one-shot producer mode is useful for scheduled refresh
workflows. `server.refreshminutes` is used only in always-on mode and must be
positive there. Existing `server.refreshhours` values remain supported as a
deprecated fallback, including fractional values such as `0.3`, but new and
reconfigured installations are written in minutes.

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

The native installation baseline is Debian 13 (Trixie) or another supported
distribution providing Python 3.13 or newer. Ensure Python 3 is installed:

```
python3 --version
Python 3.13.5
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

The `server/config/config.yaml` path can be mounted as a config directory
without hiding application code.

Run the producer and web service manually in separate terminals:

```bash
python3 server/server.py
python3 server/web_server.py
```

Run the server 9am each day:
```
crontab -e
```
Add this line:
```
0 9 * * * /usr/bin/python3 /path/to/inkplate10-weather-cal/server/server.py
```
This scheduled form expects `server.alwayson: false`; keep
`server/web_server.py` running separately. `/path/to/inkplate10-weather-cal`
should be updated to the absolute path of your checkout.

For a managed native service, prefer `./bin/install_server` unless you need to
repair individual systemd pieces manually.

## Running with Docker or Podman

The repository root contains the Dockerfile and a Compose file that works with
Docker Compose or Podman Compose. The image installs Firefox, Geckodriver,
Gunicorn, and the required Python modules, then runs as an unprivileged
`inkplate` user.

### Configure

Create a container server config from the example:

```bash
cp server/config/EXAMPLE_config.yaml server/config/config.yaml
```

For either Compose runtime, keep `server.alwayson: true`. The producer service
uses `restart: unless-stopped`, so a successful one-shot producer would
otherwise be started repeatedly.

Create a local `.env` file in the repository root. Compose reads this file and
passes the values into the container:

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
WEATHER_API_KEY=...
GOOGLE_API_KEY=...
GOOGLE_STATICMAPS_MAPID=...

# Optional, only when current_conditions.source is netatmo
NETATMO_CLIENT_ID=
NETATMO_CLIENT_SECRET=
NETATMO_REFRESH_TOKEN=
NETATMO_DEVICE_ID=
NETATMO_MODULE_ID=
NETATMO_WIND_MODULE_ID=
NETATMO_RAIN_MODULE_ID=
```

Compose runs separate `inkplate` web, `producer`, and `diagnostics` services.
They mount `server/config` read-only and share mutable runtime data in the named
`inkplate-data` volume. The Docker example sets the
Netatmo token file to `data/netatmo-token.json` so refreshed tokens survive
container replacement. Compose requires explicit values for
`WEATHER_API_KEY`, `GOOGLE_API_KEY`, and `GOOGLE_STATICMAPS_MAPID`.

### Container MQTT broker

If either MQTT feature is enabled, the server needs a reachable MQTT broker.
Configure `mqtt.weather.broker` and `mqtt.diagnostics.broker` independently; they
may point to the same broker.

For a simple local container broker, this repository includes an optional
Compose override. See [MQTT Weather and Diagnostics](../docs/mqtt.md) for the
broker command, matching server config, and security notes.

The published OCI images are:

```text
ghcr.io/orangespyderman/inkplate10-weather-cal:main
ghcr.io/orangespyderman/inkplate10-weather-cal:next
```

Use `:main` for stable deployments. Use `:next` to test changes before they are
promoted to `main`.

Do not commit `server/config/config.yaml` or `.env`; keep API keys and refresh
tokens in local files or runtime environment variables.

Docker dependency updates are split across two mechanisms. Dependabot updates
the Python packages, GitHub Actions, and Docker base image on the `next` branch.
Geckodriver is pinned with the `GECKOVERSION` build argument, so a scheduled
GitHub Actions workflow checks Mozilla's latest release and opens a pull request
when that pin needs updating.

### Starting the container

Run from the root of your cloned repository:

```bash
make version-manifest
docker compose up --build
# or
podman compose up --build
```

Or detach it:

```bash
make version-manifest
docker compose up --build -d
# or
podman compose up --build -d
```

To use the published image instead of building locally, change the shared
`image:` value and remove its `build:` block:

```yaml
image: ghcr.io/orangespyderman/inkplate10-weather-cal:main
```

The server defaults to all IPv4 interfaces on port `8080`:

```yaml
server:
  host: "0.0.0.0"
  port: 8080
```

Set `host` to a specific local IP to restrict the listener. Use `"::"` to bind
Gunicorn to IPv6; whether that socket also accepts IPv4 depends on the host's
dual-stack socket configuration. In Docker or Podman, this address is inside
the container, while the Compose `ports` mapping controls host exposure.

The generated image is served from:

```text
http://localhost:8080/calendar.png
```

The browser/PWA viewer is available from:

```text
http://localhost:8080/app
```

`localhost` is correct when testing from the container host. The Inkplate
firmware must use the host's LAN hostname or IP address in `calendar.url`.

Set each enabled MQTT broker to an address reachable from inside the container.
The installer defaults to `host.docker.internal` for Docker and
`host.containers.internal` for Podman. On Linux, you may prefer to run the MQTT
broker as another Compose service or use the host's LAN IP.

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

The repository includes an experimental fresh-install-only helper. Run it from
the repository checkout on the Proxmox host:

```bash
sudo ./bin/install_proxmox --dry-run
sudo ./bin/install_proxmox
```

It checks for Proxmox VE 9.x with `pve-container` 6.0.15 or newer, installs
`skopeo` with permission when needed, queries GHCR for the currently published
tags, resolves the selected image digest, creates an unprivileged DHCP-enabled
LXC with `pct`, installs the generated configuration through `pct push`, and
checks the readiness endpoint. Versioned release tags are offered first,
followed by `main` and `next`. Interactive runs list available LXC-capable
Proxmox storage and ask where to place the root filesystem. By default the
helper also offers separate Proxmox volumes for:

- `/srv/inkplate/server/data` as a read-write generated-data mount
- `/srv/inkplate/server/config` as a config/secrets mount that is made
  read-only after bootstrap

Use `--storage`, `--data-storage`, and `--config-storage` to choose these
stores non-interactively. Use `--no-separate-mounts` only if you deliberately
want config, secrets, and generated data to live on the CT root filesystem.

The helper deliberately refuses existing CTIDs and does not yet perform
upgrades, migration, backup, rollback, static network configuration, clustered
placement, or HA setup. Treat the created container as a technical preview and
test backup/restore before relying on it.

Recommended image:

```text
ghcr.io/orangespyderman/inkplate10-weather-cal:main
```

Use the `:next` tag only when you want to test upcoming changes.

The helper can pull a public GHCR package anonymously. For a private package,
authenticate on the Proxmox host with `skopeo login ghcr.io` before running the
helper. A manual Proxmox-managed pull may instead use registry credentials
configured in Proxmox.

For a manual OCI deployment instead of the helper, configure the container
with:

- environment variables from `.env.example`
- port `8080` exposed to your LAN
- network configured with `ip=dhcp` or a static IPv4 address if the container
  should be reachable over IPv4
- a read-only config directory mounted at `/srv/inkplate/server/config`, with
  the file available as `/srv/inkplate/server/config/config.yaml`
- persistent storage mounted at `/srv/inkplate/server/data`

Fill in at least `WEATHER_API_KEY`, `GOOGLE_API_KEY`, and
`GOOGLE_STATICMAPS_MAPID`; leave Netatmo values empty unless
`current_conditions.source` is set to `netatmo`. The supported variable names
are listed in `.env.example`.

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
- `bin/install_proxmox` currently supports fresh installations only.
- Proxmox mount and environment-variable management is not the same as Compose.
- Firefox/Geckodriver should be tested on the target Proxmox host.
- The renderer is tuned for Inkplate 10 portrait output at `825x1200`.

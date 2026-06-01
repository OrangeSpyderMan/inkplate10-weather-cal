# Inkplate 10 Weather Calendar Server

A service for the weather calendar client written in Python3, backed by [Airium](https://pypi.org/project/airium/) and [Firefox](https://www.mozilla.org/firefox/).



Example 1                  | Example 2                 | Example 3
:-------------------------:|:-------------------------:|:-------------------------:
<img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/c37e6b65-a226-40d7-b1c7-cb3d72973054 width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/71958bcb-839d-447a-b671-a4cb5fbca25e width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/90608c9f-c16e-4d56-9edc-13b9d85ef659 width=300 />

<img width="1044" alt="Screenshot 2023-05-17 at 01 07 53" src="https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/e02e672b-7ad0-431d-8a29-c2740857a4d7">



- Uses [AccuWeather](https://developer.accuweather.com/) or [OpenWeatherMap](https://openweathermap.org/api) APIs for weather data.
- Uses Google's [StaticMaps API](https://developers.google.com/maps/documentation/maps-static/overview) to generate a static map of your area.
- Uses [Airium](https://pypi.org/project/airium/) then [Selenium](https://pypi.org/project/selenium/) / [Geckodriver](https://github.com/mozilla/geckodriver) / [Firefox](https://www.mozilla.org/firefox/) to generate HTML and save it as PNG files for image serving.
- Uses [Flask](https://flask.palletsprojects.com/en/2.3.x/) to serve images.

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
Static Maps Map ID, optional Netatmo credentials, optional MQTT logging, and
whether to start the service or container. Secrets are written outside committed
YAML:

- Docker: `.env` plus `server/config.yaml`
- systemd: `/etc/inkplate/env`, `/srv/inkplate/server/config.yaml`, and
  `/srv/inkplate/inkplate_venv`

Use dry-run mode to preview actions:

```bash
./bin/install_server --dry-run
```

Re-run the installer to update an existing install. It will detect existing
Docker or systemd files and offer to update the application while preserving
config/secrets, reconfigure config/secrets, or abort.

Logs:

```bash
docker compose logs -f
sudo journalctl -u inkplate -f
```

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

### Secrets

Config values can reference environment variables with `${VARIABLE_NAME}`. Use `${VARIABLE_NAME:-default}` when the value is optional or when a provider is configured but not currently selected.

This lets you keep committed YAML files free of secrets and inject sensitive values from the runtime environment. In GitHub Actions, store those values as repository or environment secrets and pass them to the relevant step with `env:`. For local Docker runs, use an ignored `.env` file or shell environment variables.

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
cp server/EXAMPLE_config.yaml server/config.yaml
python3 -m pip install -r server/requirements.txt
```

Edit `server/config.yaml` before starting the server. At minimum, set the weather provider, API keys, Google Static Maps Map ID, and location. Environment variable placeholders such as `${WEATHER_API_KEY}` are expanded at runtime.

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
cp server/EXAMPLE_docker_config.yaml server/config.yaml
```

For Docker, keep `server.alwayson: true`. The compose file uses
`restart: unless-stopped`; one-shot server mode exits after serving or timing
out and would otherwise restart repeatedly.

Create a local `.env` file in the repository root. Docker Compose reads this
file and passes the values into the container:

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

The compose file mounts `server/config.yaml` read-only and stores mutable
runtime data in the named `inkplate-data` volume. The Docker example sets the
Netatmo token file to `data/netatmo-token.json` so refreshed tokens survive
container replacement.

Do not commit `server/config.yaml` or `.env`; keep API keys and refresh tokens
in local files or runtime environment variables.

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

The server listens on port `8080` and serves the generated image from:

```text
http://localhost:8080/calendar.png
```

`localhost` is correct when testing from the Docker host. The Inkplate firmware
must use the host's LAN hostname or IP address in `calendar.url`.

If MQTT logging is enabled, set `mqtt.host` in `server/config.yaml` to a host
that is reachable from inside the container. On Docker Desktop,
`host.docker.internal` usually points to the host. On Linux, you may prefer to
run the MQTT broker as another Compose service or use the host's LAN IP.

There is a sample crontab called [docker-errorlog](docker-errorlog) that can be
used to check the Docker logs for ERROR messages. By default that runs every
hour, on the hour, but may need local tweaking.

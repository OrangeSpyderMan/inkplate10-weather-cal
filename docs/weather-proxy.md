# Running WeatherProxy

WeatherProxy is an optional service on the `feature/weather-proxy` branch for
4.2. It shares the `weather` Python package with the calendar and supplies
forecasts and observations for independently configured locations. Direct
calendar provider configurations remain supported.

## Configuration

Start with [weather-proxy.example.yaml](../weather-proxy.example.yaml). Configure
coordinates, IANA timezone, and stable ID for each location. Account IDs refer
to credentials and a shared request budget; location IDs identify public feeds.
Supported upstream providers are `openweathermapv3`, `openweathermapv4`,
`accuweather`, and `netatmo`.

Keep secrets in environment variables or a mode-0600 `weather.env` alongside the
installed config. The environment file supports literal `KEY=value`, JSON double
quoted values, and single quoted values; it is never executed as shell code.
The runtime reads `/config/weather.env` by default. Set `WEATHER_PROXY_ENV_FILE`
for a different path. Existing environment variables take precedence.

Each forecast has its own `refresh_minutes`, `stale_minutes`, and `num_hours`.
OWM defaults to 24 hourly slots, AccuWeather to its 12-hour horizon. Each Netatmo
account has a `reconcile_minutes` interval (default 30 minutes). Events can prompt
an earlier fetch, at most once per `event_min_seconds` (default 60 seconds).
Per-account hourly and ten-second request budgets cover every actual HTTP call,
including authentication and location lookup. These are operator-selected
budgets, not assertions about the provider's allowance; tune them for your plan
and number of locations. Provider 429 responses trigger account-wide cooldown.

## Storage

SQLite is the default and currently the only backend:

```yaml
storage:
  backend: sqlite
  path: /data/weather-proxy.sqlite3
```

Use a persistent **local** filesystem, not NFS, for the database and its WAL.
The native installer sets `WEATHER_PROXY_STORAGE_PATH` to its persistent
`data/weather-proxy.sqlite3`, overriding the YAML path. For manual native runs,
relative storage paths resolve against the configuration directory.
The systemd service can write only its data directory. The database contains
OAuth tokens as well as cached readings and must remain private. It is created
with mode 0600. Back up a running database with SQLite's backup facility; copying
only the primary file while WAL writes are in progress is not a consistent backup.

The storage protocol in `server/weather_proxy/storage.py` separates documents,
atomic revisions, durable event deduplication, worker leases, and quota
reservations from SQL. A future cloud implementation must preserve those
semantics using transactions or conditional writes. It does not need shared
filesystem access. Only the worker writes weather state; web processes write the
inbox. A renewable lease prevents concurrent workers from refreshing the same
deployment. Use a separate store and MQTT client ID for independent deployments.

## Installers

The component installer accepts a complete multi-location config and optional
environment file. It preserves existing config, secrets, and data during updates;
`--replace-config` explicitly replaces config/secrets and keeps private `.bak`
copies. It does not log secret values. Preview with `--dry-run`.

```sh
python3 bin/install_server.py --component weather-proxy --mode docker \
  --config weather-proxy.yaml --env-file weather.env --build --start

python3 bin/install_weather_proxy.py --mode podman \
  --config weather-proxy.yaml --env-file weather.env --build --start

sudo python3 bin/install_weather_proxy.py --mode systemd \
  --config weather-proxy-native.yaml --env-file weather.env --start

# Proxmox VE 9 native OCI/LXC deployment (experimental, fresh CT only):
python3 bin/install_weather_proxy.py --mode proxmox --config weather-proxy.yaml \
  --env-file weather.env --ctid 123 --storage local-lvm --tag v4.2.0
```

Docker and Podman installs generate a Compose file with a read-only config mount
and persistent host data directory. They use a matching unprivileged runtime UID;
rootless Podman uses `keep-id`. Podman mounts include SELinux relabeling. Omit
`--build` to pull `ghcr.io/orangespyderman/weatherproxy:next`, or select a released
tag/digest with `--image`. These images will become available after the branch is
merged and publication runs. `--port` controls the host mapping; container configs
should bind to port 8090 on `0.0.0.0`.

Native installs require Python 3.11+, venv support, systemd, root, and network
access for dependencies. They install the standalone Python package, create the
`weatherproxy` user, and install separate `weather-proxy-web` and
`weather-proxy-worker` units. Inspect them with `journalctl -u weather-proxy-web
-u weather-proxy-worker`. Config must bind to an unprivileged port.

The image is also an OCI application with `weather-proxy serve` as its default
process, supervising web and worker children and handling shutdown signals.
Prepare a portable OCI archive with:

```sh
python3 bin/install_weather_proxy.py --mode oci --config weather-proxy.yaml \
  --env-file weather.env --image ghcr.io/orangespyderman/weatherproxy:v4.2.0
```

This mode uses Podman to export an OCI archive and writes mount metadata; it
does not provision a host. An OCI host must provide `/config/weather-proxy.yaml`,
optional `/config/weather.env`, persistent writable `/data`, and suitable
ownership for image UID/GID 10001. The archive includes the application runtime.
Keep config and data mounts separate when updating the image. Never run multiple
writer deployments against an NFS-mounted SQLite file.

The Proxmox mode reuses the existing Python OCI/LXC installer with the proxy
image, entry point, user, config/data mounts, and health probe. It accepts the
complete config instead of asking for calendar/Google credentials. The existing
shell `bin/deploy_proxmox_oci` remains a calendar deployment tool; use the component
installer above for a standalone proxy.

For a manual Compose deployment:

```sh
docker compose -f docker-compose.weatherproxy.yml up -d --build
```

Or install a wheel and use the separate entry points:

```sh
pip install inkplate_weather_proxy-4.2.0-py3-none-any.whl
weather-proxy check --config weather-proxy.yaml
weather-proxy serve --config weather-proxy.yaml
# Separate processes, useful for systemd and independently managed containers:
weather-proxy web --config weather-proxy.yaml
weather-proxy worker --config weather-proxy.yaml
```

## Netatmo setup

Configure the account's `user_id` and the station/module IDs on each location.
Authorize the Netatmo application with `read_station`. Expose only
`POST /api/v1/integrations/netatmo/webhook` through your public HTTPS reverse
proxy. Do not put an interactive login in front of that route; it authenticates
the raw body using Netatmo's HMAC signature. Other API routes can require the
configured `api_token` as a Bearer token.

Stop the worker before inspecting/changing registrations, since registration
may refresh its OAuth tokens. Inspect first, then append the desired URL:

```sh
weather-proxy webhooks --account netatmo --config weather-proxy.yaml
weather-proxy register-webhook --account netatmo --config weather-proxy.yaml \
  --url https://weather.example.net/api/v1/integrations/netatmo/webhook
```

Restart the worker afterward. Registration uses `/webhooks/v1/` and does not
replace or delete other integrations' registrations. Automatic developer-portal
registration is an alternative, with Netatmo's reauthorization requirements.

The initial implementation treats signed device/topology events as durable
cache invalidations, coalescing a station-data fetch. It **does not guess a
weather measurement payload from the control-device examples** in the upstream
guide. Polling distributes one account response across all configured stations
and preserves original per-module measurement timestamps. A live station capture
is still needed before adding direct weather delta ingestion. No live station
credentials or callback registration are required for automated tests.

## API and MQTT

```text
GET /api/v1/locations
GET /api/v1/locations/landry/weather?units=metric
GET /api/v1/locations/landry/observations?units=imperial
GET /api/v1/health
GET /api/v1/status
```

Locations returns `{ "locations": [...] }` with ID, name, coordinates, timezone.
An empty configuration returns an empty list. Weather and observations return
404 for unknown locations and 503 while a configured location awaits data.
All reads use cached state, support conditional ETags, and make no upstream calls.
`units` accepts `metric` or `imperial`; MQTT currently publishes metric state.

Weather schema **2.1** retains the calendar's `current` and `hourly` shapes and
adds `location`, `revision`, `forecast`, `measurement_metadata`, and
`observations`. `generated_at` is assembly time. Forecast freshness is based on
`forecast.fetched_at`; module measurements carry separate `observed_at`,
`received_at`, and `stale`. Measurement metadata identifies forecast fallback
versus an observation. A stopped worker cannot make old HTTP readings look live.
Revisions advance on state changes, including freshness transitions, rather than
on each client read. A forecast outage does not block observation publication.

With MQTT enabled, the configurable root defaults to `weatherproxy`:

```text
weatherproxy/availability
weatherproxy/locations/landry/weather
weatherproxy/locations/landry/current
weatherproxy/locations/landry/observations
weatherproxy/locations/landry/status
```

Location topics contain complete retained JSON state, with QoS 1 by default.
The current topic wraps `current` with revision, location, units and freshness.
Use the full weather topic for existing snapshot consumers. Reconnect republishes
state without changing measurement times. Topic deliveries are not atomic as a
group; use a single complete message or correlate revisions. Removed locations
have retained messages cleared once the broker is reachable. Availability reports
proxy broker connectivity; it does not promise fresh upstream data.

## Calendar and display integration

Select WeatherProxy in the existing calendar installer, or configure:

```yaml
weather:
  service: weatherproxy
  url: http://weather-proxy:8090
  location: landry
  token: ${WEATHER_PROXY_TOKEN:-}
  metric: true
  num_hourly_forecasts: 6
  forecastslicehours: 3
  forecastleadminutes: 15
current_conditions:
  source: weather
location: Landry, FR
```

The top-level calendar location remains accepted for compatibility; map
coordinates come from proxy location metadata. The adapter selects calendar
forecast slots, rejects stale or insufficient forecasts, and never silently
falls back to a direct upstream call. Supply enough OWM hours for the calendar's
desired horizon. AccuWeather retains its existing evenly sampled 12-hour behavior.

The calendar web service forwards location discovery and weather reads under its
own origin. The PWA shows a location picker and live conditions, polling JSON
every 30 seconds while visible. It continues showing the calendar's configured
image; changing the live-panel location does not reconfigure or rerender that
calendar image. Browser background push and SSE are not implemented.

Point the Pico example's `MQTT_BASE_TOPIC` at
`weatherproxy/locations/landry/weather`. It retains the existing calendar-topic
option, adds keepalive/reconnect, and avoids identical redraws. Source observation
times and stale flags are displayed; a local timeout also detects a feed that
stops changing. Retained data can already be old at boot, so clients needing
precise age while the proxy is offline should synchronize their own clock and
compare measurement timestamps. Sleeping Inkplates keep their current lifecycle.

## Build and release assets

`WeatherProxy CI` builds and smoke-tests the isolated image and runs proxy tests
without calendar rendering dependencies. It builds wheel/sdist artifacts and
checks installed provider imports and packaged icon maps outside the checkout.
The existing calendar CI continues to exercise direct-provider compatibility
and installer tests.

`Publish Container Image` publishes both calendar and WeatherProxy images for
`linux/amd64` and `linux/arm64`, plus Python wheel/source artifacts. Release runs
attach the Python assets and a deployment bundle (installers, source, config
examples, and documentation) to the existing GitHub Release. `Publish Release`
continues explicitly dispatching the firmware and container workflows; creating
a release with `GITHUB_TOKEN` alone is not relied on to trigger these builds.

For a local distribution build:

```sh
python3 -m pip install build packaging
python3 bin/build_weather_proxy.py --version 4.2.0
```

Build staging happens in a temporary directory, leaving the checkout version
unchanged. See [the design](weather-proxy-design.md) for the architecture and
remaining validation against a real Netatmo station.

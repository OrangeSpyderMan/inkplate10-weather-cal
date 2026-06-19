# MQTT Weather and Diagnostics

MQTT supports two independent features:

- the server publishes normalized weather data for local clients
- the Inkplate firmware publishes diagnostic logs for the server to record

Good MQTT consumers include:

- Pico W weather displays
- ESP32 displays
- Home Assistant automations
- Node-RED flows
- local dashboards

Neither feature is required for the Inkplate to download the rendered PNG from
`/calendar.png`.

## Server Configuration

Enable MQTT publishing in `server/config/config.yaml`:

```yaml
mqtt:
  weather:
    enabled: true
    broker: mqtt
    port: 1883
    base_topic: inkplate/weather-calendar
    retain: true
    qos: 0
  diagnostics:
    enabled: true
    broker: mqtt
    port: 1883
    topic: inkplate/weather-calendar/diagnostics
    qos: 0
```

The server publishes after each successful weather refresh. The diagnostic
listener subscribes when it connects and subscribes again after reconnecting.
MQTT failures do not stop image rendering or HTTP serving.

Optional realtime-provider fields are included in the retained `/current`
payload. Netatmo wind data uses `value`, `unit`, and optional `gust` and
`direction`; rain data uses `value`, `unit`, and optional `last_hour` and
`last_24_hours`.

The same normalized measurements are also published on dedicated retained
topics for clients that do not need the complete current-conditions payload.
When a measurement is no longer available, the server clears its retained
dedicated topic.

Enable diagnostic publishing in the Inkplate configuration, either the SD-card
`config.yaml` or the YAML passed to `make firmware-upload CONFIG=...`:

```yaml
mqtt_logger:
  enabled: true
  debug: false
  broker: mqtt.example.net
  port: 1883
  clientId: inkplate10-weather-cal
  topic: inkplate/weather-calendar/diagnostics
  retries: 3
```

Diagnostic messages are not retained. If the broker cannot be reached, the
firmware continues with serial-only logging.

`mqtt_logger.debug` defaults to `false`. Normal MQTT output contains the tagged
`WAKE`, `BATTERY`, and `REFRESH` lifecycle events, plus all warnings and errors.
For example:

```text
2026-06-08T17:14:38+02:00 - WAKE - cause=timer firmware=v3.1.0
2026-06-08T17:14:38+02:00 - BATTERY - voltage=4.26V
2026-06-08T17:14:45+02:00 - REFRESH - status=ready
```

`REFRESH status=ready` means that the image has been downloaded and decoded.
The firmware then acknowledges outstanding MQTT messages and turns off the
network before driving the e-paper panel.

Set `debug: true` to also publish detailed connection, timing, retry, and sleep
messages. Serial logging remains verbose in both modes.

## Example Broker

For a simple local lab broker, use the included Mosquitto Compose override:

```bash
docker compose -f docker-compose.yml -f docker-compose.mqtt.yml up -d
```

When using that override, set:

```yaml
mqtt:
  weather:
    enabled: true
    broker: mqtt
    port: 1883
    base_topic: inkplate/weather-calendar
    retain: true
    qos: 0
  diagnostics:
    enabled: true
    broker: mqtt
    port: 1883
    topic: inkplate/weather-calendar/diagnostics
    qos: 0
```

The included broker listens on port `1883`, allows anonymous access, and stores
retained messages in the `mqtt-data` Docker volume. That is convenient for a
trusted LAN or development setup. Use an authenticated broker configuration
before exposing MQTT beyond your local network.

To inspect retained messages from the host, install Mosquitto clients and run:

```bash
mosquitto_sub -h localhost -t 'inkplate/weather-calendar/#' -v
```

## IPv6

MQTT itself is transport-neutral. In this project, IPv6 support depends on the
specific publisher, broker, client, and container network path.

The Python weather publisher and diagnostic listener use Paho MQTT and pass
their configured `broker` values directly to the operating system socket stack.
They should work with IPv6-capable hostnames or IPv6 literals when the server
host/container can route to the broker.

Use one of these forms in `server/config/config.yaml`:

```yaml
mqtt:
  weather:
    broker: mqtt.example.net
```

or:

```yaml
mqtt:
  diagnostics:
    broker: "2001:db8::10"
```

Do not use URL syntax for either broker field. In particular, do not include
`mqtt://` and do not use bracketed URL literals such as `[2001:db8::10]`.

The included Mosquitto config uses:

```text
listener 1883
```

Mosquitto can listen on IPv6, but Docker port publishing and bridge networking
must also be configured for IPv6 on the host. If you run the included broker as
a Compose service and the weather server connects to `broker: mqtt` from the same
Compose network, Docker's internal service networking is usually enough. If a
Pico, phone, Home Assistant, or another LAN device connects to the broker over
IPv6, verify that the Docker host publishes port `1883` on IPv6 and that local
firewall rules allow it.

The 52Pi EP-0164 Pico example is currently documented and tested as an
IPv4-first client. MicroPython networking support is port/version dependent, and
the RP2/Pico W quick reference documents IPv4 connection details with
`wlan.ipconfig('addr4')`. Treat Pico W IPv6 MQTT as unverified until tested on
the exact MicroPython build and network.

## Topics

With the default `base_topic`, the server publishes these retained JSON
messages:

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

Inkplate diagnostics use a separate, non-retained topic:

```text
inkplate/weather-calendar/diagnostics
```

### `inkplate/weather-calendar`

Full snapshot:

```json
{
  "schema_version": "2.0",
  "generated_at": "2026-06-04T09:00:00+00:00",
  "source": "openweathermapv3",
  "units": "metric",
  "current": {
    "icon": "icon/cloudy.png",
    "alerts": {
      "active": true,
      "ids": ["alert-id"]
    },
    "temperature": {
      "unit": "\u00b0C",
      "value": 11,
      "min": 8,
      "max": 15
    }
  },
  "hourly": [
    {
      "dt": "2026-06-04T10:00:00+00:00",
      "icon": "icon/rainy.png",
      "temperature": {
        "unit": "\u00b0C",
        "value": 18
      },
      "wind": {
        "unit": "m/s",
        "value": 4.2
      },
      "rain_probability": 100
    }
  ]
}
```

The exact weather fields can vary slightly by provider, but `current`,
`hourly`, `temperature`, `icon`, and `rain_probability` are the fields intended
for lightweight display clients. With OpenWeatherMap v4, `current.alerts` is
also present. Its `active` value indicates whether the current record contains
alert IDs, and `ids` contains those OpenWeather alert identifiers. Other
providers may omit this field.

Schema `2.0` standardizes normalized wind measurements on `wind.value`.
Schema `1.0` clients that consumed the OpenWeatherMap v4-specific `wind.real`
field must migrate to `wind.value`.

The same canonical payload is available over HTTP at `/api/v1/weather`.

### `inkplate/weather-calendar/current`

Current conditions only. This is the simplest topic for character LCDs or
single-screen microcontroller displays. For OpenWeatherMap v4 this includes the
optional `alerts` object described above.

### `inkplate/weather-calendar/generated_at`

The retained snapshot generation time as a JSON string:

```json
"2026-06-04T09:00:00+00:00"
```

This duplicates the `generated_at` field in the full snapshot and status
message so small clients can subscribe to the refresh time without parsing a
larger payload. Dedicated measurement payloads retain their existing shapes.

### `inkplate/weather-calendar/hourly`

Hourly forecast list. This is useful for displays that can show a short forecast
strip or cycle through several upcoming periods.

### `inkplate/weather-calendar/status`

Metadata only:

```json
{
  "generated_at": "2026-06-04T09:00:00+00:00",
  "source": "openweathermapv3",
  "units": "metric"
}
```

This existing topic remains weather-snapshot metadata. Operational server
state is published separately at
`inkplate/weather-calendar/server/status`.

### `inkplate/weather-calendar/current/rain`

Current rain measurement only. A live Netatmo value uses an instantaneous
rate; weather-provider fallback based on the previous hour is marked as an
average:

```json
{
  "unit": "mm",
  "value": 0.4,
  "last_hour": 1.2,
  "rate_unit": "mm/h",
  "rate_basis": "instantaneous",
  "source": "netatmo",
  "live": true
}
```

### `inkplate/weather-calendar/current/wind`

Current wind measurement only:

```json
{
  "unit": "kmh",
  "value": 18,
  "gust": 27,
  "direction": 245,
  "direction_cardinal": "WSW",
  "source": "netatmo",
  "live": true
}
```

`direction` follows meteorological convention and identifies where the wind
comes from.

### `inkplate/weather-calendar/server/status`

Retained operational state published when the producer starts a refresh,
completes, fails, or schedules its next refresh. The schema includes runtime
metadata, provider names, artifact readiness, refresh timestamps, MQTT state,
and the latest sanitized producer error. It does not contain API keys, tokens,
broker credentials, or tracebacks. The canonical HTTP representation is
available at `/api/v1/status`. Timestamps are ISO 8601 values with explicit UTC
offsets; the browser dashboard converts them to its local timezone and labels
that timezone.

## Icons

The weather payload currently carries the same icon path used by the HTML
renderer, for example:

```json
{
  "icon": "icon/day/clear.png"
}
```

For MQTT clients, treat this path as an icon identifier. A small display can map
it to:

- simple locally drawn shapes
- local bitmap assets stored on the device
- short text such as `Rain`, `Cloud`, or `Clear`

Do not assume the MQTT message contains binary icon artwork. If we later want
server-managed icon assets for MQTT-only clients, the cleaner design is to
publish retained icon assets on separate topics and reference those topics from
the weather payload.

## Example Clients

- [52Pi EP-0164 Pico W MQTT weather display](../examples/pico-ep0164-mqtt-weather)

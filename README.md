# Inkplate 10 Weather Calendar

Display today's date, weather forecast and a stylised map of your area using an Inkplate 10 and a small server.

<img src=https://user-images.githubusercontent.com/5797356/223708925-131d7ecc-5e95-453a-b687-427b75d959dd.jpg width=800 />

- [Background](#background)
- [How it Works](#how-it-works)
- [Bill of Materials](#bill-of-materials)
- [Setup](#setup)
- [MQTT](#mqtt)
- [Server Installation](#server-installation)
- [Firmware](#firmware)
  - [Building with Arduino CLI](#building-with-arduino-cli)
  - [Building with Arduino IDE](#building-with-arduino-ide)
- [License](#license)

## Background

I was looking for a weather station for my home and came across [Chris Twomey's Inkplate Weather Calendar](https://github.com/chrisjtwomey/inkplate10-weather-cal). This project began as a fork of that work, but has since diverged into a separately maintained project with a stronger focus on Docker-based server deployment, repeatable firmware builds, published container images, installer tooling, and display/server tweaks. The original concept remains Chris' work, and the repository history and license attribution preserve that origin.

## How it Works

Both a server and client are required. The main workload is on the server, which allows the client to save power by not generating the image itself. The client only needs WiFi access to the server-hosted PNG.

<img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/ff903fe3-4576-41d1-92b5-3a374242759a width=800 />

### Client (Inkplate 10)

1. Wakes from deep sleep and attempts to connect to WiFi.
2. Optionally connects to MQTT to publish diagnostic logs.
3. Uses the RTC for timekeeping and refreshes it from NTP when synchronization
   is due.
4. Optionally checks the output status and downloads the server-hosted PNG only
   when its content has changed.
5. Returns to deep sleep for the configured refresh interval.

#### Features:

- Ultra-low power consumption:
  - approx 21µA in deep sleep
  - approx 240mA awake
  - approx 30 seconds awake time daily
- Real-time clock is normally synchronized from NTP at most once every 24
  hours; the retained RTC is used between synchronizations.
- Daylight savings time handled automatically.
- Battery voltage is checked before WiFi starts; low readings are confirmed
  before a refresh is skipped.
- Can publish diagnostic logs to MQTT while retaining serial output.
- Renders messages on the e-ink display for critical errors (eg. battery low, wifi connect timeout etc.).
- Supports SD-card configuration or configuration embedded during compilation.

### Server (Raspberry Pi)

1. Gets any relevant new data (ie. weather, maps).
2. Generates a HTML file using a Python HTML translator [Airium](https://pypi.org/project/airium/).
3. [Selenium](https://pypi.org/project/selenium/) then uses [Geckodriver](https://github.com/mozilla/geckodriver) to make [Firefox](https://www.mozilla.org/firefox/) capture the generated HTML file as a PNG screenshot that fits the dimensions of e-ink resolution.
4. A separate Gunicorn/Flask web process serves the generated PNG and weather
   API from the shared artifact directory.
5. (Optional) Separate server processes publish weather snapshots and listen
   for Inkplate diagnostics over MQTT.
6. The producer either refreshes indefinitely or generates one artifact set and
   exits; web serving has an independent lifecycle.
7. A cronjob can run the producer before the client's configured refresh interval elapses.

#### Features:

See the [server](/server) for more features.

## Bill of Materials

- **Inkplate 10 by Soldered Electronics ~€150**

  The [Inkplate 10](https://www.crowdsupply.com/soldered/inkplate-10) is an all-in-one hardware solution for something like this. It has a 9.7" 1200x825 display with integrated ESP32, real-time clock, and battery power management. You can get it either [directly from Soldered Electronics](https://soldered.com/product/soldered-inkplate-10-9-7-e-paper-board-with-enclosure-copy) or from a [UK reseller like Pimoroni](https://shop.pimoroni.com/products/inkplate-10-9-7-e-paper-display?variant=39959293591635). While it might seem pricey at first glance, a [similarly sized raw display from Waveshare](https://www.amazon.co.uk/Waveshare-Parallel-Resolution-Industrial-Instrument/dp/B07JG4SXBV) can cost the same or likely more, and you would still need to source the microcontroller, RTC, and BMS yourself.

- **Optional: 2 GB microSD card ~€5**

  A card is only required for the generic firmware build, which reads
  `config.yaml` from its root. An embedded-config build does not initialize or
  use the SD card.

- **3000mAh LiPo battery pack ~€10**

  Any Lithium-Ion/Polymer battery will do as long as it has a compatible JST
  connector for the Inkplate board. A 3000mAh battery should provide battery
  life measured in months, but actual runtime depends heavily on the refresh
  interval, WiFi connection time and retries, battery condition, and
  self-discharge. The client's typical deep-sleep and awake current figures are
  listed under [Client features](#features). Here is
  [the battery I used](https://cdn-shop.adafruit.com/datasheets/LiIon2000mAh37V.pdf).

- **CR2032 3V coin cell ~€1**

  In order to power the real-time clock for when the board needs to deep sleep. Should be easily-obtainable in any hardware or home store.

- **Raspberry Pi Zero W ~€40**

  To run the server, you will need something that can run Python 3 and Firefox/Geckodriver. The producer does the heavier PNG rendering work while the Gunicorn web process remains available independently. The Docker image currently targets `amd64` and `arm64`, so it is a better fit for a 64-bit Raspberry Pi or similar SBC. A 32-bit Raspberry Pi Zero W may need a native/manual setup rather than the supplied container.

- **Black photo frame 8"x10" ~€10**

  This might be the trickiest part to source, as the card insert (also called the 'mount') needs to fit the 8"x10" frame but fit a photo closer in dimension to 5.5"x7.5" in order for just the e-ink part of the board to be in-frame. The inkplate I bought came with a 3D printed case that looks good enough, and has ports in the right places for charging/SD card access etc and a handy (but a flaky..) on/off switch.

## Setup

The generic release firmware reads `config.yaml` from the root of an SD card:

```
display:
  # 0, 1, 2, or 3; each step rotates the display clockwise by 90 degrees.
  rotation: 1
calendar:
  url: http://<server-host>:8080/calendar.png
  status_url: http://<server-host>:8080/api/v1/outputs/inkplate10-portrait/status
  refresh_interval: 3
  retries: 1
  retry_interval_minutes: 15
wifi:
  ssid: XXXX
  pass: XXXX
  retries: 6
ntp:
  host: pool.ntp.org
  timezone: Europe/Dublin
mqtt_logger:
  enabled: false
  debug: false
  broker: <mqtt-broker-host>
  port: 1883
  clientId: inkplate10-weather-cal
  topic: inkplate/weather-calendar/diagnostics
  retries: 3
```

Likely parameters you'll need to change are:

- `display.rotation` - physical display orientation from `0` to `3`, in
  clockwise 90-degree steps. It defaults to `1`, preserving the existing
  Inkplate 10 portrait orientation. Use `3` for the opposite portrait
  orientation when the enclosure is mounted upside down relative to that
  default.
- `wifi.ssid` - the SSID of your WiFi network.
- `wifi.pass` - the WiFi password.
- `calendar.url` - the hostname or IP address of your server which the client will attempt to download the image from. Do not use `localhost` here unless the server is running on the Inkplate itself.
- `calendar.status_url` - optional small JSON manifest containing the rendered
  image SHA-256. When it matches the retained signature, the firmware skips the
  image download and e-paper refresh. Omit it when using an older or third-party
  server.
- `calendar.refresh_interval` - how often you want the device to wake up and check for a new image.
- `calendar.retries` - whether to make one immediate image retry while awake.
  Set to `0` to disable it; values above `1` are accepted for compatibility but
  still result in only one immediate retry.
- `calendar.retry_interval_minutes` - deep-sleep interval after both image
  attempts fail. Defaults to 15 minutes.
- `ntp.timezone` - the timezone used for local timestamps and daylight-saving
  rules, in Olson format such as `Europe/Dublin`.
- `mqtt_logger.broker` - the MQTT broker reachable from the Inkplate when remote diagnostics are enabled.
- `mqtt_logger.debug` - publish detailed diagnostics over MQTT; defaults to `false`. Serial logging remains verbose.

The firmware uses the Inkplate RTC between wake cycles and refreshes it from NTP
at most once every 24 hours. A cold boot, invalid RTC value, or clock moving
backwards forces an immediate NTP synchronization.

When `calendar.status_url` is configured, an unchanged SHA-256 skips both the
PNG download and e-paper update. If the status endpoint is unavailable or
invalid, the firmware falls back to downloading the image. Failed image
attempts are limited to one immediate retry before the device returns to deep
sleep for `calendar.retry_interval_minutes`.

See the [server](/server) for info on server setup.

The server can also expose the rendered calendar as a lightweight browser/PWA
viewer at `http://<server-host>:8080/app`. Use `server.alwayson: true` for this
mode so the server keeps refreshing and serving the PNG continuously. The
browser viewer uses a separate inline image route and does not interfere with
the Inkplate client's `/calendar.png` download route.

The server also exposes versioned data and output endpoints:

```text
/api/v1/weather
/api/v1/health
/api/v1/ready
/api/v1/outputs/inkplate10-portrait/status
/outputs/inkplate10-portrait/calendar.png
```

`/calendar.png` remains available as a compatibility alias for existing
Inkplate firmware.

Named outputs are profile-driven. Additional display sizes and renderer
implementations can be enabled as separate profiles while `/calendar.png`
continues to serve the configured default.

The server supports AccuWeather and OpenWeatherMap One Call 3.0 and 4.0.
OpenWeatherMap v2 has been removed. Forecast and optional realtime providers
use explicit normalized interfaces so additional integrations can be added
without extending the producer entry point.

## MQTT

The server can optionally publish the normalized weather snapshot to MQTT for
other local displays and automations. It can separately listen for diagnostic
messages from the Inkplate firmware. The Inkplate still downloads the rendered
PNG regardless of either MQTT feature.

See [MQTT Weather and Diagnostics](docs/mqtt.md) for broker setup, topics, payloads,
and example clients such as the
[52Pi EP-0164 Pico W weather display](examples/pico-ep0164-mqtt-weather).

## Server Installation

The recommended server setup is the interactive installer. It can configure
Docker Compose, Podman Compose, native producer and web systemd services under
`/srv/inkplate`, or hand off to the experimental Proxmox VE 9 OCI installer.

Run it from the repository root:

```bash
./bin/install_server
```

The installer prompts for the weather provider, API keys, Google Static Maps
Map ID, location, optional Netatmo details, optional MQTT weather publishing,
optional MQTT diagnostic listening, and whether to start the service/container.
It keeps secrets out of committed YAML files:

- Docker and Podman installs write secrets to `.env` and config to
  `server/config/config.yaml`.
- systemd installs write secrets to `/etc/inkplate/weather.env`, config to
  `/srv/inkplate/server/config/config.yaml`, and dependencies to
  `/srv/inkplate/inkplate_venv`.

For native systemd installs, run as root or as a user that can elevate with
`sudo`, `doas`, or `run0`. The installer checks this before making system
changes and exits cleanly if it cannot get the required privileges.

You can preview actions without writing files:

```bash
./bin/install_server --dry-run
```

For CI or repeatable testing, use a JSON answers file:

```bash
./bin/install_server --dry-run --non-interactive --answers bin/install_server.answers.example.json
```

Re-run the installer later to update an existing install. It will detect
existing Docker, Podman, or systemd files and offer to update the application
while preserving config/secrets, reconfigure config/secrets, or abort.

The Proxmox option directs you to the dedicated fresh-install-only preview:

```bash
sudo ./bin/install_proxmox --dry-run
```

Interactive Proxmox runs list available LXC storage and can create separate
mounts for generated data and read-only config. Use `--storage`,
`--data-storage`, and `--config-storage` for unattended storage selection.

To deploy from this checkout to another machine over SSH, use the remote
wrapper. It currently supports Proxmox and systemd targets:

```bash
./bin/install_remote root@pve1 --mode proxmox
./bin/install_remote admin@server1 --mode systemd
```

The wrapper uploads only Git-tracked files plus an explicitly supplied answers
file, runs the normal installer on the target, and removes its temporary remote
workspace after success. It preserves the workspace after a failure and prints
its location for diagnosis. `--dry-run` previews locally without connecting;
`--remote-dry-run` connects and runs the selected installer's own dry-run.

For troubleshooting:

```bash
docker compose logs -f
podman compose logs -f
sudo journalctl -u inkplate-producer -u inkplate -u inkplate-diagnostics -f
```

If Docker reports socket permission errors after adding a user to the `docker`
group, start a new login session or run `newgrp docker` before retrying.

The lower-level helpers `bin/install_service` and `bin/refresh_deps` remain
available for manual repair or advanced installs.

## Firmware

### Building with Arduino CLI

The firmware still uses the Arduino framework and Inkplate Arduino libraries, but
it can be built without opening the Arduino IDE. This is the preferred workflow
for repeatable local builds.

Install the Arduino CLI into `.tools/`:

```bash
make firmware-install-cli
```

The installer downloads a pinned Arduino CLI release for your OS/architecture.
The `.tools/` directory is ignored by git so the binary is not committed. If you
already have `arduino-cli` installed on your `PATH`, you can skip this step.

Then install the Inkplate board package and required libraries (these libraries are installed locally under `build/sketchbook/` to avoid polluting your global Arduino library directory):

```bash
make firmware-setup
```

You can also let Make check the local firmware toolchain state and do the
missing setup work before compiling:

```bash
make world
```

`world` installs the repo-local Arduino CLI if needed, installs the Inkplate
board package or firmware libraries if they are missing, then runs
`firmware-compile`. Pass the same build options you would pass to
`firmware-compile`, for example:

```bash
make world CONFIG=firmware-config.yaml
```

Compile the firmware:

```bash
make firmware-compile
```

The build embeds a firmware identity based on the repository's highest release
tag. Exact tagged builds use the release tag; development builds append the
short commit SHA, for example `v3.0.1+g0b15863`. Dirty local builds also include
`.dirty`. Override the detected identity when packaging a specific build:

```bash
make firmware-compile FIRMWARE_VERSION=v3.1.0
```

The version is printed in serial boot diagnostics and included in the MQTT
`WAKE` event.

Maintainer release steps are documented in
[docs/releasing.md](docs/releasing.md).

This is the generic build used by CI and release binaries. It expects
`/config.yaml` on an SD card but renders the calendar image directly from HTTP.

For an SD-free build, copy and edit the firmware configuration template:

```bash
cp firmware-config.example.yaml firmware-config.yaml
make firmware-compile CONFIG=firmware-config.yaml
```

The generated header and build output stay under ignored `build/` paths. The
resulting binary contains the WiFi password and any MQTT details in recoverable
form, so do not publish it.

To clean generated firmware and embedded configuration artifacts:

```bash
make firmware-clean
```

The locally installed Arduino libraries are preserved. To remove those
dependencies as well:

```bash
make firmware-distclean
```

Upload to a connected Inkplate 10:

```bash
make firmware-upload PORT=/dev/ttyUSB0
```

`firmware-upload` recompiles before flashing. Add `CONFIG` to compile and flash
the embedded-config variant:

```bash
make firmware-upload PORT=/dev/ttyUSB0 CONFIG=firmware-config.yaml
```

Without `CONFIG`, upload builds the generic SD-config variant.

On macOS the port is usually under `/dev/cu.*`; on Windows it is usually a
`COMx` port. You can list connected boards with:

```bash
make firmware-board-list
```

The default CLI build target is `Inkplate_Boards:esp32:Inkplate10V2`, using the
Inkplate board package version `8.1.0`. These defaults can be overridden, for
example:

```bash
make firmware-compile FIRMWARE_FQBN=Inkplate_Boards:esp32:Inkplate10
```

### Building with Arduino IDE

The firmware can be compiled correctly on the Arduino IDE. Generic compiled
firmware is attached to each [GitHub release](../../releases), and CI builds
also provide a downloadable workflow artifact. You may be able to use a release
binary to program your board directly, but I would recommend setting up an
Arduino IDE with the latest library versions and compiling a version locally.

The below assumes you already have a working Arduino environment, configure for the Inkplate10 (with the board definition). The documentation for that is available here :

- https://inkplate.readthedocs.io/en/latest/get-started.html#arduino

The following libraries should be installed in your Arduino IDE. They are available in the IDE's Library Manager :

- [InkplateLibrary](https://github.com/SolderedElectronics/Inkplate-Arduino-library)
- [ArduinoJson](https://arduinojson.org/?utm_source=meta&utm_medium=library.properties)
- [Queue](https://github.com/SMFSW/Queue)
- [StreamUtils](https://github.com/bblanchon/ArduinoStreamUtils)
- [YAMLDuino](https://github.com/tobozo/YAMLDuino)
- [ezTime](https://github.com/ropg/ezTime)
- [SdFat](https://github.com/greiman/SdFat)

## License

All code in this repository is licensed under the MIT license.

Weather icons by [lutfix](https://www.flaticon.com/authors/lutfix) from [www.flaticon.com](https://www.flaticon.com).

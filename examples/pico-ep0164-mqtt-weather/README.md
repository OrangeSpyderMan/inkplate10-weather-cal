# 52Pi EP-0164 Pico W MQTT Weather Display

This is a small MicroPython example for displaying the Inkplate weather MQTT
snapshot on a Raspberry Pi Pico W fitted to the 52Pi EP-0164 Pico Breadboard
Kit.

The EP-0164 has a 2.8 inch 240x320 ILI9341 LCD. The 52Pi wiki documents the
MicroPython display driver and demo wiring:

- https://wiki.52pi.com/index.php?title=EP-0164

This example does not vendor the 52Pi display driver. Install the driver from
52Pi's documentation or demo archive, then copy this example alongside it.

## Prerequisites

- Raspberry Pi Pico W with MicroPython installed.
- 52Pi EP-0164 Pico Breadboard Kit.
- 52Pi ILI9341 MicroPython display driver copied to the Pico as
  `ili934xnew.py`.
- 52Pi/driver font dependency copied to the Pico as `glcdfont.py`.
- Network access from the Pico W to an MQTT broker receiving the Inkplate
  weather snapshot.

## Install MicroPython

Install MicroPython on the Pico W before copying this example. Use the official
Raspberry Pi MicroPython setup guide or the MicroPython Pico W download page:

- https://www.raspberrypi.com/documentation/microcontrollers/micropython.html
- https://micropython.org/download/RPI_PICO_W/

Use the Pico W firmware, not the non-wireless Pico firmware. After flashing, you
should be able to connect to the MicroPython REPL over USB and copy files to the
board with your preferred Pico file tool.

## Files

- `main.py` - MicroPython weather display client
- `config.example.py` - copy to `config.py` and edit for your network/broker

## Pico Filesystem

Copy these files to the Pico W:

```text
main.py
config.py
ili934xnew.py
glcdfont.py
```

Optional 52Pi font files can also be copied if you want to adjust typography
later:

```text
tt14.py
tt24.py
tt32.py
```

The example uses the 52Pi demo pin mapping:

```text
TFT_CLK  GP6
TFT_MOSI GP7
TFT_MISO GP4
TFT_CS   GP13
TFT_RST  GP14
TFT_DC   GP15
```

## MQTT Setup

Enable MQTT weather publishing on the server. See:

- [MQTT Weather and Diagnostics](../../docs/mqtt.md)

The example subscribes to the full retained snapshot topic:

```text
inkplate/weather-calendar
```

Using the full snapshot keeps the client simple: one retained message contains
current conditions, status metadata, and the hourly list.

## Configuration

Copy the example config:

```text
config.example.py -> config.py
```

Edit:

```python
WIFI_SSID = "your-wifi"
WIFI_PASSWORD = "your-password"
MQTT_HOST = "192.168.1.10"
MQTT_BASE_TOPIC = "inkplate/weather-calendar"
```

Use the broker IP address or hostname reachable from the Pico W. Do not use the
Docker Compose service name `mqtt` from the Pico; that name only works inside
the Compose network.

## IPv6

This example should be treated as IPv4-first. The server-side MQTT publisher can
use IPv6-capable broker hostnames or IPv6 literals, but Pico W IPv6 support
depends on the MicroPython RP2 build and network stack.

For the first hardware test, use an IPv4 broker address or a hostname that
resolves to an IPv4 address. If you want to test IPv6 later, verify the Pico has
an IPv6 address, DNS can return IPv6 records, and `umqtt.simple` can connect to
the broker on your MicroPython build.

## Display Behaviour

The first version intentionally keeps the layout simple:

- current temperature
- current icon name rendered as text
- forecast source/status time
- first four hourly forecast entries

The weather icon path from MQTT is converted to a short label such as `Rain`,
`Cloudy`, or `Clear`. This keeps the client useful before adding bitmap icon
assets.

## Notes

- The MQTT payload does not include binary icon data.
- The example falls back to serial output if the ILI9341 driver is not present.
- The retained MQTT snapshot means the display should populate shortly after it
  connects, even if the server last published minutes or hours earlier.

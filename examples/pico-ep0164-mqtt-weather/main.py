import time

import network
import ujson as json
from machine import Pin, SPI
from micropython import const

try:
    from umqtt.simple import MQTTClient
except ImportError:
    raise ImportError(
        "umqtt.simple is required; install it with mpremote as described "
        "in the example README"
    )

from config import (
    IDLE_SLEEP_SECONDS,
    MQTT_BASE_TOPIC,
    MQTT_CLIENT_ID,
    MQTT_HOST,
    MQTT_PORT,
    WIFI_PASSWORD,
    WIFI_SSID,
)


SCR_WIDTH = const(320)
SCR_HEIGHT = const(240)
SCR_ROT = const(2)

TFT_CLK_PIN = const(6)
TFT_MOSI_PIN = const(7)
TFT_MISO_PIN = const(4)
TFT_CS_PIN = const(13)
TFT_RST_PIN = const(14)
TFT_DC_PIN = const(15)

BLACK = 0x0000
WHITE = 0xFFFF
YELLOW = 0xFFE0
CYAN = 0x07FF
GREEN = 0x07E0
RED = 0xF800


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep(1)

    if not wlan.isconnected():
        raise RuntimeError("WiFi connection failed")

    print("WiFi connected", wlan.ifconfig())
    return wlan


class SerialDisplay:
    def show_status(self, line):
        print(line)

    def show_snapshot(self, payload):
        print(render_lines(payload))


class Ili9341Display:
    def __init__(self):
        from ili934xnew import ILI9341

        spi = SPI(
            0,
            baudrate=40000000,
            miso=Pin(TFT_MISO_PIN),
            mosi=Pin(TFT_MOSI_PIN),
            sck=Pin(TFT_CLK_PIN),
        )
        self.display = ILI9341(
            spi,
            cs=Pin(TFT_CS_PIN),
            dc=Pin(TFT_DC_PIN),
            rst=Pin(TFT_RST_PIN),
            w=SCR_WIDTH,
            h=SCR_HEIGHT,
            r=SCR_ROT,
        )

    def show_status(self, line):
        self.display.erase()
        self._print(8, 8, line, YELLOW)

    def show_snapshot(self, payload):
        lines = render_lines(payload)
        self.display.erase()
        colors = [CYAN, WHITE, GREEN, WHITE, WHITE, WHITE, WHITE]
        y = 8
        for index, line in enumerate(lines):
            self._print(8, y, line, colors[min(index, len(colors) - 1)])
            y += 30

    def _print(self, x, y, text, color):
        self.display.set_pos(x, y)
        self.display.set_color(color, BLACK)
        self.display.print(text)


def create_display():
    try:
        return Ili9341Display()
    except Exception as exc:
        print("Display init failed, using serial output:", exc)
        return SerialDisplay()


def render_lines(payload):
    current = payload.get("current", {})
    hourly = payload.get("hourly", [])
    status = "{} {}".format(payload.get("source", ""), payload.get("units", ""))

    temp = temperature_text(current)
    icon = icon_label(current.get("icon", ""))
    generated_at = payload.get("generated_at", "")

    lines = [
        "Inkplate Weather",
        "Now: {} {}".format(temp, icon),
        status[:26],
        generated_at[:26],
    ]

    for item in hourly[:4]:
        lines.append(hourly_line(item))

    return lines


def hourly_line(item):
    dt = item.get("dt", "")
    hour = dt[11:16] if len(dt) >= 16 else "--:--"
    temp = temperature_text(item)
    rain = item.get("rain_probability", 0)
    icon = icon_label(item.get("icon", ""))
    return "{} {:>6} {:>3}% {}".format(hour, temp, rain, icon)[:28]


def temperature_text(item):
    temperature = item.get("temperature", {})
    value = temperature.get("value")
    unit = temperature.get("unit", "")
    if value is None:
        return "--"

    suffix = "C"
    if "F" in unit:
        suffix = "F"

    return "{}{}".format(value, suffix)


def icon_label(icon):
    icon = icon.lower()
    if "thunder" in icon:
        return "Storm"
    if "snow" in icon:
        return "Snow"
    if "rain" in icon or "showers" in icon:
        return "Rain"
    if "fog" in icon:
        return "Fog"
    if "wind" in icon:
        return "Wind"
    if "clear" in icon and "partly" not in icon:
        return "Clear"
    if "cloud" in icon or "partly" in icon:
        return "Cloudy"
    if "hot" in icon:
        return "Hot"
    if "cold" in icon or "icy" in icon:
        return "Cold"
    return "Weather"


def main():
    display = create_display()
    display.show_status("Connecting WiFi")
    connect_wifi()

    topic = MQTT_BASE_TOPIC.encode()

    def on_message(received_topic, message):
        try:
            if isinstance(message, bytes):
                message = message.decode()
            payload = json.loads(message)
            display.show_snapshot(payload)
        except Exception as exc:
            print("Bad MQTT payload:", exc)
            display.show_status("Bad MQTT payload")

    display.show_status("Connecting MQTT")
    client = MQTTClient(MQTT_CLIENT_ID, MQTT_HOST, port=MQTT_PORT, keepalive=60)
    client.set_callback(on_message)
    client.connect()
    client.subscribe(topic)
    display.show_status("Waiting for MQTT")
    print("Subscribed to", topic)

    while True:
        client.check_msg()
        time.sleep(IDLE_SLEEP_SECONDS)


main()

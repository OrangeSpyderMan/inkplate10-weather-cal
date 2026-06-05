WIFI_SSID = "your-wifi"
WIFI_PASSWORD = "your-password"

# Use an IPv4 address or hostname for the first Pico W hardware test. IPv6 on
# Pico W depends on the MicroPython build and is not yet verified by this
# example.
MQTT_HOST = "192.168.1.10"
MQTT_PORT = 1883
MQTT_BASE_TOPIC = "inkplate/weather-calendar"
MQTT_CLIENT_ID = "pico-ep0164-weather"

# Seconds between MQTT keepalive checks while waiting for retained updates.
IDLE_SLEEP_SECONDS = 1

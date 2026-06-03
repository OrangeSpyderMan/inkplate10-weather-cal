#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import sys
import yaml
import time
import threading
import datetime as dt
import logging.config
import paho.mqtt.client as mqtt
from utils import expand_env_vars, get_prop, get_prop_by_keys
from views.calendar import CalendarPage
from google.api import GoogleAPIService
from werkzeug.serving import make_server
from flask import Flask, send_file, abort, send_from_directory

cwd = os.path.dirname(os.path.realpath(__file__))
pwa_dir = os.path.join(cwd, "views", "pwa")
default_config_path = os.path.join(cwd, "config.yaml")
default_config_dir_path = os.path.join(cwd, "config", "config.yaml")
log = None

app = Flask(__name__)
# number of times served
server_num_serves = 0
server_max_serves = 1


def main():
    global log, server_max_serves

    config_path = resolve_config_path()
    with open(config_path) as config_file:
        config = expand_env_vars(yaml.safe_load(config_file))

    debug = get_prop(config, "debug", default=False)
    # Create and configure logger
    log_ini_path = os.path.join(cwd, "logging.ini")
    if debug:
        logging.config.fileConfig(os.path.join(cwd, "logging.dev.ini"))
    logging.config.fileConfig(log_ini_path)
    log = logging.getLogger("server")
    log.info(f"Loaded config from {config_path}")

    google_apikey = get_prop_by_keys(config, "google", "apikey", required=True)
    weather_service_type = get_prop_by_keys(config, "weather", "service", required=True)
    if weather_service_type not in [
        "accuweather",
        "openweathermap",
        "openweathermapv3",
    ]:
        log.error(f"not a supported weather service {weather_service_type}")
        sys.exit(1)

    weather_apikey = get_prop_by_keys(config, "weather", "apikey", required=True)
    weather_metric = get_prop_by_keys(config, "weather", "metric", default=True)
    weather_num_hourly_forecasts = get_prop_by_keys(
        config, "weather", "num_hourly_forecasts", default=6
    )
    if weather_num_hourly_forecasts < 0:
        log.error(
            f"num_hourly_forecasts {weather_num_hourly_forecasts} must be non-negative"
        )
        sys.exit(1)

    staticmaps_mapid = get_prop_by_keys(
        config, "google", "staticmaps_mapid", required=True
    )

    location = get_prop(config, "location", required=True).strip().replace(" ", "")

    server_enabled = get_prop_by_keys(config, "server", "enabled", default=True)
    server_port = get_prop_by_keys(config, "server", "port", default=8080)
    server_always_on = get_prop_by_keys(config, "server", "alwayson", default=False)
    server_refresh_seconds = 3600 * get_prop_by_keys(
        config, "server", "refreshhours", default=3
    )
    image_width = get_prop_by_keys(config, "image", "width", default=825)
    image_height = get_prop_by_keys(config, "image", "height", default=1200)

    mqtt_enabled = get_prop_by_keys(config, "mqtt", "enabled", default=False)
    mqtt_host = get_prop_by_keys(config, "mqtt", "host", default="localhost")
    mqtt_port = get_prop_by_keys(config, "mqtt", "port", default=1883)
    mqtt_topic = get_prop_by_keys(
        config, "mqtt", "topic", default="mqtt/eink-cal-client"
    )

    gapi = GoogleAPIService(google_apikey)
    map_file = os.path.join(cwd, "views", "html", "map.png")
    gapi.save_static_map(staticmaps_mapid, location, map_file)
    map_url = "map.png"

    weather_svc = None
    if weather_service_type == "accuweather":
        from weather.accuweather.accuweather import AccuweatherService

        weather_svc = AccuweatherService(
            weather_apikey,
            location,
            metric=weather_metric,
            num_hours=weather_num_hourly_forecasts,
        )
    elif weather_service_type == "openweathermap":
        log.error(
            f"{weather_service_type} is no longer supported.   Please use the V3 API (openweathermapv3)"
        )
        sys.exit(1)

    elif weather_service_type == "openweathermapv3":
        from weather.openweathermapv3.openweathermapv3 import OpenWeatherMapv3Service

        weather_svc = OpenWeatherMapv3Service(
            weather_apikey,
            location,
            metric=weather_metric,
            num_hours=weather_num_hourly_forecasts,
        )
    else:
        log.error(f"not a supported weather service {weather_service_type}")
        sys.exit(1)

    current_temperature_svc = build_current_temperature_service(
        config, weather_metric
    )

    # bail early if http server is not enabled
    if not server_enabled:
        sys.exit(0)

    # set up listener for client logs
    mqtt_client = None
    if mqtt_enabled:
        mqtt_client = get_client_mqtt_logging(mqtt_host, mqtt_port, mqtt_topic)

    # setup http server
    if server_always_on:
        http_server = ServerThread(app, server_port)
        http_server.start()
        log.info(f"Started always on server")
        while True:
            log.info(f"Retrieving forecast data")
            try:
                daily_summary = weather_svc.get_daily_summary()
                hourly_forecasts = weather_svc.get_hourly_forecast()
                apply_current_temperature_override(
                    daily_summary, current_temperature_svc
                )
            except Exception as e:
                error_string = repr(e)
                log.error(f"An error occurred whilst getting weather data!")
                log.error(f"The error was :")
                log.error(f"{error_string}")
                log.error(f"Sleeping for 120 seconds before retrying....")
                time.sleep(120)
                continue
            try:
                # generate page images
                log.info(f"Generating page")
                page = CalendarPage(image_width, image_height)
                page.template(
                    map_url=map_url,
                    daily_summary=daily_summary,
                    hourly_forecasts=hourly_forecasts,
                )
                page.save()
            except Exception as e:
                error_string = repr(e)
                log.error(f"An error occurred whilst creating page!")
                log.error(f"The error was :")
                log.error(f"{error_string}")
                log.error(f"Sleeping for 120 seconds before retrying....")
                time.sleep(120)
                continue
            log.info(f"Serving current image for {server_refresh_seconds} seconds")
            time.sleep(server_refresh_seconds)
            log.info(f"Woken after {server_refresh_seconds} seconds to refresh image")

    else:
        server_alive_seconds = get_prop_by_keys(
            config, "server", "aliveSeconds", default=60
        )
        server_max_serves = get_prop_by_keys(config, "server", "maxServes", default=1)
        try:
            daily_summary = weather_svc.get_daily_summary()
            hourly_forecasts = weather_svc.get_hourly_forecast()
            apply_current_temperature_override(
                daily_summary, current_temperature_svc
            )
        except Exception as e:
            error_string = repr(e)
            log.error(f"An error occurred whilst getting weather data!")
            log.error(f"The error was :")
            log.error(f"{error_string}")
            log.error(f"Will retry on next cycle...")
        try:
            # generate page images
            page = CalendarPage(image_width, image_height)
            page.template(
                map_url=map_url,
                daily_summary=daily_summary,
                hourly_forecasts=hourly_forecasts,
            )
            page.save()
        except Exception as e:
            error_string = repr(e)
            log.error(f"An error occurred whilst creating page!")
            log.error(f"The error was :")
            log.error(f"{error_string}")
            log.error(f"Retrying on next cycle")
        http_server = ServerThread(app, server_port)
        http_server.start()

        enable_wait = server_alive_seconds > 0
        enable_max_serves = server_max_serves > 0

        if enable_wait:
            log.info(
                f"Serving images for {server_alive_seconds} seconds before shutdown"
            )
        if enable_max_serves:
            log.info(
                f"Serving images for max {server_max_serves} times before shutdown"
            )

        start_wait_dt = dt.datetime.now()
        diff = dt.datetime.now() - start_wait_dt
        while (enable_max_serves and server_num_serves < server_max_serves) and (
            enable_wait and diff.seconds < server_alive_seconds
        ):
            time.sleep(1)
            diff = dt.datetime.now() - start_wait_dt

        http_server.shutdown(timeout=10)

        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()

        log.info(f"Exiting")
        sys.exit(0)


def resolve_config_path():
    env_config_path = os.environ.get("INKPLATE_CONFIG_FILE")
    if env_config_path:
        if os.path.isfile(env_config_path):
            return env_config_path

        print(
            f"INKPLATE_CONFIG_FILE points to a missing config file: {env_config_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    if os.path.isfile(default_config_dir_path):
        return default_config_dir_path

    if os.path.isfile(default_config_path):
        return default_config_path

    print(
        "No config file found. Checked "
        f"{default_config_dir_path} and {default_config_path}.",
        file=sys.stderr,
    )
    sys.exit(1)


def build_current_temperature_service(config, metric):
    current_temperature_config = get_prop(
        config, "current_temperature", default={}, required=False
    )
    if current_temperature_config is None:
        current_temperature_config = {}

    source = str(current_temperature_config.get("source", "weather")).lower()
    if source == "weather":
        return None

    if source != "netatmo":
        log.error(f"not a supported current temperature source {source}")
        sys.exit(1)

    netatmo_config = current_temperature_config.get(
        "netatmo", current_temperature_config
    )
    token_file = netatmo_config.get("token_file", "netatmo-token.json")
    if not os.path.isabs(token_file):
        token_file = os.path.join(cwd, token_file)

    from weather.netatmo.netatmo import NetatmoCurrentTemperatureService

    return NetatmoCurrentTemperatureService(
        client_id=get_required_current_temperature_config(
            netatmo_config, "client_id"
        ),
        client_secret=get_required_current_temperature_config(
            netatmo_config, "client_secret"
        ),
        refresh_token=get_required_current_temperature_config(
            netatmo_config, "refresh_token"
        ),
        token_file=token_file,
        device_id=get_optional_current_temperature_config(
            netatmo_config, "device_id"
        ),
        module_id=get_optional_current_temperature_config(
            netatmo_config, "module_id"
        ),
        metric=metric,
    )


def get_required_current_temperature_config(config, key):
    if key not in config or config[key] is None or config[key] == "":
        log.error(f"current_temperature.netatmo.{key} is required")
        sys.exit(1)

    return config[key]


def get_optional_current_temperature_config(config, key):
    value = config.get(key)
    if value == "":
        return None

    return value


def apply_current_temperature_override(daily_summary, current_temperature_svc):
    if current_temperature_svc is None:
        return

    daily_summary["temperature"].update(
        current_temperature_svc.get_current_temperature()
    )


def get_client_mqtt_logging(host, port, topic):
    mqtt_client = mqtt.Client("eink-cal-server")
    client_log = logging.getLogger("client")

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            log.error("Connection to client logging broker failed")

        log.info("Connected to client logging broker")

    def on_disconnect(client, userdata, rc):
        if rc != 0:
            log.error("Unexpected broker disconnection")

        log.info("Disconnected from client logging broker")

    def on_message(client, userdata, message):
        if message.retain:
            # ignore stale messages
            return

        client_log.info(message.payload.decode())

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    try:
        mqtt_client.connect(host, port, 60)
        mqtt_client.subscribe(topic)
        mqtt_client.loop_start()

        return mqtt_client
    except Exception as e:
        log.error(f"Connection to client logging broker failed: {e}")

    return None


class ServerThread(threading.Thread):
    def __init__(self, app, port, max_serves=1):
        threading.Thread.__init__(self)
        self.server = make_server("0.0.0.0", port, app)
        self.ctx = app.app_context()
        self.ctx.push()
        self.max_serves = max_serves

    def run(self):
        log.info("Starting http server")
        self.server.serve_forever()

    def shutdown(self, timeout=60):
        log.info(f"Stopping http server in {timeout} seconds")
        time.sleep(timeout)
        self.server.shutdown()


@app.route("/calendar.png")
def serve_cal_png():
    global server_num_serves, server_max_serves
    """
    Returns the calendar image directly through send_file
    """

    path = os.path.join(cwd, "views/calendar.png")

    if not os.path.exists(path):
        log.error(f"{path}: no such file exists")
        abort(404)

    f = open(path, "rb")
    stream = io.BytesIO(f.read())
    f.close()
    server_num_serves += 1
    log.info(f"Served the image")

    return send_file(
        stream,
        mimetype="image/png",
        as_attachment=True,
        download_name=os.path.basename(path),
    )


@app.route("/")
@app.route("/app")
@app.route("/app/")
@app.route("/app/index.html")
def serve_pwa():
    return send_from_directory(pwa_dir, "index.html")


@app.route("/app.css")
def serve_pwa_css():
    return send_from_directory(pwa_dir, "app.css")


@app.route("/app.js")
def serve_pwa_js():
    return send_from_directory(pwa_dir, "app.js")


@app.route("/manifest.webmanifest")
def serve_pwa_manifest():
    return send_from_directory(
        pwa_dir,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/sw.js")
def serve_pwa_service_worker():
    return send_from_directory(
        pwa_dir,
        "sw.js",
        mimetype="application/javascript",
    )


@app.route("/icons/<path:filename>")
def serve_pwa_icon(filename):
    return send_from_directory(os.path.join(pwa_dir, "icons"), filename)


@app.route("/favicon.ico")
def serve_favicon():
    return send_from_directory(
        os.path.join(pwa_dir, "icons"),
        "weathercal-favicon.ico",
        mimetype="image/x-icon",
    )


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def serve_apple_touch_icon():
    return send_from_directory(
        os.path.join(pwa_dir, "icons"),
        "weathercal-icon-192.png",
        mimetype="image/png",
    )


@app.route("/app/calendar.png")
def serve_pwa_cal_png():
    """
    Returns the calendar image for browsers without affecting the Inkplate
    download route's serve counter or Content-Disposition behavior.
    """

    path = os.path.join(cwd, "views/calendar.png")

    if not os.path.exists(path):
        log.error(f"{path}: no such file exists")
        abort(404)

    return send_file(
        path,
        mimetype="image/png",
        as_attachment=False,
        max_age=0,
    )


if __name__ == "__main__":
    main()

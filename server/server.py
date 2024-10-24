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
from utils import get_prop, get_prop_by_keys
from views.calendar import CalendarPage
from google.api import GoogleAPIService
from werkzeug.serving import make_server
from flask import Flask, send_file, abort

cwd = os.path.dirname(os.path.realpath(__file__))
log = None

app = Flask(__name__)
# number of times served
server_num_serves = 0
server_max_serves = 1


def main():
    global log, server_max_serves

    config_file = open(os.path.join(cwd, "config.yaml"))
    config = yaml.safe_load(config_file)
    config_file.close()

    debug = get_prop(config, "debug", default=False)
    # Create and configure logger
    log_ini_path = os.path.join(cwd, "logging.ini")
    if debug:
        logging.config.fileConfig(os.path.join(cwd, "logging.dev.ini"))
    logging.config.fileConfig(log_ini_path)
    log = logging.getLogger("server")

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
    map_url = gapi.get_static_map_url(staticmaps_mapid, location)

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
    log.info(f"Served the image")

    return send_file(
        stream,
        mimetype="image/png",
        as_attachment=True,
        download_name=os.path.basename(path),
    )
    

if __name__ == "__main__":
    main()

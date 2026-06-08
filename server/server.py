#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import yaml
import time
import threading
import datetime as dt
import logging.config
from utils import expand_env_vars, get_prop, get_prop_by_keys
from mqtt_diagnostics import MqttDiagnosticListener
from mqtt_publisher import MqttWeatherPublisher
from artifacts import ArtifactStore, DEFAULT_OUTPUT_PROFILE
from weather.snapshot import WeatherSnapshot
from web import create_app
from views.calendar import CalendarPage
from google.api import GoogleAPIService
from werkzeug.serving import make_server

cwd = os.path.dirname(os.path.realpath(__file__))
default_config_path = os.path.join(cwd, "config.yaml")
default_config_dir_path = os.path.join(cwd, "config", "config.yaml")
log = None

# number of times served
server_num_serves = 0
server_max_serves = 1


def record_legacy_calendar_serve():
    global server_num_serves
    server_num_serves += 1
    if log is not None:
        log.info("Served the image")


artifact_store = ArtifactStore(
    os.environ.get("INKPLATE_DATA_DIR", os.path.join(cwd, "data"))
)
app = create_app(
    data_dir=artifact_store.root,
    legacy_calendar_served=record_legacy_calendar_serve,
)


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
    removed_temporary_files = artifact_store.cleanup_stale_temporary_files()
    if removed_temporary_files:
        log.info(
            "Removed %s stale temporary artifact files",
            len(removed_temporary_files),
        )

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

    mqtt_config = get_prop(config, "mqtt", default={}, required=False) or {}
    weather_publisher = build_mqtt_weather_publisher(
        mqtt_config.get("weather", {}) or {}
    )
    diagnostic_listener = build_mqtt_diagnostic_listener(
        mqtt_config.get("diagnostics", {}) or {}
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

    if diagnostic_listener is not None and not diagnostic_listener.start():
        diagnostic_listener = None

    # setup http server
    if server_always_on:
        http_server = ServerThread(app, server_port)
        http_server.start()
        log.info(f"Started always on server")
        while True:
            log.info(f"Retrieving forecast data")
            try:
                snapshot = build_weather_snapshot(
                    weather_svc,
                    current_temperature_svc,
                    weather_service_type,
                    weather_metric,
                )
                artifact_store.write_snapshot(snapshot)
                publish_weather_snapshot(weather_publisher, snapshot)
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
                render_calendar_snapshot(
                    snapshot, image_width, image_height, map_url, artifact_store
                )
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
            snapshot = build_weather_snapshot(
                weather_svc,
                current_temperature_svc,
                weather_service_type,
                weather_metric,
            )
            artifact_store.write_snapshot(snapshot)
            publish_weather_snapshot(weather_publisher, snapshot)
        except Exception as e:
            error_string = repr(e)
            log.error(f"An error occurred whilst getting weather data!")
            log.error(f"The error was :")
            log.error(f"{error_string}")
            log.error(f"Will retry on next cycle...")
        try:
            # generate page images
            render_calendar_snapshot(
                snapshot, image_width, image_height, map_url, artifact_store
            )
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

        if diagnostic_listener is not None:
            diagnostic_listener.stop()

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


def build_weather_snapshot(
    weather_svc, current_temperature_svc, weather_service_type, weather_metric
):
    daily_summary = weather_svc.get_daily_summary()
    hourly_forecasts = weather_svc.get_hourly_forecast()
    apply_current_temperature_override(daily_summary, current_temperature_svc)

    return WeatherSnapshot(
        daily_summary=daily_summary,
        hourly_forecasts=hourly_forecasts,
        weather_source=weather_service_type,
        metric=weather_metric,
    )


def render_calendar_snapshot(
    snapshot, image_width, image_height, map_url, store=artifact_store
):
    page = CalendarPage(
        image_width,
        image_height,
        output_path=store.output_path(
            DEFAULT_OUTPUT_PROFILE,
            "calendar.png",
        ),
    )
    page.template(
        map_url=map_url,
        daily_summary=snapshot.daily_summary,
        hourly_forecasts=snapshot.hourly_forecasts,
    )
    page.save()


def build_mqtt_weather_publisher(mqtt_config):
    if not mqtt_config.get("enabled", False):
        return None

    broker = mqtt_config.get("broker", "localhost")
    port = mqtt_config.get("port", 1883)
    base_topic = mqtt_config.get("base_topic", "inkplate/weather-calendar")
    retain = mqtt_config.get("retain", True)
    qos = mqtt_config.get("qos", 0)

    return MqttWeatherPublisher(
        broker=broker,
        port=port,
        base_topic=base_topic,
        retain=retain,
        qos=qos,
    )


def build_mqtt_diagnostic_listener(mqtt_config):
    if not mqtt_config.get("enabled", False):
        return None

    return MqttDiagnosticListener(
        broker=mqtt_config.get("broker", "localhost"),
        port=mqtt_config.get("port", 1883),
        topic=mqtt_config.get(
            "topic", "inkplate/weather-calendar/diagnostics"
        ),
        qos=mqtt_config.get("qos", 0),
    )


def publish_weather_snapshot(weather_publisher, snapshot):
    if weather_publisher is None:
        return

    weather_publisher.publish_snapshot(snapshot)


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


if __name__ == "__main__":
    main()

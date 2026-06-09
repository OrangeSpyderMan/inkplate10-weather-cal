#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging.config
from utils import get_prop, get_prop_by_keys
from mqtt_diagnostics import MqttDiagnosticListener
from mqtt_publisher import MqttWeatherPublisher
from artifacts import ArtifactStore
from configuration import load_config
from output_profiles import load_output_profiles
from renderers import build_renderer
from weather.snapshot import WeatherSnapshot
from google.api import GoogleAPIService

cwd = os.path.dirname(os.path.realpath(__file__))
log = None

artifact_store = ArtifactStore(
    os.environ.get("INKPLATE_DATA_DIR", os.path.join(cwd, "data"))
)


def main():
    global log

    config_path, config = load_config()

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
    server_always_on = get_prop_by_keys(config, "server", "alwayson", default=False)
    server_refresh_seconds = 3600 * get_prop_by_keys(
        config, "server", "refreshhours", default=3
    )
    output_profiles, default_output_profile = load_output_profiles(config)
    output_renderers = build_output_renderers(output_profiles)
    log.info(
        "Enabled output profiles: %s (default: %s)",
        ", ".join(output_profiles),
        default_output_profile,
    )

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

    if server_always_on:
        while True:
            if not produce_artifacts(
                weather_svc,
                current_temperature_svc,
                weather_service_type,
                weather_metric,
                weather_publisher,
                map_url,
                output_profiles,
                output_renderers,
                artifact_store,
            ):
                log.error("Sleeping for 120 seconds before retrying....")
                time.sleep(120)
                continue
            log.info(f"Sleeping for {server_refresh_seconds} seconds before refresh")
            time.sleep(server_refresh_seconds)

    else:
        success = produce_artifacts(
            weather_svc,
            current_temperature_svc,
            weather_service_type,
            weather_metric,
            weather_publisher,
            map_url,
            output_profiles,
            output_renderers,
            artifact_store,
        )
        if diagnostic_listener is not None:
            diagnostic_listener.stop()
        sys.exit(0 if success else 1)


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


def build_output_renderers(profiles):
    return {
        name: build_renderer(profile.renderer)
        for name, profile in profiles.items()
    }


def render_outputs(
    snapshot,
    map_url,
    profiles,
    renderers,
    store=artifact_store,
):
    for name, profile in profiles.items():
        renderer = renderers[name]
        renderer.render(
            snapshot,
            map_url,
            store.output_path(profile.name, profile.filename),
            profile.width,
            profile.height,
            profile.options,
        )


def produce_artifacts(
    weather_svc,
    current_temperature_svc,
    weather_service_type,
    weather_metric,
    weather_publisher,
    map_url,
    profiles,
    renderers,
    store=artifact_store,
):
    log.info("Retrieving forecast data")
    try:
        snapshot = build_weather_snapshot(
            weather_svc,
            current_temperature_svc,
            weather_service_type,
            weather_metric,
        )
    except Exception as exc:
        log.exception("An error occurred whilst getting weather data: %s", exc)
        return False

    try:
        log.info("Generating output profiles")
        render_outputs(
            snapshot,
            map_url,
            profiles,
            renderers,
            store,
        )
        store.write_snapshot(snapshot)
        store.write_ready(snapshot, profiles)
    except Exception as exc:
        log.exception("An error occurred whilst creating artifacts: %s", exc)
        return False

    publish_weather_snapshot(weather_publisher, snapshot)
    return True


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


if __name__ == "__main__":
    main()

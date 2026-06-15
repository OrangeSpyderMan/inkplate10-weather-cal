#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging.config
from utils import get_prop, get_prop_by_keys
from mqtt_publisher import MqttWeatherPublisher
from artifacts import ArtifactStore
from configuration import load_config
from output_profiles import load_output_profiles
from renderers import build_renderer
from weather.snapshot import WeatherSnapshot
from weather.providers import (
    ConfigurationError,
    build_forecast_provider,
    build_realtime_provider,
)
from google.api import GoogleAPIService

cwd = os.path.dirname(os.path.realpath(__file__))
log = None

artifact_store = ArtifactStore(
    os.environ.get("INKPLATE_DATA_DIR", os.path.join(cwd, "data"))
)


def configure_logging(debug):
    configured_path = os.environ.get("INKPLATE_LOG_CONFIG")
    if configured_path:
        log_config_path = configured_path
    elif debug:
        log_config_path = os.path.join(cwd, "logging.dev.ini")
    else:
        log_config_path = os.path.join(cwd, "logging.ini")
    logging.config.fileConfig(log_config_path)
    return logging.getLogger("server")


def main():
    global log

    try:
        return run()
    except (ConfigurationError, KeyError) as exc:
        if log is None:
            logging.basicConfig()
            logging.getLogger("server").error("Configuration error: %s", exc)
        else:
            log.error("Configuration error: %s", exc)
        return 1


def run():
    global log

    config_path, config = load_config()

    debug = get_prop(config, "debug", default=False)
    log = configure_logging(debug)
    log.info(f"Loaded config from {config_path}")
    removed_temporary_files = artifact_store.cleanup_stale_temporary_files()
    if removed_temporary_files:
        log.info(
            "Removed %s stale temporary artifact files",
            len(removed_temporary_files),
        )

    google_apikey = get_prop_by_keys(config, "google", "apikey", required=True)
    weather_service_type = get_prop_by_keys(
        config, "weather", "service", required=True
    )

    weather_apikey = get_prop_by_keys(config, "weather", "apikey", required=True)
    weather_metric = get_prop_by_keys(config, "weather", "metric", default=True)
    weather_num_hourly_forecasts = get_prop_by_keys(
        config, "weather", "num_hourly_forecasts", default=6
    )
    if weather_num_hourly_forecasts < 0:
        raise ConfigurationError(
            f"num_hourly_forecasts {weather_num_hourly_forecasts} must be non-negative"
        )

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

    weather_svc = build_weather_service(
        weather_service_type,
        weather_apikey,
        location,
        weather_metric,
        weather_num_hourly_forecasts,
    )
    realtime_svc = build_current_conditions_service(
        config,
        weather_metric,
    )

    gapi = GoogleAPIService(google_apikey)
    map_file = os.path.join(cwd, "views", "html", "map.png")
    gapi.save_static_map(staticmaps_mapid, location, map_file)
    map_url = "map.png"

    # bail early if http server is not enabled
    if not server_enabled:
        return 0

    if server_always_on:
        while True:
            if not produce_artifacts(
                weather_svc,
                realtime_svc,
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
            realtime_svc,
            weather_service_type,
            weather_metric,
            weather_publisher,
            map_url,
            output_profiles,
            output_renderers,
            artifact_store,
        )
        return 0 if success else 1


def build_weather_service(
    service_type,
    apikey,
    location,
    metric,
    num_hourly_forecasts,
):
    return build_forecast_provider(
        service_type,
        apikey=apikey,
        location=location,
        metric=metric,
        num_hours=num_hourly_forecasts,
    )


def build_current_conditions_service(config, metric):
    realtime_config = get_prop(
        config,
        "current_conditions",
        default=None,
        required=False,
    )
    if realtime_config is None:
        realtime_config = get_prop(
            config,
            "current_temperature",
            default={},
            required=False,
        )
    return build_realtime_provider(
        realtime_config,
        metric=metric,
        base_dir=os.path.abspath(cwd),
    )


def apply_current_conditions(daily_summary, realtime_svc):
    if realtime_svc is None:
        return

    for key, value in realtime_svc.get_current_conditions().items():
        if isinstance(value, dict) and isinstance(daily_summary.get(key), dict):
            daily_summary[key].update(value)
        else:
            daily_summary[key] = value


def build_weather_snapshot(
    weather_svc, realtime_svc, weather_service_type, weather_metric
):
    daily_summary = weather_svc.get_daily_summary()
    hourly_forecasts = weather_svc.get_hourly_forecast()
    apply_current_conditions(daily_summary, realtime_svc)

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
    realtime_svc,
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
            realtime_svc,
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


def publish_weather_snapshot(weather_publisher, snapshot):
    if weather_publisher is None:
        return

    weather_publisher.publish_snapshot(snapshot)


if __name__ == "__main__":
    sys.exit(main())

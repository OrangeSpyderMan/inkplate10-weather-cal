#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging.config
from datetime import timedelta
from mqtt_publisher import MqttWeatherPublisher
from artifacts import ArtifactStore
from configuration import load_config
from producer_config import ProducerConfig, producer_enabled
from redaction import exception_text
from renderers import build_renderer
from weather.snapshot import WeatherSnapshot
from server_status import ServerStatus, sanitized_error, utc_now
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
        error = exception_text(exc)
        if log is None:
            logging.basicConfig()
            logging.getLogger("server").error("Configuration error: %s", error)
        else:
            log.error("Configuration error: %s", error)
        return 1
    except Exception as exc:
        error = exception_text(exc)
        if log is None:
            logging.basicConfig()
            logging.getLogger("server").error("Producer failed: %s", error)
        else:
            log.error("Producer failed: %s", error)
        return 1


def run():
    global log

    config_path, config = load_config()

    debug = bool(config.get("debug", False))
    log = configure_logging(debug)
    log.info(f"Loaded config from {config_path}")
    if not producer_enabled(config):
        log.info("Producer is disabled by server.enabled")
        return 0

    settings = ProducerConfig.from_config(config)
    log.info(
        "Producer refresh interval: %s seconds (from %s)",
        settings.refresh_seconds,
        settings.refresh_source,
    )
    removed_temporary_files = artifact_store.cleanup_stale_temporary_files()
    if removed_temporary_files:
        log.info(
            "Removed %s stale temporary artifact files",
            len(removed_temporary_files),
        )

    output_renderers = build_output_renderers(settings.output_profiles)
    log.info(
        "Enabled output profiles: %s (default: %s)",
        ", ".join(settings.output_profiles),
        settings.default_output_profile,
    )

    weather_publisher = build_mqtt_weather_publisher(
        settings.mqtt_weather_config
    )
    status = ServerStatus(
        artifact_store,
        mode="always_on" if settings.always_on else "one_shot",
        refresh_seconds=settings.refresh_seconds,
        forecast_provider=settings.weather_service,
        realtime_provider=settings.realtime_config.get("source", "weather"),
        profiles=settings.output_profiles,
        mqtt_publisher=weather_publisher,
        metadata=None,
    )
    status.transition("starting")

    weather_svc = build_weather_service(
        settings.weather_service,
        settings.weather_api_key,
        settings.location,
        settings.weather_metric,
        settings.hourly_forecasts,
        settings.forecast_slice_hours,
        settings.forecast_lead_minutes,
    )
    realtime_svc = build_realtime_provider(
        settings.realtime_config,
        metric=settings.weather_metric,
        base_dir=os.path.abspath(cwd),
    )

    gapi = GoogleAPIService(settings.google_api_key)
    map_file = os.path.join(cwd, "views", "html", "map.png")
    try:
        gapi.save_static_map(
            settings.static_maps_id,
            settings.location,
            map_file,
        )
    except Exception as exc:
        log.error(
            "Static map update failed: %s",
            exception_text(exc, (settings.google_api_key,)),
        )
        return 1
    map_url = "map.png"

    if settings.always_on:
        while True:
            if not produce_artifacts(
                weather_svc,
                realtime_svc,
                settings.weather_service,
                settings.weather_metric,
                weather_publisher,
                map_url,
                settings.output_profiles,
                output_renderers,
                artifact_store,
                status=status,
                success_state="ready",
                next_refresh_seconds=settings.refresh_seconds,
            ):
                log.error("Sleeping for 120 seconds before retrying....")
                time.sleep(120)
                continue
            log.info(
                "Sleeping for %s seconds before refresh",
                settings.refresh_seconds,
            )
            time.sleep(settings.refresh_seconds)

    else:
        success = produce_artifacts(
            weather_svc,
            realtime_svc,
            settings.weather_service,
            settings.weather_metric,
            weather_publisher,
            map_url,
            settings.output_profiles,
            output_renderers,
            artifact_store,
            status=status,
            success_state="completed",
        )
        return 0 if success else 1


def build_weather_service(
    service_type,
    apikey,
    location,
    metric,
    num_hourly_forecasts,
    forecast_slice_hours,
    forecast_lead_minutes,
):
    return build_forecast_provider(
        service_type,
        apikey=apikey,
        location=location,
        metric=metric,
        num_hours=num_hourly_forecasts,
        forecast_slice_hours=forecast_slice_hours,
        forecast_lead_minutes=forecast_lead_minutes,
    )


def apply_current_conditions(daily_summary, realtime_svc):
    if realtime_svc is None:
        return daily_summary

    try:
        current_conditions = realtime_svc.get_current_conditions()
    except Exception as exc:
        secrets = realtime_service_secrets(realtime_svc)
        log.warning(
            "Realtime conditions unavailable; using forecast conditions: %s",
            exception_text(exc, secrets),
        )
        return daily_summary

    return daily_summary.overlay(current_conditions)


def build_weather_snapshot(
    weather_svc, realtime_svc, weather_service_type, weather_metric
):
    forecast = weather_svc.fetch().validate()
    forecast.current = apply_current_conditions(
        forecast.current,
        realtime_svc,
    )

    return WeatherSnapshot(
        daily_summary=None,
        hourly_forecasts=None,
        weather_source=weather_service_type,
        metric=weather_metric,
        forecast=forecast,
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
    status=None,
    success_state="ready",
    next_refresh_seconds=None,
):
    secrets = service_secrets(weather_svc, realtime_svc)
    cycle_started_at = utc_now()
    if status is not None:
        status.transition(
            "refreshing",
            cycle_started_at=cycle_started_at,
        )
    log.info("Retrieving forecast data")
    try:
        snapshot = build_weather_snapshot(
            weather_svc,
            realtime_svc,
            weather_service_type,
            weather_metric,
        )
    except Exception as exc:
        log.error(
            "An error occurred whilst getting weather data: %s",
            exception_text(exc, secrets),
        )
        if status is not None:
            status.transition(
                "degraded",
                failure_at=utc_now(),
                error=sanitized_error("weather", exc, secrets=secrets),
            )
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
        log.error(
            "An error occurred whilst creating artifacts: %s",
            exception_text(exc, secrets),
        )
        if status is not None:
            status.transition(
                "degraded",
                failure_at=utc_now(),
                error=sanitized_error("render", exc, secrets=secrets),
            )
        return False

    publish_weather_snapshot(weather_publisher, snapshot)
    if status is not None:
        success_at = utc_now()
        next_refresh_at = None
        if next_refresh_seconds is not None:
            next_refresh_at = success_at + timedelta(
                seconds=next_refresh_seconds
            )
        status.transition(
            success_state,
            success_at=success_at,
            next_refresh_at=next_refresh_at,
            weather_generated_at=snapshot.generated_at,
        )
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
        return {"success": True, "error": None}

    return weather_publisher.publish_snapshot(snapshot)


def realtime_service_secrets(service):
    if service is None:
        return ()
    return tuple(
        getattr(service, name, None)
        for name in (
            "client_secret",
            "refresh_token",
        )
    )


def service_secrets(weather_service, realtime_service):
    return (
        getattr(weather_service, "apikey", None),
        *realtime_service_secrets(realtime_service),
    )


if __name__ == "__main__":
    sys.exit(main())

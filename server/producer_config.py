from dataclasses import dataclass
import math

from output_profiles import load_output_profiles
from utils import get_prop, get_prop_by_keys
from weather.providers import ConfigurationError


@dataclass(frozen=True)
class ProducerConfig:
    debug: bool
    always_on: bool
    refresh_seconds: int
    refresh_source: str
    weather_service: str
    weather_api_key: str
    weather_metric: bool
    hourly_forecasts: int
    location: str
    google_api_key: str
    static_maps_id: str
    realtime_config: dict
    mqtt_weather_config: dict
    output_profiles: dict
    default_output_profile: str

    @classmethod
    def from_config(cls, config):
        always_on = bool(
            get_prop_by_keys(
                config,
                "server",
                "alwayson",
                default=False,
                required=False,
            )
        )
        hourly_forecasts = int(
            get_prop_by_keys(
                config,
                "weather",
                "num_hourly_forecasts",
                default=6,
                required=False,
            )
        )
        if hourly_forecasts < 0:
            raise ConfigurationError(
                f"num_hourly_forecasts {hourly_forecasts} must be non-negative"
            )

        refresh_seconds, refresh_key = _refresh_seconds(config)
        if always_on and refresh_seconds == 0:
            raise ConfigurationError(
                f"{refresh_key} must be positive when server.alwayson is true"
            )

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

        mqtt_config = get_prop(
            config,
            "mqtt",
            default={},
            required=False,
        ) or {}
        output_profiles, default_output_profile = load_output_profiles(config)

        return cls(
            debug=bool(
                get_prop(config, "debug", default=False, required=False)
            ),
            always_on=always_on,
            refresh_seconds=refresh_seconds,
            refresh_source=refresh_key,
            weather_service=str(
                get_prop_by_keys(
                    config,
                    "weather",
                    "service",
                    required=True,
                )
            ),
            weather_api_key=str(
                get_prop_by_keys(
                    config,
                    "weather",
                    "apikey",
                    required=True,
                )
            ),
            weather_metric=bool(
                get_prop_by_keys(
                    config,
                    "weather",
                    "metric",
                    default=True,
                    required=False,
                )
            ),
            hourly_forecasts=hourly_forecasts,
            location=str(
                get_prop(config, "location", required=True)
            ).strip(),
            google_api_key=str(
                get_prop_by_keys(
                    config,
                    "google",
                    "apikey",
                    required=True,
                )
            ),
            static_maps_id=str(
                get_prop_by_keys(
                    config,
                    "google",
                    "staticmaps_mapid",
                    required=True,
                )
            ),
            realtime_config=realtime_config or {},
            mqtt_weather_config=(mqtt_config.get("weather") or {}),
            output_profiles=output_profiles,
            default_output_profile=default_output_profile,
        )


def producer_enabled(config):
    return bool(
        get_prop_by_keys(
            config,
            "server",
            "enabled",
            default=True,
            required=False,
        )
    )


def _refresh_seconds(config):
    server_config = config.get("server") or {}
    if "refreshminutes" in server_config:
        refresh_value = server_config["refreshminutes"]
        source_key = "server.refreshminutes"
        multiplier = 60
    elif "refreshhours" in server_config:
        refresh_value = server_config["refreshhours"]
        source_key = "server.refreshhours"
        multiplier = 3600
    else:
        refresh_value = 180
        source_key = "server.refreshminutes"
        multiplier = 60

    try:
        refresh_interval = float(refresh_value)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"{source_key} {refresh_value!r} must be a number"
        ) from None
    if not math.isfinite(refresh_interval) or refresh_interval < 0:
        raise ConfigurationError(
            f"{source_key} {refresh_interval:g} must be non-negative"
        )
    return round(refresh_interval * multiplier), source_key

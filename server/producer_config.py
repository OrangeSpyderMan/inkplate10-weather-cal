from dataclasses import dataclass

from output_profiles import load_output_profiles
from utils import get_prop, get_prop_by_keys
from weather.providers import ConfigurationError


@dataclass(frozen=True)
class ProducerConfig:
    debug: bool
    always_on: bool
    refresh_seconds: int
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

        refresh_hours = int(
            get_prop_by_keys(
                config,
                "server",
                "refreshhours",
                default=3,
                required=False,
            )
        )
        if refresh_hours < 0:
            raise ConfigurationError(
                f"server.refreshhours {refresh_hours} must be non-negative"
            )
        if always_on and refresh_hours == 0:
            raise ConfigurationError(
                "server.refreshhours must be positive when "
                "server.alwayson is true"
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
            refresh_seconds=refresh_hours * 3600,
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

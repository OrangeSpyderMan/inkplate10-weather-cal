from dataclasses import dataclass
from importlib import import_module
from os.path import isabs
from pathlib import Path


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderDefinition:
    module: str
    class_name: str

    def build(self, **kwargs):
        provider_class = getattr(import_module(self.module), self.class_name)
        return provider_class(**kwargs)


FORECAST_PROVIDERS = {
    "accuweather": ProviderDefinition(
        "weather.accuweather.accuweather",
        "AccuweatherService",
    ),
    "openweathermapv3": ProviderDefinition(
        "weather.openweathermapv3.openweathermapv3",
        "OpenWeatherMapv3Service",
    ),
    "openweathermapv4": ProviderDefinition(
        "weather.openweathermapv4.openweathermapv4",
        "OpenWeatherMapv4Service",
    ),
}

REALTIME_PROVIDERS = {
    "netatmo": ProviderDefinition(
        "weather.netatmo.netatmo",
        "NetatmoRealtimeService",
    ),
}

REMOVED_FORECAST_PROVIDERS = {
    "openweathermap": (
        "weather.service openweathermap was removed; "
        "use openweathermapv3 or openweathermapv4"
    ),
}


def build_forecast_provider(
    provider_name,
    *,
    apikey,
    location,
    metric=True,
    num_hours=6,
):
    name = _provider_name(provider_name, "weather.service")
    if name in REMOVED_FORECAST_PROVIDERS:
        raise ConfigurationError(REMOVED_FORECAST_PROVIDERS[name])

    definition = FORECAST_PROVIDERS.get(name)
    if definition is None:
        raise ConfigurationError(
            "unsupported weather.service {}; supported providers: {}".format(
                name,
                ", ".join(sorted(FORECAST_PROVIDERS)),
            )
        )

    return definition.build(
        apikey=apikey,
        location=location,
        metric=metric,
        num_hours=num_hours,
    )


def build_realtime_provider(config, *, metric=True, base_dir=None):
    realtime_config = config or {}
    name = _provider_name(
        realtime_config.get("source", "weather"),
        "current_conditions.source",
    )
    if name == "weather":
        return None

    definition = REALTIME_PROVIDERS.get(name)
    if definition is None:
        raise ConfigurationError(
            "unsupported current_conditions.source {}; supported providers: "
            "weather, {}".format(name, ", ".join(sorted(REALTIME_PROVIDERS)))
        )

    provider_config = realtime_config.get(name, realtime_config)
    kwargs = {
        "client_id": _required(provider_config, name, "client_id"),
        "client_secret": _required(provider_config, name, "client_secret"),
        "refresh_token": _required(provider_config, name, "refresh_token"),
        "token_file": provider_config.get("token_file", "netatmo-token.json"),
        "device_id": _optional(provider_config, "device_id"),
        "module_id": _optional(provider_config, "module_id"),
        "wind_module_id": _optional(provider_config, "wind_module_id"),
        "rain_module_id": _optional(provider_config, "rain_module_id"),
        "metric": metric,
    }

    if base_dir and not isabs(kwargs["token_file"]):
        kwargs["token_file"] = str(Path(base_dir) / kwargs["token_file"])

    return definition.build(**kwargs)


def _provider_name(value, key):
    if value is None or str(value).strip() == "":
        raise ConfigurationError(f"{key} is required")
    return str(value).strip().lower()


def _required(config, provider, key):
    value = config.get(key)
    if value is None or value == "":
        raise ConfigurationError(
            f"current_conditions.{provider}.{key} is required"
        )
    return value


def _optional(config, key):
    value = config.get(key)
    return None if value == "" else value

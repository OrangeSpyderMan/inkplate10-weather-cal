from dataclasses import dataclass
from importlib import import_module

from .service import ProviderConfigurationError


ConfigurationError = ProviderConfigurationError


@dataclass(frozen=True)
class ProviderDefinition:
    module: str
    builder_name: str = "build_provider"

    def build(self, config, **context):
        builder = getattr(import_module(self.module), self.builder_name)
        return builder(config, **context)


FORECAST_PROVIDERS = {
    "accuweather": ProviderDefinition(
        "weather.accuweather.accuweather",
    ),
    "openweathermapv3": ProviderDefinition(
        "weather.openweathermapv3.openweathermapv3",
    ),
    "openweathermapv4": ProviderDefinition(
        "weather.openweathermapv4.openweathermapv4",
    ),
}

REALTIME_PROVIDERS = {
    "netatmo": ProviderDefinition(
        "weather.netatmo.netatmo",
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
    forecast_slice_hours=3,
    forecast_lead_minutes=15,
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
        {
            "apikey": apikey,
            "location": location,
            "metric": metric,
            "num_hours": num_hours,
            "forecast_slice_hours": forecast_slice_hours,
            "forecast_lead_minutes": forecast_lead_minutes,
        }
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
    return definition.build(
        provider_config,
        metric=metric,
        base_dir=base_dir,
    )


def _provider_name(value, key):
    if value is None or str(value).strip() == "":
        raise ConfigurationError(f"{key} is required")
    return str(value).strip().lower()

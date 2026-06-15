import os
import json
from abc import ABC, abstractmethod

from .models import CurrentConditions, ForecastData


class ProviderConfigurationError(ValueError):
    pass


class ForecastProvider(ABC):
    def __init__(
        self, apikey, baseurl, service_name, num_hours=6, metric=True
    ):
        self.baseurl = baseurl
        self.service_name = service_name
        self.apikey = apikey
        self.units = "metric" if metric else "imperial"
        self.num_hours = num_hours

    def get_icon(self, icon_key):
        icon_key = str(icon_key)

        cwd = os.path.dirname(os.path.realpath(__file__))
        mapfile_path = os.path.join(
            cwd, "..", f"weather/{self.service_name}/icon-map.json"
        )
        icon_map = None
        with open(mapfile_path) as f:
            icon_map = json.load(f)
            f.close()

        if icon_key not in icon_map:
            return ""

        return f"icon/{icon_map[icon_key]}"

    @abstractmethod
    def fetch(self) -> ForecastData:
        """Return a complete normalized forecast."""


class RealtimeProvider(ABC):
    @abstractmethod
    def get_current_conditions(self) -> CurrentConditions:
        """Return a partial normalized current-conditions overlay."""


# Backwards-compatible name for existing forecast provider implementations.
WeatherService = ForecastProvider

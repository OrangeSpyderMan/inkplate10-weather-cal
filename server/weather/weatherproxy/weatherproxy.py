from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from .. import http
from ..models import ForecastData
from ..selection import even_select
from ..service import ProviderConfigurationError


class WeatherProxyService:
    def __init__(self, url, location, metric=True, num_hours=6,
                 forecast_slice_hours=3, forecast_lead_minutes=15, token=None):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ProviderConfigurationError("weather.url must be an HTTP(S) WeatherProxy URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ProviderConfigurationError("weather.url must not contain credentials, a query, or a fragment")
        if not location:
            raise ProviderConfigurationError("weather.location must name a proxy location")
        self.url, self.location = url.rstrip("/"), location
        self.metric, self.num_hours = metric, num_hours
        self.forecast_slice_hours, self.forecast_lead_minutes = forecast_slice_hours, forecast_lead_minutes
        self.token = token
        self.metadata = None

    def _get(self, path):
        response = http.get(f"{self.url}/api/v1/{path}",
                            headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
                            params={"units": "metric" if self.metric else "imperial"}, timeout=20)
        try:
            if response.status_code != 200:
                raise ValueError(f"WeatherProxy returned {response.status_code}")
            return response.json()
        finally:
            response.close()

    def location_metadata(self):
        if self.metadata is None:
            self.metadata = next((item for item in self._get("locations")["locations"]
                                  if item["id"] == self.location), None)
            if self.metadata is None:
                raise ValueError("Configured WeatherProxy location does not exist")
        return self.metadata

    def fetch(self):
        data = self._get(f"locations/{quote(self.location, safe='')}/weather")
        if data.get("schema_version") != "2.1" or not data.get("forecast", {}).get("available"):
            raise ValueError("WeatherProxy has no compatible forecast")
        self.metadata = data["location"]
        if self.metadata["id"] != self.location:
            raise ValueError("WeatherProxy returned a different location")
        if data.get("units") != ("metric" if self.metric else "imperial"):
            raise ValueError("WeatherProxy returned unexpected units")
        if data["forecast"].get("stale"):
            raise ValueError("WeatherProxy forecast is stale")
        cutoff = datetime.now(timezone.utc) + timedelta(minutes=self.forecast_lead_minutes)
        hourly = []
        for value in data["hourly"]:
            entry = dict(value)
            entry["dt"] = datetime.fromisoformat(entry["dt"])
            local = entry["dt"].astimezone(ZoneInfo(self.metadata["timezone"]))
            slot = local.toordinal() * 24 + local.hour
            if entry["dt"] > cutoff and (data["source"] == "accuweather" or slot % self.forecast_slice_hours == 0):
                hourly.append(entry)
        if len(hourly) < self.num_hours:
            raise ValueError("WeatherProxy has insufficient future forecast slots")
        hourly = even_select(self.num_hours, hourly) if data["source"] == "accuweather" else hourly[:self.num_hours]
        return ForecastData.from_dicts(data["current"], hourly).validate()


def build_provider(config):
    return WeatherProxyService(
        url=config.get("url", ""), location=config["location"], metric=config.get("metric", True),
        num_hours=config.get("num_hours", 6), forecast_slice_hours=config.get("forecast_slice_hours", 3),
        forecast_lead_minutes=config.get("forecast_lead_minutes", 15), token=config.get("token"),
    )

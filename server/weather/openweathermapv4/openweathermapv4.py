import math
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from ..service import WeatherService
from ..models import ForecastData


class OpenWeatherMapv4Service(WeatherService):
    def __init__(
        self,
        apikey,
        location,
        num_hours=6,
        metric=True,
        forecast_slice_hours=3,
        forecast_lead_minutes=15,
        mock=False,
    ):
        super().__init__(
            apikey,
            "https://api.openweathermap.org",
            "openweathermapv4",
            num_hours,
            metric,
            forecast_slice_hours,
            forecast_lead_minutes,
        )
        self.lat, self.lon = self._get_location_coords(location)

    def get_daily_summary(self):
        current_records, _ = self._get_records(
            "/data/4.0/onecall/current"
        )
        daily_records, _ = self._get_records(
            "/data/4.0/onecall/timeline/1day",
            required_records=1,
        )
        current = current_records[0]
        daily = daily_records[0]
        alert_ids = current.get("alerts") or []
        metric = self.units == "metric"

        summary = {
            "icon": self.get_icon(current["weather"][0]["icon"]),
            "alerts": {
                "active": bool(alert_ids),
                "ids": alert_ids,
            },
            "temperature": {
                "unit": self._temperature_unit(),
                "value": round(self._temperature_value(current["temp"])),
                "min": round(self._temperature_value(daily["temp"]["min"])),
                "max": round(self._temperature_value(daily["temp"]["max"])),
            },
        }
        if "wind_speed" in current:
            summary["wind"] = {
                "unit": "m/s" if metric else "mph",
                "value": current["wind_speed"],
            }
            if current.get("wind_gust") is not None:
                summary["wind"]["gust"] = current["wind_gust"]
            if current.get("wind_deg") is not None:
                summary["wind"]["direction"] = current["wind_deg"]
        rain_last_hour = (current.get("rain") or {}).get("1h")
        if rain_last_hour is not None:
            if not metric:
                rain_last_hour *= 0.0393701
            summary["rain"] = {
                "unit": "mm" if metric else "in",
                "value": round(rain_last_hour, 2),
                "last_hour": round(rain_last_hour, 2),
                "rate_unit": "mm/h" if metric else "in/h",
                "rate_basis": "last_hour_average",
            }
        return summary

    def fetch(self):
        return ForecastData.from_dicts(
            self.get_daily_summary(),
            self.get_hourly_forecast(),
        ).validate()

    def get_hourly_forecast(self):
        required_records = (
            self.forecast_slice_hours * self.num_hours
            + math.ceil(self.forecast_lead_minutes / 60)
            + 2
        )
        records, timezone_offset = self._get_records(
            "/data/4.0/onecall/timeline/1h",
            required_records=required_records,
        )
        location_timezone = timezone(timedelta(seconds=timezone_offset))
        cutoff = datetime.now(location_timezone) + timedelta(
            minutes=self.forecast_lead_minutes
        )
        selected = [
            entry
            for entry in records
            if self._is_forecast_slot(entry, location_timezone, cutoff)
        ][: self.num_hours]
        if len(selected) < self.num_hours:
            raise ValueError(
                "Unexpected response from weather api: "
                f"needed {self.num_hours} forecast slots, "
                f"received {len(selected)}"
            )

        return [
            {
                "dt": datetime.fromtimestamp(
                    entry["dt"],
                    location_timezone,
                ),
                "icon": self.get_icon(entry["weather"][0]["icon"]),
                "temperature": {
                    "unit": self._temperature_unit(),
                    "value": round(self._temperature_value(entry["temp"])),
                },
                "wind": {
                    "unit": "m/s" if self.units == "metric" else "mph",
                    "value": entry["wind_speed"],
                },
                "humidity": entry["humidity"],
                "rain_probability": round(entry.get("pop", 0) * 100),
            }
            for entry in selected
        ]

    def _is_forecast_slot(self, entry, location_timezone, cutoff):
        timestamp = datetime.fromtimestamp(entry["dt"], location_timezone)
        return (
            timestamp > cutoff
            and self.is_forecast_slice(timestamp)
        )

    def _get_records(self, path, required_records=1):
        url = f"{self.baseurl}{path}"
        params = self._weather_params()
        records = []
        visited = set()
        timezone_offset = 0

        while url and len(records) < required_records:
            if url in visited:
                raise ValueError("OpenWeatherMap v4 pagination loop detected")
            visited.add(url)

            data = self._get_json(url, params=params)
            params = None
            timezone_offset = int(
                data.get("timezone_offset", timezone_offset)
            )
            records.extend(data.get("data") or [])
            next_url = data.get("next")
            url = self._url_with_units(next_url) if next_url else None

        if len(records) < required_records:
            raise ValueError(
                "Unexpected response from weather api: "
                f"needed {required_records} records, received {len(records)}"
            )

        return records, timezone_offset

    def _url_with_units(self, url):
        parts = urlsplit(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key != "units"
        ]
        query.append(("units", self.units))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def _get_location_coords(self, location):
        data = self._get_json(
            f"{self.baseurl}/geo/1.0/direct",
            params={
                "q": location,
                "limit": 1,
                "appid": self.apikey,
            },
        )
        if len(data) != 1:
            raise ValueError(
                "Unexpected response from weather api: {}".format(data)
            )

        return data[0]["lat"], data[0]["lon"]

    def _get_json(self, url, params=None):
        response = requests.get(url, params=params, timeout=20)
        try:
            if response.status_code != 200:
                raise ValueError(
                    "Non-200 response from weather api: "
                    f"{response.status_code}"
                )
            return response.json()
        finally:
            response.close()

    def _weather_params(self):
        return {
            "lat": self.lat,
            "lon": self.lon,
            "appid": self.apikey,
            "units": self.units,
        }

    def _temperature_unit(self):
        return (
            "\N{DEGREE SIGN}C"
            if self.units == "metric"
            else "\N{DEGREE SIGN}F"
        )

    def _temperature_value(self, value):
        if value < 170:
            return value

        celsius = value - 273.15
        if self.units == "metric":
            return celsius
        return celsius * 9 / 5 + 32


def build_provider(config):
    return OpenWeatherMapv4Service(
        apikey=config["apikey"],
        location=config["location"],
        metric=config.get("metric", True),
        num_hours=config.get("num_hours", 6),
        forecast_slice_hours=config.get("forecast_slice_hours", 3),
        forecast_lead_minutes=config.get("forecast_lead_minutes", 15),
    )

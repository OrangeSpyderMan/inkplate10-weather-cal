from datetime import datetime

import requests

from ..service import WeatherService


class OpenWeatherMapv4Service(WeatherService):
    HOURLY_START_OFFSET = 5
    HOURLY_STEP = 3

    def __init__(self, apikey, location, num_hours=6, metric=True, mock=False):
        super().__init__(
            apikey,
            "https://api.openweathermap.org",
            "openweathermapv4",
            num_hours,
            metric,
        )
        self.lat, self.lon = self._get_location_coords(location)

    def get_daily_summary(self):
        current = self._get_records("/data/4.0/onecall/current")[0]
        daily = self._get_records(
            "/data/4.0/onecall/timeline/1day",
            required_records=1,
        )[0]

        return {
            "icon": self.get_icon(current["weather"][0]["icon"]),
            "temperature": {
                "unit": self._temperature_unit(),
                "value": round(current["temp"]),
                "min": round(daily["temp"]["min"]),
                "max": round(daily["temp"]["max"]),
            },
        }

    def get_hourly_forecast(self):
        required_records = (
            self.HOURLY_START_OFFSET
            + self.HOURLY_STEP * (self.num_hours - 1)
            + 1
        )
        records = self._get_records(
            "/data/4.0/onecall/timeline/1h",
            required_records=required_records,
        )
        selected = records[
            self.HOURLY_START_OFFSET:required_records:self.HOURLY_STEP
        ]

        return [
            {
                "dt": datetime.fromtimestamp(entry["dt"]),
                "icon": self.get_icon(entry["weather"][0]["icon"]),
                "temperature": {
                    "unit": self._temperature_unit(),
                    "value": round(entry["temp"]),
                },
                "wind": {
                    "unit": "m/s" if self.units == "metric" else "mph",
                    "real": entry["wind_speed"],
                },
                "humidity": entry["humidity"],
                "rain_probability": round(entry.get("pop", 0) * 100),
            }
            for entry in selected
        ]

    def _get_records(self, path, required_records=1):
        url = f"{self.baseurl}{path}"
        params = self._weather_params()
        records = []
        visited = set()

        while url and len(records) < required_records:
            if url in visited:
                raise ValueError("OpenWeatherMap v4 pagination loop detected")
            visited.add(url)

            data = self._get_json(url, params=params)
            params = None
            records.extend(data.get("data") or [])
            url = data.get("next")

        if len(records) < required_records:
            raise ValueError(
                "Unexpected response from weather api: "
                f"needed {required_records} records, received {len(records)}"
            )

        return records

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

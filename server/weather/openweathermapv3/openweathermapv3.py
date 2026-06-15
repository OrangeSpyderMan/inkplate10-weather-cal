import requests
import itertools
from datetime import datetime, timedelta, timezone
from ..service import WeatherService
from ..models import ForecastData


class OpenWeatherMapv3Service(WeatherService):
    def __init__(self, apikey, location, num_hours=6, metric=True, mock=False):
        super().__init__(
            apikey,
            "https://api.openweathermap.org",
            "openweathermapv3",
            num_hours,
            metric,
        )
        self.lat, self.lon = self._get_location_coords(location)

    def fetch(self):
        res = requests.get(
            self.baseurl
            + "/data/3.0/onecall?lat={}&lon={}&appid={}&units={}".format(
                self.lat, self.lon, self.apikey, self.units
            ),
            timeout=20,
        )
        try:
            if res.status_code != 200:
                raise ValueError(
                    "Non-200 response from weather api: {}".format(
                        res.status_code
                    )
                )
            data = res.json()
        finally:
            res.close()

        return ForecastData.from_dicts(
            self._daily_summary(data),
            self._hourly_forecast(data),
        ).validate()

    def _daily_summary(self, data):
        if self.units == "metric":
            units = "\N{DEGREE SIGN}C"
        else:
            units = "\N{DEGREE SIGN}F"

        forecast = {
            "icon": self.get_icon(data["current"]["weather"][0]["icon"]),
            "temperature": {
                "unit": units,
                "value": round(data["current"]["temp"]),
                "min": round(data["daily"][0]["temp"]["min"]),
                "max": round(data["daily"][0]["temp"]["max"]),
            },
        }

        return forecast

    def _hourly_forecast(self, data):
        if self.units == "metric":
            temp_units = "\N{DEGREE SIGN}C"
            speed_units = "m/s"
        else:
            temp_units = "\N{DEGREE SIGN}F"
            speed_units = "mph"

        location_timezone = timezone(
            timedelta(seconds=int(data.get("timezone_offset", 0)))
        )
        forecasts = []
        for entry in itertools.islice(
            data["hourly"],
            5,
            5 + self.num_hours * 3,
            3,
        ):
            forecast = {
                "dt": datetime.fromtimestamp(
                    entry["dt"],
                    location_timezone,
                ),
                "icon": self.get_icon(entry["weather"][0]["icon"]),
                "temperature": {
                    "unit": temp_units,
                    "value": round(entry["temp"]),
                },
                "wind": {
                    "unit": speed_units,
                    "value": entry["wind_speed"],
                },
                "humidity": entry["humidity"],
                "rain_probability": round(entry["pop"] * 100),
            }

            forecasts.append(forecast)
        if len(forecasts) < self.num_hours:
            raise ValueError(
                "Unexpected response from weather api: "
                f"needed {self.num_hours} forecast slots, "
                f"received {len(forecasts)}"
            )
        return forecasts

    def get_daily_summary(self):
        return self.fetch().current_dict()

    def get_hourly_forecast(self):
        return self.fetch().hourly_dicts()

    def _get_location_coords(self, location):
        res = requests.get(
            self.baseurl
            + "/geo/1.0/direct?q={}&limit=1&appid={}".format(
                location,
                self.apikey,
            ),
            timeout=20,
        )
        try:
            if res.status_code != 200:
                raise ValueError(
                    "Non-200 response from weather api: {}".format(
                        res.status_code
                    )
                )
            data = res.json()
        finally:
            res.close()

        if len(data) != 1:
            raise ValueError(
                "Unexpected response from weather api: {}".format(data)
            )

        return data[0]["lat"], data[0]["lon"]

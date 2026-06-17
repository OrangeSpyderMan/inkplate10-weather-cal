import requests
from datetime import datetime, timedelta, timezone
from ..service import WeatherService
from ..models import ForecastData


class OpenWeatherMapv3Service(WeatherService):
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
            "openweathermapv3",
            num_hours,
            metric,
            forecast_slice_hours,
            forecast_lead_minutes,
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
        cutoff = self._forecast_cutoff(data, location_timezone)
        selected = [
            entry
            for entry in data["hourly"]
            if self._is_forecast_slot(entry, location_timezone, cutoff)
        ][: self.num_hours]

        forecasts = []
        for entry in selected:
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

    def _forecast_cutoff(self, data, location_timezone):
        current_timestamp = data.get("current", {}).get("dt")
        if current_timestamp is not None:
            cutoff = datetime.fromtimestamp(
                current_timestamp,
                location_timezone,
            )
        else:
            cutoff = datetime.now(location_timezone)
        return cutoff + timedelta(minutes=self.forecast_lead_minutes)

    def _is_forecast_slot(self, entry, location_timezone, cutoff):
        timestamp = datetime.fromtimestamp(entry["dt"], location_timezone)
        return (
            timestamp > cutoff
            and self.is_forecast_slice(timestamp)
        )

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


def build_provider(config):
    return OpenWeatherMapv3Service(
        apikey=config["apikey"],
        location=config["location"],
        metric=config.get("metric", True),
        num_hours=config.get("num_hours", 6),
        forecast_slice_hours=config.get("forecast_slice_hours", 3),
        forecast_lead_minutes=config.get("forecast_lead_minutes", 15),
    )

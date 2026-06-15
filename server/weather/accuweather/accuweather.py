import requests
from utils import even_select
from datetime import datetime
from ..service import WeatherService
from ..models import ForecastData


class AccuweatherService(WeatherService):
    def __init__(self, apikey, location, num_hours=6, metric=True, mock=False):
        super().__init__(
            apikey,
            "https://dataservice.accuweather.com",
            "accuweather",
            num_hours,
            metric,
        )
        self.location_key = self._get_location_key(location)

    def fetch(self):
        return ForecastData.from_dicts(
            self.get_daily_summary(),
            self.get_hourly_forecast(),
        ).validate()

    def get_daily_summary(self):
        is_metric = self.units == "metric"
        path = f"{self.baseurl}/forecasts/v1/daily/1day/{self.location_key}"
        data = self._get_json(
            path,
            params={"metric": is_metric, "details": True},
        )

        if len(data) == 0:
            raise ValueError("Unexpected response from weather api: {}".format(data))

        if len(data["DailyForecasts"]) == 0:
            raise ValueError("Unexpected response from weather api: {}".format(data))

        current_conditions = self._get_current_conditions()

        data = data["DailyForecasts"][0]
        forecast = {
            "icon": self.get_icon(data["Day"]["Icon"]),
            "temperature": {
                "unit": "\N{DEGREE SIGN}C"
                if self.units == "metric"
                else "\N{DEGREE SIGN}F",
                "min": round(data["Temperature"]["Minimum"]["Value"]),
                "max": round(data["Temperature"]["Maximum"]["Value"]),
                "value": current_conditions["temperature"]["value"],
            },
            "wind": current_conditions["wind"],
            "humidity": current_conditions["humidity"],
        }

        return forecast

    def get_hourly_forecast(self):
        is_metric = self.units == "metric"
        path = f"{self.baseurl}/forecasts/v1/hourly/12hour/{self.location_key}"
        data = self._get_json(
            path,
            params={"metric": is_metric, "details": True},
        )

        if len(data) == 0:
            raise ValueError("Unexpected response from weather api: {}".format(data))

        if self.units == "metric":
            temp_units = "\N{DEGREE SIGN}C"
            speed_units = "kmh"
        else:
            temp_units = "\N{DEGREE SIGN}F"
            speed_units = "mph"

        forecasts = []
        for entry in even_select(self.num_hours, data):
            forecast = {
                "dt": datetime.fromtimestamp(entry["EpochDateTime"]),
                "icon": self.get_icon(entry["WeatherIcon"]),
                "temperature": {
                    "unit": temp_units,
                    "value": round(entry["Temperature"]["Value"]),
                },
                "wind": {
                    "unit": speed_units,
                    "value": entry["Wind"]["Speed"]["Value"],
                },
                "humidity": entry["RelativeHumidity"],
                "rain_probability": round(entry["RainProbability"]),
            }

            forecasts.append(forecast)

        return forecasts

    def _get_current_conditions(self):
        path = f"{self.baseurl}/currentconditions/v1/{self.location_key}"
        data = self._get_json(path, params={"details": True})

        if len(data) == 0:
            raise ValueError("Unexpected response from weather api: {}".format(data))

        if self.units == "metric":
            temp_units = "\N{DEGREE SIGN}C"
            speed_units = "kmh"
            units_key = "Metric"
        else:
            temp_units = "\N{DEGREE SIGN}F"
            speed_units = "mph"
            units_key = "Imperial"

        data = data[0]
        conditions = {
            "icon": self.get_icon(data["WeatherIcon"]),
            "temperature": {
                "unit": temp_units,
                "value": round(data["Temperature"][units_key]["Value"]),
            },
            "wind": {
                "unit": speed_units,
                "value": data["Wind"]["Speed"][units_key]["Value"],
            },
            "humidity": data["RelativeHumidity"],
        }

        return conditions

    def _get_location_key(self, location):
        path = f"{self.baseurl}/locations/v1/search"
        data = self._get_json(path, params={"q": location})

        if len(data) == 0:
            raise ValueError("Unexpected response from weather api: {}".format(data))
        data = data[0]
        location_key = data["Key"]

        return location_key

    def _get_json(self, url, params=None):
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.apikey}"},
            params=params,
            timeout=20,
        )
        try:
            if response.status_code != 200:
                raise ValueError(
                    "Non-200 response from weather api: "
                    f"{response.status_code}"
                )
            return response.json()
        finally:
            response.close()


def build_provider(config):
    return AccuweatherService(
        apikey=config["apikey"],
        location=config["location"],
        metric=config.get("metric", True),
        num_hours=config.get("num_hours", 6),
    )

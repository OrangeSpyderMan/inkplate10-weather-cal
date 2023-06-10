import requests
import itertools
from datetime import datetime
from ..service import WeatherService


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

    def get_daily_summary(self):
        data = None
        res = requests.get(
            self.baseurl
            + "/data/3.0/onecall?lat={}&lon={}&appid={}&units={}".format(
                self.lat, self.lon, self.apikey, self.units
            )
        )
        if res.status_code != 200:
            raise ValueError("Non-200 response from weather api: {}".format(res.history))
                
        data = res.json()

        if self.units == "metric":
            units = "\N{DEGREE SIGN}C"
        else:
            units = ("\N{DEGREE SIGN}F",)

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

    def get_hourly_forecast(self):
        data = None
        res = requests.get(
            self.baseurl
            + "/data/3.0/onecall?cnt={}&lat={}&lon={}&appid={}&units={}".format(
                self.num_hours, self.lat, self.lon, self.apikey, self.units
            )
        )
        
        if res.status_code != 200:
            raise ValueError("Non-200 response from weather api: {}".format(res.history))
        
        data = res.json()

        if self.units == "metric":
            temp_units = "\N{DEGREE SIGN}C"
            speed_units = "kmh"
        else:
            temp_units = "\N{DEGREE SIGN}F"
            speed_units = "mph"

        forecasts = []
        for entry in itertools.islice(data["hourly"],2 , self.num_hours*3, 3):
            forecast = {
                "dt": datetime.fromtimestamp(entry["dt"]),
                "icon": self.get_icon(entry["weather"][0]["icon"]),
                "temperature": {
                    "unit": temp_units,
                    "value": round(entry["temp"]),
                },
                "wind": {
                    "unit": speed_units,
                    "real": (entry["wind_speed"]),
                },
                "humidity": (entry["humidity"]),
                "rain_probability": round(entry["pop"] * 100),
            }

            forecasts.append(forecast)

        return forecasts

    def _get_location_coords(self, location):
        data = None

        res = requests.get(
            self.baseurl
            + "/geo/1.0/direct?q={}&limit=1&appid={}".format(location, self.apikey)
        )
        data = res.json()

        if res.status_code != 200:
            raise ValueError("Non-200 response from weather api: {}".format(res.history))

        if len(data) == 0 or len(data) > 1:
            raise ValueError("Unexpected response from weather api: {}".format(data))

        data = data[0]
        lat = data["lat"]
        lon = data["lon"]

        return lat, lon

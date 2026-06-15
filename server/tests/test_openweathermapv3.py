import pathlib
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from weather.openweathermapv3.openweathermapv3 import OpenWeatherMapv3Service


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class OpenWeatherMapv3ServiceTests(unittest.TestCase):
    @mock.patch("weather.openweathermapv3.openweathermapv3.requests.get")
    def test_fetch_reuses_onecall_response_for_current_and_hourly(self, get):
        geocode = FakeResponse([{"lat": 45.572, "lon": 6.739}])
        start = int(datetime(2026, 6, 15, tzinfo=timezone.utc).timestamp())
        onecall = FakeResponse(
            {
                "timezone_offset": 7200,
                "current": {
                    "temp": 12.4,
                    "weather": [{"icon": "01d"}],
                },
                "daily": [{"temp": {"min": 5.2, "max": 15.4}}],
                "hourly": [
                    {
                        "dt": start + offset * 3600,
                        "temp": 10 + offset,
                        "humidity": 60,
                        "wind_speed": 3.5,
                        "pop": 0.25,
                        "weather": [{"icon": "01d"}],
                    }
                    for offset in range(24)
                ],
            }
        )
        get.side_effect = [geocode, onecall]
        service = OpenWeatherMapv3Service(
            "weather-key",
            "Landry,FR",
            num_hours=6,
        )

        forecast = service.fetch()

        self.assertEqual(forecast.current.temperature.value, 12)
        self.assertEqual(len(forecast.hourly), 6)
        self.assertEqual(
            [item.temperature.value for item in forecast.hourly],
            [15, 18, 21, 24, 27, 30],
        )
        self.assertTrue(
            all(
                item.timestamp.utcoffset().total_seconds() == 7200
                for item in forecast.hourly
            )
        )
        self.assertEqual(get.call_count, 2)
        get.assert_has_calls(
            [
                mock.call(
                    "https://api.openweathermap.org/geo/1.0/direct"
                    "?q=Landry,FR&limit=1&appid=weather-key",
                    timeout=20,
                ),
                mock.call(
                    "https://api.openweathermap.org/data/3.0/onecall"
                    "?lat=45.572&lon=6.739&appid=weather-key&units=metric",
                    timeout=20,
                ),
            ]
        )
        self.assertTrue(geocode.closed)
        self.assertTrue(onecall.closed)


if __name__ == "__main__":
    unittest.main()

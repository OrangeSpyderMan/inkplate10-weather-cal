import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
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
                    "dt": start,
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
            [11, 14, 17, 20, 23, 26],
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

    @mock.patch("weather.openweathermapv3.openweathermapv3.requests.get")
    def test_hourly_forecast_skips_current_local_boundary(self, get):
        geocode = FakeResponse([{"lat": 45.572, "lon": 6.739}])
        location_timezone = timezone(timedelta(hours=2))
        start = datetime(2026, 6, 16, 9, tzinfo=location_timezone)
        current = start + timedelta(minutes=20)
        onecall = FakeResponse(
            {
                "timezone_offset": 7200,
                "current": {
                    "dt": int(current.timestamp()),
                    "temp": 17.1,
                    "weather": [{"icon": "03d"}],
                },
                "daily": [{"temp": {"min": 12.1, "max": 27.4}}],
                "hourly": [
                    {
                        "dt": int((start + timedelta(hours=offset)).timestamp()),
                        "temp": 20 + offset,
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

        self.assertEqual(
            [item.timestamp.strftime("%-I%p").lower() for item in forecast.hourly],
            ["12pm", "3pm", "6pm", "9pm", "12am", "3am"],
        )
        self.assertEqual(
            [item.temperature.value for item in forecast.hourly],
            [23, 26, 29, 32, 35, 38],
        )

    @mock.patch("weather.openweathermapv3.openweathermapv3.requests.get")
    def test_hourly_forecast_uses_lead_time_before_slice_boundary(self, get):
        geocode = FakeResponse([{"lat": 45.572, "lon": 6.739}])
        location_timezone = timezone(timedelta(hours=2))
        start = datetime(2026, 6, 16, 15, tzinfo=location_timezone)
        current = datetime(2026, 6, 16, 17, 59, tzinfo=location_timezone)
        onecall = FakeResponse(
            {
                "timezone_offset": 7200,
                "current": {
                    "dt": int(current.timestamp()),
                    "temp": 17.1,
                    "weather": [{"icon": "03d"}],
                },
                "daily": [{"temp": {"min": 12.1, "max": 27.4}}],
                "hourly": [
                    {
                        "dt": int((start + timedelta(hours=offset)).timestamp()),
                        "temp": 20 + offset,
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

        self.assertEqual(
            [item.timestamp.strftime("%-I%p").lower() for item in forecast.hourly],
            ["9pm", "12am", "3am", "6am", "9am", "12pm"],
        )

    @mock.patch("weather.openweathermapv3.openweathermapv3.requests.get")
    def test_hourly_forecast_uses_configured_slice_hours(self, get):
        geocode = FakeResponse([{"lat": 45.572, "lon": 6.739}])
        location_timezone = timezone(timedelta(hours=2))
        start = datetime(2026, 6, 16, 9, tzinfo=location_timezone)
        current = start + timedelta(minutes=20)
        onecall = FakeResponse(
            {
                "timezone_offset": 7200,
                "current": {
                    "dt": int(current.timestamp()),
                    "temp": 17.1,
                    "weather": [{"icon": "03d"}],
                },
                "daily": [{"temp": {"min": 12.1, "max": 27.4}}],
                "hourly": [
                    {
                        "dt": int((start + timedelta(hours=offset)).timestamp()),
                        "temp": 20 + offset,
                        "humidity": 60,
                        "wind_speed": 3.5,
                        "pop": 0.25,
                        "weather": [{"icon": "01d"}],
                    }
                    for offset in range(16)
                ],
            }
        )
        get.side_effect = [geocode, onecall]
        service = OpenWeatherMapv3Service(
            "weather-key",
            "Landry,FR",
            num_hours=6,
            forecast_slice_hours=2,
            forecast_lead_minutes=0,
        )

        forecast = service.fetch()

        self.assertEqual(
            [item.timestamp.strftime("%-I%p").lower() for item in forecast.hourly],
            ["10am", "12pm", "2pm", "4pm", "6pm", "8pm"],
        )

    @mock.patch("weather.openweathermapv3.openweathermapv3.requests.get")
    def test_hourly_forecast_supports_slice_hours_across_midnight(self, get):
        geocode = FakeResponse([{"lat": 45.572, "lon": 6.739}])
        location_timezone = timezone(timedelta(hours=2))
        start = datetime(2026, 6, 16, tzinfo=location_timezone)
        current = start
        onecall = FakeResponse(
            {
                "timezone_offset": 7200,
                "current": {
                    "dt": int(current.timestamp()),
                    "temp": 17.1,
                    "weather": [{"icon": "03d"}],
                },
                "daily": [{"temp": {"min": 12.1, "max": 27.4}}],
                "hourly": [
                    {
                        "dt": int((start + timedelta(hours=offset)).timestamp()),
                        "temp": 20 + offset,
                        "humidity": 60,
                        "wind_speed": 3.5,
                        "pop": 0.25,
                        "weather": [{"icon": "01d"}],
                    }
                    for offset in range(48)
                ],
            }
        )
        get.side_effect = [geocode, onecall]
        service = OpenWeatherMapv3Service(
            "weather-key",
            "Landry,FR",
            num_hours=6,
            forecast_slice_hours=7,
            forecast_lead_minutes=0,
        )

        forecast = service.fetch()

        self.assertEqual(
            [item.timestamp.strftime("%-I%p").lower() for item in forecast.hourly],
            ["1am", "8am", "3pm", "10pm", "5am", "12pm"],
        )


if __name__ == "__main__":
    unittest.main()

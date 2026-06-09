import json
import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_DIR = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "openweathermapv4"
)
sys.path.insert(0, str(SERVER_DIR))

from weather.openweathermapv4.openweathermapv4 import OpenWeatherMapv4Service


def fixture(name):
    with (FIXTURE_DIR / name).open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class OpenWeatherMapv4ServiceTests(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.get_patcher = mock.patch(
            "weather.openweathermapv4.openweathermapv4.requests.get",
            side_effect=self.request,
        )
        self.get = self.get_patcher.start()
        self.service = OpenWeatherMapv4Service(
            "weather-key",
            "Landry,FR",
            num_hours=6,
            metric=True,
        )

    def tearDown(self):
        self.get_patcher.stop()

    def request(self, url, params=None, timeout=None):
        if url.endswith("/geo/1.0/direct"):
            payload = fixture("geocode.json")
        elif url.endswith("/data/4.0/onecall/current"):
            payload = fixture("current.json")
        elif url.endswith("/data/4.0/onecall/timeline/1day"):
            payload = fixture("daily.json")
        elif url.endswith("/data/4.0/onecall/timeline/1h"):
            payload = fixture("hourly-page-1.json")
        elif url.endswith("timeline/1h?page=2&units=metric"):
            self.assertIsNone(params)
            payload = fixture("hourly-page-2.json")
        else:
            raise AssertionError(f"unexpected URL {url}")

        response = FakeResponse(payload)
        self.responses.append(response)
        return response

    def test_resolves_location_with_geocoding_api(self):
        self.assertEqual((self.service.lat, self.service.lon), (45.572, 6.739))
        self.get.assert_called_once_with(
            "https://api.openweathermap.org/geo/1.0/direct",
            params={
                "q": "Landry,FR",
                "limit": 1,
                "appid": "weather-key",
            },
            timeout=20,
        )

    def test_builds_daily_summary_from_current_and_daily_endpoints(self):
        summary = self.service.get_daily_summary()

        self.assertEqual(
            summary,
            {
                "icon": "icon/day/partly-clear.png",
                "temperature": {
                    "unit": "\N{DEGREE SIGN}C",
                    "value": 16,
                    "min": 10,
                    "max": 24,
                },
            },
        )

    def test_follows_hourly_pagination_for_all_six_forecast_slots(self):
        forecasts = self.service.get_hourly_forecast()

        self.assertEqual(len(forecasts), 6)
        self.assertEqual(
            [forecast["temperature"]["value"] for forecast in forecasts],
            [12, 15, 18, 21, 24, 27],
        )
        self.assertEqual(
            [forecast["rain_probability"] for forecast in forecasts],
            [2, 5, 8, 11, 14, 17],
        )
        self.assertTrue(
            all(forecast["wind"]["unit"] == "m/s" for forecast in forecasts)
        )
        self.assertEqual(
            [forecast["dt"].strftime("%-I%p").lower() for forecast in forecasts],
            ["12pm", "3pm", "6pm", "9pm", "12am", "3am"],
        )
        self.assertTrue(
            all(
                forecast["dt"].utcoffset().total_seconds() == 7200
                for forecast in forecasts
            )
        )
        second_page_call = self.get.call_args_list[-1]
        self.assertEqual(
            second_page_call,
            mock.call(
                "https://api.openweathermap.org/data/4.0/onecall/"
                "timeline/1h?page=2&units=metric",
                params=None,
                timeout=20,
            ),
        )

    def test_closes_all_http_responses(self):
        self.service.get_daily_summary()
        self.service.get_hourly_forecast()

        self.assertTrue(all(response.closed for response in self.responses))

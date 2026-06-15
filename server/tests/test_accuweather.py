import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from weather.accuweather.accuweather import AccuweatherService


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class AccuweatherServiceTests(unittest.TestCase):
    @mock.patch("weather.accuweather.accuweather.requests.get")
    def test_fetch_returns_typed_forecast_and_closes_responses(self, get):
        responses = [
            FakeResponse([{"Key": "location-key"}]),
            FakeResponse(
                {
                    "DailyForecasts": [
                        {
                            "Day": {"Icon": 1},
                            "RealFeelTemperature": {
                                "Minimum": {"Value": 5},
                                "Maximum": {"Value": 15},
                            },
                        }
                    ]
                }
            ),
            FakeResponse(
                [
                    {
                        "WeatherIcon": 1,
                        "RealFeelTemperature": {
                            "Metric": {"Value": 12},
                            "Imperial": {"Value": 54},
                        },
                        "Wind": {
                            "Speed": {
                                "Metric": {"Value": 10},
                                "Imperial": {"Value": 6},
                            }
                        },
                        "RelativeHumidity": 65,
                    }
                ]
            ),
            FakeResponse(
                [
                    {
                        "EpochDateTime": 1781510400 + offset * 3600,
                        "WeatherIcon": 1,
                        "RealFeelTemperature": {"Value": 12 + offset},
                        "Wind": {"Speed": {"Value": 10}},
                        "RelativeHumidity": 65,
                        "RainProbability": 20,
                    }
                    for offset in range(12)
                ]
            ),
        ]
        get.side_effect = responses
        service = AccuweatherService(
            "weather-key",
            "Landry,FR",
            num_hours=6,
        )

        forecast = service.fetch()

        self.assertEqual(forecast.current.temperature.value, 12)
        self.assertEqual(len(forecast.hourly), 6)
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(
            all(call.kwargs["timeout"] == 20 for call in get.call_args_list)
        )

    @mock.patch("weather.accuweather.accuweather.requests.get")
    def test_non_200_response_is_closed(self, get):
        response = FakeResponse({}, status_code=503)
        get.return_value = response

        with self.assertRaisesRegex(ValueError, "503"):
            AccuweatherService("weather-key", "Landry,FR")

        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()

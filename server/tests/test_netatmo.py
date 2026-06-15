import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from weather.netatmo.netatmo import NetatmoRealtimeService


class NetatmoRealtimeServiceTests(unittest.TestCase):
    def service(self, metric=True):
        return NetatmoRealtimeService(
            client_id="id",
            client_secret="secret",
            refresh_token="refresh",
            token_file=None,
            device_id="station",
            module_id="outdoor",
            wind_module_id="wind",
            rain_module_id="rain",
            metric=metric,
        )

    def stations_data(self):
        return {
            "body": {
                "devices": [
                    {
                        "_id": "station",
                        "modules": [
                            {
                                "_id": "outdoor",
                                "dashboard_data": {
                                    "Temperature": 12.4,
                                    "Humidity": 67,
                                },
                            },
                            {
                                "_id": "wind",
                                "dashboard_data": {
                                    "WindStrength": 18,
                                    "GustStrength": 27,
                                    "WindAngle": 245,
                                },
                            },
                            {
                                "_id": "rain",
                                "dashboard_data": {
                                    "Rain": 0.4,
                                    "sum_rain_1": 1.2,
                                    "sum_rain_24": 4.8,
                                },
                            },
                        ],
                    }
                ]
            }
        }

    def test_normalizes_all_supported_metric_measurements(self):
        service = self.service()
        service._get_stations_data = mock.Mock(return_value=self.stations_data())

        conditions = service.get_current_conditions()

        self.assertEqual(conditions["temperature"]["value"], 12)
        self.assertEqual(conditions["humidity"], 67)
        self.assertEqual(
            conditions["wind"],
            {
                "source": "netatmo",
                "live": True,
                "unit": "kmh",
                "value": 18,
                "gust": 27,
                "direction": 245,
            },
        )
        self.assertEqual(conditions["rain"]["value"], 0.4)
        self.assertEqual(conditions["rain"]["last_hour"], 1.2)
        self.assertEqual(conditions["rain"]["last_24_hours"], 4.8)

    def test_converts_measurements_to_imperial(self):
        service = self.service(metric=False)
        service._get_stations_data = mock.Mock(return_value=self.stations_data())

        conditions = service.get_current_conditions()

        self.assertEqual(conditions["temperature"]["unit"], "\N{DEGREE SIGN}F")
        self.assertEqual(conditions["temperature"]["value"], 54)
        self.assertEqual(conditions["wind"]["unit"], "mph")
        self.assertEqual(conditions["wind"]["value"], 11.2)
        self.assertEqual(conditions["rain"]["unit"], "in")
        self.assertEqual(conditions["rain"]["last_24_hours"], 0.19)


if __name__ == "__main__":
    unittest.main()

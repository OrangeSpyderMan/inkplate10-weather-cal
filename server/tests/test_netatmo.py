import pathlib
import sys
import tempfile
import time
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

        self.assertEqual(conditions.temperature.value, 12)
        self.assertEqual(conditions.humidity, 67)
        self.assertEqual(
            conditions.wind.to_dict(),
            {
                "source": "netatmo",
                "live": True,
                "unit": "kmh",
                "value": 18,
                "gust": 27,
                "direction": 245,
                "direction_cardinal": "WSW",
            },
        )
        self.assertEqual(conditions.rain.rate_unit, "mm/h")
        self.assertEqual(conditions.rain.rate_basis, "instantaneous")
        self.assertEqual(conditions.rain.value, 0.4)
        self.assertEqual(conditions.rain.last_hour, 1.2)
        self.assertEqual(conditions.rain.last_24_hours, 4.8)

    def test_converts_measurements_to_imperial(self):
        service = self.service(metric=False)
        service._get_stations_data = mock.Mock(return_value=self.stations_data())

        conditions = service.get_current_conditions()

        self.assertEqual(conditions.temperature.unit, "\N{DEGREE SIGN}F")
        self.assertEqual(conditions.temperature.value, 54)
        self.assertEqual(conditions.wind.unit, "mph")
        self.assertEqual(conditions.wind.value, 11.2)
        self.assertEqual(conditions.rain.unit, "in")
        self.assertEqual(conditions.rain.last_24_hours, 0.19)

    @mock.patch("weather.netatmo.netatmo.requests.post")
    @mock.patch("weather.netatmo.netatmo.requests.get")
    def test_unauthorized_request_uses_persisted_rotated_refresh_token(
        self,
        get,
        post,
    ):
        unauthorized = mock.Mock(status_code=401)
        successful = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value=self.stations_data()),
        )
        get.side_effect = [unauthorized, successful]
        post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(
                return_value={
                    "access_token": "replacement-access",
                    "refresh_token": "replacement-refresh",
                    "expires_in": 3600,
                }
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            token_file = pathlib.Path(temporary_dir) / "token.json"
            token_file.write_text(
                '{"access_token": "stale", "refresh_token": "rotated", '
                f'"expires_at": {time.time() + 3600}}}',
                encoding="utf-8",
            )
            service = self.service()
            service.token_file = str(token_file)

            self.assertEqual(service._get_stations_data(), self.stations_data())

            saved_token = token_file.read_text(encoding="utf-8")

        self.assertEqual(
            post.call_args.kwargs["data"]["refresh_token"],
            "rotated",
        )
        self.assertIn('"refresh_token": "replacement-refresh"', saved_token)
        post.return_value.close.assert_called_once_with()
        unauthorized.close.assert_called_once_with()
        successful.close.assert_called_once_with()

    @mock.patch("weather.netatmo.netatmo.requests.post")
    def test_refresh_preserves_refresh_token_when_response_omits_it(self, post):
        post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(
                return_value={
                    "access_token": "replacement-access",
                    "expires_in": 3600,
                }
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            token_file = pathlib.Path(temporary_dir) / "token.json"
            service = self.service()
            service.token_file = str(token_file)

            service._refresh_access_token("rotated")

            self.assertIn(
                '"refresh_token": "rotated"',
                token_file.read_text(encoding="utf-8"),
            )
        post.return_value.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

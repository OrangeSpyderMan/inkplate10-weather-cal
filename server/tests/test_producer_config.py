import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from producer_config import ProducerConfig, producer_enabled
from weather.providers import ConfigurationError


class ProducerConfigTests(unittest.TestCase):
    def test_applies_defaults_when_optional_sections_are_missing(self):
        settings = ProducerConfig.from_config(
            {
                "server": {},
                "weather": {
                    "service": "openweathermapv3",
                    "apikey": "weather-key",
                },
                "google": {
                    "apikey": "google-key",
                    "staticmaps_mapid": "map-id",
                },
                "location": "Landry, FR",
            }
        )

        self.assertFalse(settings.debug)
        self.assertFalse(settings.always_on)
        self.assertEqual(settings.refresh_seconds, 3 * 3600)
        self.assertTrue(settings.weather_metric)
        self.assertEqual(settings.hourly_forecasts, 6)
        self.assertEqual(settings.location, "Landry, FR")
        self.assertEqual(settings.realtime_config, {})
        self.assertEqual(settings.mqtt_weather_config, {})

    def test_accepts_missing_server_section_when_checking_enabled(self):
        self.assertTrue(producer_enabled({}))
        self.assertFalse(producer_enabled({"server": {"enabled": False}}))

    def test_rejects_non_positive_refresh_interval(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "server.refreshhours 0 must be positive",
        ):
            ProducerConfig.from_config(
                {
                    "server": {"refreshhours": 0},
                    "weather": {
                        "service": "openweathermapv3",
                        "apikey": "weather-key",
                    },
                    "google": {
                        "apikey": "google-key",
                        "staticmaps_mapid": "map-id",
                    },
                    "location": "Landry, FR",
                }
            )


if __name__ == "__main__":
    unittest.main()

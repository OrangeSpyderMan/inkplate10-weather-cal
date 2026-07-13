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
        self.assertEqual(settings.refresh_source, "server.refreshminutes")
        self.assertTrue(settings.weather_metric)
        self.assertEqual(settings.hourly_forecasts, 6)
        self.assertEqual(settings.forecast_slice_hours, 3)
        self.assertEqual(settings.forecast_lead_minutes, 15)
        self.assertEqual(settings.location, "Landry, FR")
        self.assertEqual(settings.realtime_config, {})
        self.assertEqual(settings.mqtt_weather_config, {})

    def test_legacy_current_temperature_config_remains_accepted(self):
        legacy_config = {
            "source": "netatmo",
            "netatmo": {
                "client_id": "id",
            },
        }
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
                "current_temperature": legacy_config,
            }
        )

        self.assertEqual(settings.realtime_config, legacy_config)

    def test_passes_shared_mqtt_instance_id_to_weather_publisher(self):
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
                "mqtt": {
                    "instance_id": "4f9k2m",
                    "weather": {"enabled": True},
                },
            }
        )

        self.assertEqual(
            settings.mqtt_weather_config["instance_id"],
            "4f9k2m",
        )

    def test_accepts_missing_server_section_when_checking_enabled(self):
        self.assertTrue(producer_enabled({}))
        self.assertFalse(producer_enabled({"server": {"enabled": False}}))

    def test_accepts_zero_refresh_interval_in_one_shot_mode(self):
        settings = ProducerConfig.from_config(
            {
                "server": {
                    "alwayson": False,
                    "refreshhours": 0,
                },
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

        self.assertFalse(settings.always_on)
        self.assertEqual(settings.refresh_seconds, 0)

    def test_accepts_refresh_interval_in_minutes(self):
        settings = ProducerConfig.from_config(
            {
                "server": {
                    "alwayson": True,
                    "refreshminutes": 15,
                },
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

        self.assertTrue(settings.always_on)
        self.assertEqual(settings.refresh_seconds, 15 * 60)
        self.assertEqual(settings.refresh_source, "server.refreshminutes")

    def test_accepts_forecast_slice_settings(self):
        settings = ProducerConfig.from_config(
            {
                "server": {},
                "weather": {
                    "service": "openweathermapv3",
                    "apikey": "weather-key",
                    "forecastslicehours": 2,
                    "forecastleadminutes": 30,
                },
                "google": {
                    "apikey": "google-key",
                    "staticmaps_mapid": "map-id",
                },
                "location": "Landry, FR",
            }
        )

        self.assertEqual(settings.forecast_slice_hours, 2)
        self.assertEqual(settings.forecast_lead_minutes, 30)

    def test_rejects_non_positive_forecast_slice_hours(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "weather.forecastslicehours 0 must be positive",
        ):
            ProducerConfig.from_config(
                {
                    "server": {},
                    "weather": {
                        "service": "openweathermapv3",
                        "apikey": "weather-key",
                        "forecastslicehours": 0,
                    },
                    "google": {
                        "apikey": "google-key",
                        "staticmaps_mapid": "map-id",
                    },
                    "location": "Landry, FR",
                }
            )

    def test_rejects_more_than_twelve_forecast_slots(self):
        with self.assertRaisesRegex(ConfigurationError, "from 0 to 12"):
            ProducerConfig.from_config(
                {
                    "server": {},
                    "weather": {
                        "service": "openweathermapv3",
                        "apikey": "weather-key",
                        "num_hourly_forecasts": 13,
                    },
                    "google": {
                        "apikey": "google-key",
                        "staticmaps_mapid": "map-id",
                    },
                    "location": "Landry, FR",
                }
            )

    def test_rejects_negative_forecast_lead_minutes(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "weather.forecastleadminutes -1 must be non-negative",
        ):
            ProducerConfig.from_config(
                {
                    "server": {},
                    "weather": {
                        "service": "openweathermapv3",
                        "apikey": "weather-key",
                        "forecastleadminutes": -1,
                    },
                    "google": {
                        "apikey": "google-key",
                        "staticmaps_mapid": "map-id",
                    },
                    "location": "Landry, FR",
                }
            )

    def test_legacy_fractional_hours_remain_supported(self):
        settings = ProducerConfig.from_config(
            {
                "server": {
                    "alwayson": True,
                    "refreshhours": 0.3,
                },
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

        self.assertEqual(settings.refresh_seconds, 18 * 60)
        self.assertEqual(settings.refresh_source, "server.refreshhours")

    def test_refresh_minutes_take_precedence_over_legacy_hours(self):
        settings = ProducerConfig.from_config(
            {
                "server": {
                    "alwayson": True,
                    "refreshminutes": 15,
                    "refreshhours": 3,
                },
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

        self.assertEqual(settings.refresh_seconds, 15 * 60)

    def test_rejects_zero_refresh_interval_in_always_on_mode(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "server.refreshhours must be positive",
        ):
            ProducerConfig.from_config(
                {
                    "server": {
                        "alwayson": True,
                        "refreshhours": 0,
                    },
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

    def test_rejects_negative_refresh_interval_in_one_shot_mode(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "server.refreshhours -1 must be non-negative",
        ):
            ProducerConfig.from_config(
                {
                    "server": {
                        "alwayson": False,
                        "refreshhours": -1,
                    },
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

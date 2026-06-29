import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import server
from weather.models import CurrentConditions, Temperature
from weather.providers import (
    ConfigurationError,
    build_forecast_provider,
    build_realtime_provider,
)


class ForecastProviderFactoryTests(unittest.TestCase):
    PROVIDERS = {
        "accuweather": "weather.accuweather.accuweather.AccuweatherService",
        "openweathermapv3": (
            "weather.openweathermapv3.openweathermapv3.OpenWeatherMapv3Service"
        ),
        "openweathermapv4": (
            "weather.openweathermapv4.openweathermapv4.OpenWeatherMapv4Service"
        ),
    }

    def test_builds_each_supported_provider(self):
        for name, class_path in self.PROVIDERS.items():
            with self.subTest(name=name), mock.patch(class_path) as provider:
                result = build_forecast_provider(
                    name,
                    apikey="key",
                    location="location",
                    metric=False,
                    num_hours=4,
                    forecast_slice_hours=2,
                    forecast_lead_minutes=30,
                )

                self.assertIs(result, provider.return_value)
                provider.assert_called_once_with(
                    apikey="key",
                    location="location",
                    metric=False,
                    num_hours=4,
                    forecast_slice_hours=2,
                    forecast_lead_minutes=30,
                )

    def test_rejects_removed_openweathermap_v2_name(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "openweathermap was removed",
        ):
            build_forecast_provider(
                "openweathermap",
                apikey="key",
                location="location",
            )

    def test_rejects_unknown_provider(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "unsupported weather.service future-weather",
        ):
            build_forecast_provider(
                "future-weather",
                apikey="key",
                location="location",
            )

    @mock.patch("server.build_forecast_provider")
    def test_server_weather_factory_delegates_to_registry(self, build_provider):
        result = server.build_weather_service(
            "openweathermapv4",
            "key",
            "location",
            True,
            6,
            3,
            15,
        )

        self.assertIs(result, build_provider.return_value)
        build_provider.assert_called_once_with(
            "openweathermapv4",
            apikey="key",
            location="location",
            metric=True,
            num_hours=6,
            forecast_slice_hours=3,
            forecast_lead_minutes=15,
        )


class RealtimeProviderFactoryTests(unittest.TestCase):
    def test_weather_source_has_no_overlay(self):
        self.assertIsNone(build_realtime_provider({"source": "weather"}))

    def test_missing_netatmo_value_raises_configuration_error(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "current_conditions.netatmo.client_secret is required",
        ):
            build_realtime_provider(
                {
                    "source": "netatmo",
                    "netatmo": {
                        "client_id": "id",
                        "refresh_token": "refresh",
                    },
                }
            )

    @mock.patch("weather.netatmo.netatmo.NetatmoRealtimeService")
    def test_builds_netatmo_with_optional_measurement_modules(self, provider):
        with tempfile.TemporaryDirectory() as temporary_dir:
            result = build_realtime_provider(
                {
                    "source": "netatmo",
                    "netatmo": {
                        "client_id": "id",
                        "client_secret": "secret",
                        "refresh_token": "refresh",
                        "token_file": "tokens.json",
                        "device_id": "station",
                        "module_id": "outdoor",
                        "wind_module_id": "wind",
                        "rain_module_id": "rain",
                    },
                },
                metric=True,
                base_dir=temporary_dir,
            )

        self.assertIs(result, provider.return_value)
        provider.assert_called_once_with(
            client_id="id",
            client_secret="secret",
            refresh_token="refresh",
            token_file=str(pathlib.Path(temporary_dir) / "tokens.json"),
            device_id="station",
            module_id="outdoor",
            wind_module_id="wind",
            rain_module_id="rain",
            metric=True,
        )

    @mock.patch("weather.netatmo.netatmo.NetatmoRealtimeService")
    def test_builds_netatmo_with_data_dir_token_default(self, provider):
        with tempfile.TemporaryDirectory() as temporary_dir:
            result = build_realtime_provider(
                {
                    "source": "netatmo",
                    "netatmo": {
                        "client_id": "id",
                        "client_secret": "secret",
                        "refresh_token": "refresh",
                    },
                },
                metric=True,
                base_dir=temporary_dir,
            )

        self.assertIs(result, provider.return_value)
        provider.assert_called_once_with(
            client_id="id",
            client_secret="secret",
            refresh_token="refresh",
            token_file=str(
                pathlib.Path(temporary_dir)
                / "data"
                / "netatmo-token.json"
            ),
            device_id=None,
            module_id=None,
            wind_module_id=None,
            rain_module_id=None,
            metric=True,
        )


class CurrentConditionsOverlayTests(unittest.TestCase):
    def test_partial_overlay_preserves_forecast_temperature_range(self):
        daily_summary = CurrentConditions(
            icon="icon/clear.png",
            temperature=Temperature(
                unit="\N{DEGREE SIGN}C",
                value=10,
                minimum=5,
                maximum=15,
            ),
        )
        realtime = mock.Mock()
        realtime.get_current_conditions.return_value = CurrentConditions(
            temperature=Temperature(
                source="netatmo",
                live=True,
                unit="\N{DEGREE SIGN}C",
                value=12,
            ),
            humidity=60,
        )

        result = server.apply_current_conditions(daily_summary, realtime)

        self.assertEqual(
            result.temperature,
            Temperature(
                source="netatmo",
                live=True,
                unit="\N{DEGREE SIGN}C",
                value=12,
                minimum=5,
                maximum=15,
            ),
        )
        self.assertEqual(result.humidity, 60)


class ProducerConfigurationTests(unittest.TestCase):
    @mock.patch(
        "server.run",
        side_effect=ConfigurationError("invalid provider"),
    )
    def test_main_reports_configuration_error_at_process_boundary(self, run):
        logger = mock.Mock()
        with mock.patch.object(server, "log", logger):
            self.assertEqual(server.main(), 1)

        logger.error.assert_called_once_with(
            "Configuration error: %s",
            mock.ANY,
        )


if __name__ == "__main__":
    unittest.main()

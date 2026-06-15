import datetime as dt
import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from weather.models import (
    CurrentConditions,
    ForecastData,
    HourlyForecast,
    Rain,
    Temperature,
    Wind,
)


class WeatherModelTests(unittest.TestCase):
    def test_serializes_normalized_forecast_contract(self):
        forecast = ForecastData(
            current=CurrentConditions(
                icon="icon/clear.png",
                temperature=Temperature(
                    unit="\N{DEGREE SIGN}C",
                    value=12,
                    minimum=5,
                    maximum=15,
                ),
            ),
            hourly=[
                HourlyForecast(
                    timestamp=dt.datetime(
                        2026,
                        6,
                        15,
                        12,
                        tzinfo=dt.timezone.utc,
                    ),
                    icon="icon/rain.png",
                    temperature=Temperature(
                        unit="\N{DEGREE SIGN}C",
                        value=13,
                    ),
                    wind=Wind(unit="m/s", value=4.2),
                    humidity=70,
                    rain_probability=40,
                )
            ],
        ).validate()

        self.assertEqual(forecast.current_dict()["temperature"]["min"], 5)
        self.assertEqual(forecast.hourly_dicts()[0]["wind"]["value"], 4.2)

    def test_realtime_overlay_preserves_forecast_temperature_range(self):
        forecast = CurrentConditions(
            icon="icon/clear.png",
            temperature=Temperature(
                unit="\N{DEGREE SIGN}C",
                value=10,
                minimum=5,
                maximum=15,
            ),
            wind=Wind(unit="m/s", value=2),
        )
        realtime = CurrentConditions(
            temperature=Temperature(
                unit="\N{DEGREE SIGN}C",
                value=12,
                source="netatmo",
                live=True,
            ),
            wind=Wind(
                unit="kmh",
                value=18,
                source="netatmo",
                live=True,
            ),
            rain=Rain(unit="mm", value=0.4),
            humidity=67,
        )

        overlaid = forecast.overlay(realtime)

        self.assertEqual(overlaid.temperature.value, 12)
        self.assertEqual(overlaid.temperature.minimum, 5)
        self.assertEqual(overlaid.temperature.maximum, 15)
        self.assertEqual(overlaid.wind.source, "netatmo")
        self.assertEqual(overlaid.rain.value, 0.4)

    def test_rejects_incomplete_forecast_contract(self):
        with self.assertRaisesRegex(ValueError, "require an icon"):
            ForecastData(
                current=CurrentConditions(
                    temperature=Temperature(
                        unit="\N{DEGREE SIGN}C",
                        value=12,
                    )
                ),
                hourly=[],
            ).validate()


if __name__ == "__main__":
    unittest.main()

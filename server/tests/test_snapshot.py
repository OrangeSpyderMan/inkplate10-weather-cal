import datetime as dt
import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from weather.snapshot import SCHEMA_VERSION, WeatherSnapshot


class WeatherSnapshotTests(unittest.TestCase):
    def test_payload_has_versioned_contract(self):
        snapshot = WeatherSnapshot(
            daily_summary={},
            hourly_forecasts=[],
            weather_source="test",
            generated_at=dt.datetime(2026, 6, 8, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(SCHEMA_VERSION, "2.0")
        self.assertEqual(snapshot.to_payload()["schema_version"], SCHEMA_VERSION)

    def test_payload_preserves_current_weather_alerts(self):
        alerts = {"active": True, "ids": ["alert-id"]}
        snapshot = WeatherSnapshot(
            daily_summary={"alerts": alerts},
            hourly_forecasts=[],
            weather_source="openweathermapv4",
        )

        self.assertEqual(snapshot.to_payload()["current"]["alerts"], alerts)

    def test_payload_preserves_hourly_forecast_timezone_offset(self):
        location_timezone = dt.timezone(dt.timedelta(hours=2))
        snapshot = WeatherSnapshot(
            daily_summary={},
            hourly_forecasts=[
                {
                    "dt": dt.datetime(
                        2026,
                        6,
                        22,
                        12,
                        tzinfo=location_timezone,
                    ),
                    "icon": "icon/day/clear.png",
                    "temperature": {"unit": "\N{DEGREE SIGN}C", "value": 27},
                    "rain_probability": 0,
                }
            ],
            weather_source="openweathermapv4",
            generated_at=dt.datetime(
                2026,
                6,
                22,
                6,
                13,
                tzinfo=dt.timezone.utc,
            ),
        )

        payload = snapshot.to_payload()

        self.assertEqual(payload["hourly"][0]["dt"], "2026-06-22T12:00:00+02:00")
        self.assertEqual(
            payload["generated_at"],
            "2026-06-22T06:13:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()

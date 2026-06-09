import datetime as dt
import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from weather.snapshot import WeatherSnapshot


class WeatherSnapshotTests(unittest.TestCase):
    def test_payload_has_versioned_contract(self):
        snapshot = WeatherSnapshot(
            daily_summary={},
            hourly_forecasts=[],
            weather_source="test",
            generated_at=dt.datetime(2026, 6, 8, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(snapshot.to_payload()["schema_version"], "1.0")


if __name__ == "__main__":
    unittest.main()

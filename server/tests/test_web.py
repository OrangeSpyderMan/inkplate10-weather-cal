import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from artifacts import ArtifactStore, DEFAULT_OUTPUT_PROFILE
from web import create_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(self.temporary_dir.name)
        self.legacy_calendar_served = mock.Mock()
        self.app = create_app(
            data_dir=self.temporary_dir.name,
            legacy_calendar_served=self.legacy_calendar_served,
        )
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_dir.cleanup()

    def test_health_is_available_without_artifacts(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_weather_and_readiness_are_unavailable_without_artifacts(self):
        weather = self.client.get("/api/v1/weather")
        ready = self.client.get("/api/v1/ready")

        self.assertEqual(weather.status_code, 503)
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(
            ready.get_json(),
            {
                "status": "not_ready",
                "snapshot": False,
                "outputs": {DEFAULT_OUTPUT_PROFILE: False},
            },
        )

    def test_serves_weather_snapshot_with_cache_validators(self):
        payload = {
            "schema_version": "1.0",
            "generated_at": "2026-06-08T08:46:24+00:00",
            "current": {},
            "hourly": [],
        }
        ArtifactStore.write_json(self.store.snapshot_path, payload)

        response = self.client.get("/api/v1/weather")
        conditional = self.client.get(
            "/api/v1/weather",
            headers={"If-None-Match": response.headers["ETag"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), payload)
        self.assertIn("no-cache", response.headers["Cache-Control"])
        self.assertIn("Last-Modified", response.headers)
        self.assertEqual(conditional.status_code, 304)

    def test_serves_named_output_and_legacy_alias(self):
        output_path = self.store.output_path(
            DEFAULT_OUTPUT_PROFILE,
            "calendar.png",
        )
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        output = self.client.get(
            "/outputs/inkplate10-portrait/calendar.png"
        )
        legacy = self.client.get("/calendar.png")

        self.assertEqual(output.status_code, 200)
        self.assertEqual(output.mimetype, "image/png")
        self.assertNotIn("attachment", output.headers.get("Content-Disposition", ""))
        self.assertEqual(legacy.status_code, 200)
        self.assertIn("attachment", legacy.headers["Content-Disposition"])
        self.legacy_calendar_served.assert_called_once_with()
        output.close()
        legacy.close()

    def test_ready_requires_snapshot_and_named_output(self):
        ArtifactStore.write_json(
            self.store.snapshot_path,
            {"schema_version": "1.0"},
        )
        output_path = self.store.output_path(
            DEFAULT_OUTPUT_PROFILE,
            "calendar.png",
        )
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        response = self.client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ready")


if __name__ == "__main__":
    unittest.main()

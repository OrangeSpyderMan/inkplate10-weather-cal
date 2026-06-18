import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from artifacts import ArtifactStore
from output_profiles import DEFAULT_OUTPUT_PROFILE, OutputProfile
from web import create_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(self.temporary_dir.name)
        self.profiles = {
            DEFAULT_OUTPUT_PROFILE: OutputProfile(
                DEFAULT_OUTPUT_PROFILE,
                "firefox",
                825,
                1200,
            ),
            "inkplate6-landscape": OutputProfile(
                "inkplate6-landscape",
                "pillow",
                800,
                600,
                filename="weather.png",
            ),
        }
        self.legacy_calendar_served = mock.Mock()
        self.app = create_app(
            data_dir=self.temporary_dir.name,
            legacy_calendar_served=self.legacy_calendar_served,
            output_profiles=self.profiles,
            default_output_profile=DEFAULT_OUTPUT_PROFILE,
        )
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_dir.cleanup()

    def test_health_is_available_without_artifacts(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_status_is_unavailable_before_producer_writes_state(self):
        response = self.client.get("/api/v1/status")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "unavailable")

    def test_status_overlays_current_artifact_readiness(self):
        ArtifactStore.write_json(
            self.store.status_path,
            {
                "schema_version": "1.0",
                "updated_at": "2026-06-18T10:00:00+00:00",
                "producer": {"state": "ready"},
                "readiness": {
                    "snapshot": True,
                    "outputs": {DEFAULT_OUTPUT_PROFILE: True},
                    "producer_cycle_complete": True,
                },
            },
        )

        response = self.client.get("/api/v1/status")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["readiness"]["snapshot"])
        self.assertFalse(
            response.get_json()["readiness"]["producer_cycle_complete"]
        )

    def test_serves_status_dashboard_assets(self):
        page = self.client.get("/status")
        trailing = self.client.get("/status/")
        css = self.client.get("/status.css")
        javascript = self.client.get("/status.js")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(trailing.status_code, 200)
        self.assertIn(b"Server status", page.data)
        self.assertIn(b"/api/v1/status", javascript.data)
        self.assertIn(b"grid-template-columns", css.data)
        page.close()
        trailing.close()
        css.close()
        javascript.close()

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
                "outputs": {
                    DEFAULT_OUTPUT_PROFILE: False,
                    "inkplate6-landscape": False,
                },
                "producer_cycle_complete": False,
            },
        )

    def test_serves_weather_snapshot_with_cache_validators(self):
        payload = {
            "schema_version": "2.0",
            "generated_at": "2026-06-08T08:46:24+00:00",
            "current": {
                "alerts": {
                    "active": True,
                    "ids": ["alert-id"],
                }
            },
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

    def test_weather_validator_matches_the_open_file_during_replacement(self):
        old_payload = {"schema_version": "2.0", "value": "old"}
        new_payload = {"schema_version": "2.0", "value": "new-and-larger"}
        ArtifactStore.write_json(self.store.snapshot_path, old_payload)
        old_size = self.store.snapshot_path.stat().st_size
        original_json_load = json.load

        def replace_after_read(snapshot_file):
            payload = original_json_load(snapshot_file)
            ArtifactStore.write_json(self.store.snapshot_path, new_payload)
            return payload

        with mock.patch("web.json.load", side_effect=replace_after_read):
            first = self.client.get("/api/v1/weather")

        second = self.client.get(
            "/api/v1/weather",
            headers={"If-None-Match": first.headers["ETag"]},
        )

        self.assertEqual(first.get_json(), old_payload)
        self.assertIn(f"-{old_size}", first.headers["ETag"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json(), new_payload)

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

    def test_serves_each_configured_profile_and_rejects_unknown_outputs(self):
        output_path = self.store.output_path(
            "inkplate6-landscape",
            "weather.png",
        )
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        output = self.client.get(
            "/outputs/inkplate6-landscape/weather.png"
        )
        wrong_filename = self.client.get(
            "/outputs/inkplate6-landscape/calendar.png"
        )
        unknown_profile = self.client.get(
            "/outputs/unknown/weather.png"
        )

        self.assertEqual(output.status_code, 200)
        self.assertEqual(wrong_filename.status_code, 404)
        self.assertEqual(unknown_profile.status_code, 404)
        output.close()

    def test_serves_content_hash_status_for_completed_output(self):
        ArtifactStore.write_json(
            self.store.snapshot_path,
            {"schema_version": "2.0"},
        )
        output_path = self.store.output_path(
            DEFAULT_OUTPUT_PROFILE,
            "calendar.png",
        )
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"calendar image")
        snapshot = mock.Mock()
        snapshot.generated_at.isoformat.return_value = (
            "2026-06-15T10:00:00+00:00"
        )
        self.store.write_ready(
            snapshot,
            {DEFAULT_OUTPUT_PROFILE: self.profiles[DEFAULT_OUTPUT_PROFILE]},
        )

        response = self.client.get(
            f"/api/v1/outputs/{DEFAULT_OUTPUT_PROFILE}/status"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "profile": DEFAULT_OUTPUT_PROFILE,
                "filename": "calendar.png",
                "url": (
                    "/outputs/inkplate10-portrait/calendar.png"
                ),
                "generated_at": "2026-06-15T10:00:00+00:00",
                "sha256": ArtifactStore.file_sha256(output_path),
            },
        )

    def test_output_status_requires_completed_output(self):
        missing = self.client.get(
            f"/api/v1/outputs/{DEFAULT_OUTPUT_PROFILE}/status"
        )
        unknown = self.client.get("/api/v1/outputs/unknown/status")

        self.assertEqual(missing.status_code, 503)
        self.assertEqual(unknown.status_code, 404)

    def test_legacy_aliases_follow_the_configured_default_profile(self):
        output_path = self.store.output_path(
            "inkplate6-landscape",
            "weather.png",
        )
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"secondary")
        app = create_app(
            data_dir=self.temporary_dir.name,
            output_profiles=self.profiles,
            default_output_profile="inkplate6-landscape",
        )
        app.config["TESTING"] = True
        client = app.test_client()

        legacy = client.get("/calendar.png")
        pwa = client.get("/app/calendar.png")

        self.assertEqual(legacy.data, b"secondary")
        self.assertEqual(pwa.data, b"secondary")
        legacy.close()
        pwa.close()

    def test_ready_requires_snapshot_and_named_output(self):
        ArtifactStore.write_json(
            self.store.snapshot_path,
            {"schema_version": "2.0"},
        )
        output_path = self.store.output_path(
            DEFAULT_OUTPUT_PROFILE,
            "calendar.png",
        )
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        secondary_path = self.store.output_path(
            "inkplate6-landscape",
            "weather.png",
        )
        secondary_path.parent.mkdir(parents=True)
        secondary_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        snapshot = mock.Mock()
        snapshot.generated_at.isoformat.return_value = (
            "2026-06-08T08:46:24+00:00"
        )
        self.store.write_ready(snapshot, self.profiles)

        response = self.client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ready")

        output_path.write_bytes(b"replacement")
        response = self.client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["producer_cycle_complete"])


if __name__ == "__main__":
    unittest.main()

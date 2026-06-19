import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from artifacts import ArtifactStore
from build_version import detected_version
from output_profiles import DEFAULT_OUTPUT_PROFILE, OutputProfile
from server_status import ServerStatus, runtime_metadata, sanitized_error


class ServerStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(self.temporary_dir.name)
        self.profiles = {
            DEFAULT_OUTPUT_PROFILE: OutputProfile(
                DEFAULT_OUTPUT_PROFILE,
                "firefox",
                825,
                1200,
            )
        }
        self.now = dt.datetime(2026, 6, 18, 10, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.temporary_dir.cleanup()

    def status(self, publisher=None):
        return ServerStatus(
            self.store,
            mode="always_on",
            refresh_seconds=180,
            forecast_provider="openweathermapv4",
            realtime_provider="netatmo",
            profiles=self.profiles,
            mqtt_publisher=publisher,
            metadata={
                "version": "v3.2.0",
                "revision": "abc123",
                "build_date": "2026-06-18T09:00:00Z",
            },
            now=lambda: self.now,
        )

    def test_writes_atomic_operational_status(self):
        status = self.status()

        status.transition("refreshing", cycle_started_at=self.now)

        payload = self.store.read_status()
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["producer"]["state"], "refreshing")
        self.assertEqual(
            payload["producer"]["cycle_started_at"],
            "2026-06-18T10:00:00+00:00",
        )
        self.assertFalse(payload["readiness"]["snapshot"])
        self.assertNotIn("apikey", json.dumps(payload).lower())

    def test_publishes_status_and_records_success(self):
        publisher = mock.Mock()
        publisher.publish_server_status.return_value = {
            "success": True,
            "error": None,
        }
        status = self.status(publisher)

        status.transition("ready", success_at=self.now)

        publisher.publish_server_status.assert_called_once()
        payload = self.store.read_status()
        self.assertTrue(payload["mqtt"]["last_publish_success"])
        self.assertIsNone(payload["mqtt"]["last_error"])

    def test_records_nonfatal_mqtt_failure_in_shared_status(self):
        publisher = mock.Mock()
        publisher.publish_server_status.return_value = {
            "success": False,
            "error": "OSError: offline",
        }
        status = self.status(publisher)

        status.transition("degraded", failure_at=self.now)

        payload = self.store.read_status()
        self.assertFalse(payload["mqtt"]["last_publish_success"])
        self.assertEqual(payload["mqtt"]["last_error"], "OSError: offline")

    def test_sanitized_error_has_no_traceback(self):
        value = sanitized_error(
            "weather",
            ValueError("provider unavailable"),
            timestamp=self.now,
        )

        self.assertEqual(
            value,
            {
                "stage": "weather",
                "type": "ValueError",
                "message": "provider unavailable",
                "timestamp": "2026-06-18T10:00:00+00:00",
            },
        )

    def test_runtime_metadata_reads_shared_version_manifest(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            metadata_path = pathlib.Path(temporary_dir) / ".version.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "version": "v3.2.0+gabc1234",
                        "revision": "abc1234",
                        "build_date": "2026-06-19T12:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            metadata = runtime_metadata(base_dir=temporary_dir)

        self.assertEqual(
            metadata,
            {
                "version": "v3.2.0+gabc1234",
                "revision": "abc1234",
                "build_date": "2026-06-19T12:00:00+00:00",
            },
        )

    def test_runtime_metadata_is_unknown_without_manifest(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            metadata = runtime_metadata(base_dir=temporary_dir)

        self.assertEqual(
            metadata,
            {
                "version": "unknown",
                "revision": "unknown",
                "build_date": "unknown",
            },
        )

    @mock.patch("build_version.git")
    def test_detected_version_uses_release_baseline_and_commit(self, git):
        git.side_effect = [
            "",
            "v3.2.0",
            "abc1234",
            "",
        ]

        self.assertEqual(detected_version(), "v3.2.0+gabc1234")

    @mock.patch("build_version.git")
    def test_detected_version_marks_dirty_checkout(self, git):
        git.side_effect = [
            "",
            "v3.2.0",
            "abc1234",
            " M server/server_status.py",
        ]

        self.assertEqual(detected_version(), "v3.2.0+gabc1234.dirty")

    @mock.patch("build_version.git")
    def test_detected_version_preserves_exact_release_tag(self, git):
        git.return_value = "v3.2.0"

        self.assertEqual(detected_version(), "v3.2.0")
        git.assert_called_once()


if __name__ == "__main__":
    unittest.main()

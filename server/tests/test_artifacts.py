import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from artifacts import ArtifactStore
from output_profiles import DEFAULT_OUTPUT_PROFILE, OutputProfile


class ArtifactStoreTests(unittest.TestCase):
    def test_writes_snapshot_and_output_paths_under_root(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            snapshot = mock.Mock()
            snapshot.generated_at = mock.Mock()
            snapshot.generated_at.isoformat.return_value = (
                "2026-06-09T00:00:00+00:00"
            )
            snapshot.to_payload.return_value = {"schema_version": "2.0"}

            store.write_snapshot(snapshot)
            output_path = store.output_path(
                DEFAULT_OUTPUT_PROFILE,
                "calendar.png",
            )
            output_path.parent.mkdir(parents=True)
            output_path.write_bytes(b"png")
            profiles = {
                DEFAULT_OUTPUT_PROFILE: OutputProfile(
                    DEFAULT_OUTPUT_PROFILE,
                    "pillow",
                    825,
                    1200,
                )
            }
            store.write_ready(snapshot, profiles)

            self.assertEqual(
                json.loads(store.snapshot_path.read_text(encoding="utf-8")),
                {"schema_version": "2.0"},
            )
            self.assertEqual(
                store.output_path(DEFAULT_OUTPUT_PROFILE, "calendar.png"),
                pathlib.Path(temporary_dir)
                / "outputs"
                / "inkplate10-portrait"
                / "calendar.png",
            )
            ready = json.loads(store.ready_path.read_text(encoding="utf-8"))
            self.assertEqual(
                ready["snapshot"]["path"],
                "weather.json",
            )
            self.assertEqual(
                ready["outputs"][DEFAULT_OUTPUT_PROFILE]["path"],
                "outputs/inkplate10-portrait/calendar.png",
            )
            self.assertEqual(
                ready["outputs"][DEFAULT_OUTPUT_PROFILE]["sha256"],
                "8f8cbb7dcf46e0bc7d53265749a6c17d"
                "116093a6ba95e442764060c76fd4a86c",
            )
            self.assertEqual(
                ready["snapshot"]["sha256"],
                ArtifactStore.file_sha256(store.snapshot_path),
            )
            self.assertTrue(store.producer_cycle_complete(profiles))

            output_path.write_bytes(b"new output")

            self.assertFalse(store.producer_cycle_complete(profiles))

    def test_detects_content_changes_when_file_metadata_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            snapshot = mock.Mock()
            snapshot.generated_at.isoformat.return_value = (
                "2026-06-09T00:00:00+00:00"
            )
            snapshot.to_payload.return_value = {"value": "old"}
            profile = OutputProfile(
                DEFAULT_OUTPUT_PROFILE,
                "pillow",
                825,
                1200,
            )
            profiles = {DEFAULT_OUTPUT_PROFILE: profile}

            store.write_snapshot(snapshot)
            output_path = store.output_path(
                DEFAULT_OUTPUT_PROFILE,
                profile.filename,
            )
            output_path.parent.mkdir(parents=True)
            output_path.write_bytes(b"old")
            store.write_ready(snapshot, profiles)
            original_stat = output_path.stat()

            output_path.write_bytes(b"new")
            os.utime(
                output_path,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            self.assertFalse(store.producer_cycle_complete(profiles))

    def test_upgrades_legacy_ready_manifest_when_signatures_match(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            profile = OutputProfile(
                DEFAULT_OUTPUT_PROFILE,
                "pillow",
                825,
                1200,
            )
            profiles = {DEFAULT_OUTPUT_PROFILE: profile}
            ArtifactStore.write_json(
                store.snapshot_path,
                {"schema_version": "1.0"},
            )
            output_path = store.output_path(
                DEFAULT_OUTPUT_PROFILE,
                profile.filename,
            )
            output_path.parent.mkdir(parents=True)
            output_path.write_bytes(b"legacy output")
            ArtifactStore.write_json(
                store.ready_path,
                {
                    "generated_at": "2026-06-09T00:00:00+00:00",
                    "snapshot": {
                        "path": store.snapshot_path.name,
                        "signature": store.file_signature(store.snapshot_path),
                    },
                    "outputs": {
                        DEFAULT_OUTPUT_PROFILE: {
                            "path": str(output_path.relative_to(store.root)),
                            "signature": store.file_signature(output_path),
                            "renderer": profile.renderer,
                        }
                    },
                },
            )

            with mock.patch.object(
                store,
                "file_sha256",
                wraps=store.file_sha256,
            ) as file_sha256:
                self.assertTrue(store.producer_cycle_complete(profiles))
            self.assertEqual(file_sha256.call_count, 2)

            ready = json.loads(store.ready_path.read_text(encoding="utf-8"))
            self.assertEqual(
                ready["snapshot"]["sha256"],
                store.file_sha256(store.snapshot_path),
            )
            self.assertEqual(
                ready["outputs"][DEFAULT_OUTPUT_PROFILE]["sha256"],
                store.file_sha256(output_path),
            )

    def test_does_not_upgrade_legacy_manifest_with_changed_output(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            profile = OutputProfile(
                DEFAULT_OUTPUT_PROFILE,
                "pillow",
                825,
                1200,
            )
            profiles = {DEFAULT_OUTPUT_PROFILE: profile}
            ArtifactStore.write_json(
                store.snapshot_path,
                {"schema_version": "1.0"},
            )
            output_path = store.output_path(
                DEFAULT_OUTPUT_PROFILE,
                profile.filename,
            )
            output_path.parent.mkdir(parents=True)
            output_path.write_bytes(b"legacy output")
            output_signature = store.file_signature(output_path)
            ArtifactStore.write_json(
                store.ready_path,
                {
                    "generated_at": "2026-06-09T00:00:00+00:00",
                    "snapshot": {
                        "path": store.snapshot_path.name,
                        "signature": store.file_signature(store.snapshot_path),
                    },
                    "outputs": {
                        DEFAULT_OUTPUT_PROFILE: {
                            "path": str(output_path.relative_to(store.root)),
                            "signature": output_signature,
                            "renderer": profile.renderer,
                        }
                    },
                },
            )
            output_path.write_bytes(b"changed output")

            self.assertFalse(store.producer_cycle_complete(profiles))

            ready = json.loads(store.ready_path.read_text(encoding="utf-8"))
            self.assertNotIn(
                "sha256",
                ready["outputs"][DEFAULT_OUTPUT_PROFILE],
            )

    def test_removes_only_stale_temporary_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            stale = store.root / ".weather.json.old.tmp"
            current = store.root / ".weather.json.current.tmp"
            output = store.output_path(DEFAULT_OUTPUT_PROFILE, "calendar.png")
            output.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")
            current.write_text("current", encoding="utf-8")
            output.write_bytes(b"png")
            os.utime(stale, (100, 100))
            os.utime(current, (190, 190))

            removed = store.cleanup_stale_temporary_files(
                max_age_seconds=50,
                now=200,
            )

            self.assertEqual(removed, [stale])
            self.assertFalse(stale.exists())
            self.assertTrue(current.exists())
            self.assertTrue(output.exists())

    def test_rejects_negative_temporary_file_max_age(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)

            with self.assertRaises(ValueError):
                store.cleanup_stale_temporary_files(max_age_seconds=-1)


if __name__ == "__main__":
    unittest.main()

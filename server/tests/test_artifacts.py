import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from artifacts import ArtifactStore, DEFAULT_OUTPUT_PROFILE


class ArtifactStoreTests(unittest.TestCase):
    def test_writes_snapshot_and_output_paths_under_root(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            snapshot = mock.Mock()
            snapshot.to_payload.return_value = {"schema_version": "1.0"}

            store.write_snapshot(snapshot)

            self.assertEqual(
                json.loads(store.snapshot_path.read_text(encoding="utf-8")),
                {"schema_version": "1.0"},
            )
            self.assertEqual(
                store.output_path(DEFAULT_OUTPUT_PROFILE, "calendar.png"),
                pathlib.Path(temporary_dir)
                / "outputs"
                / "inkplate10-portrait"
                / "calendar.png",
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

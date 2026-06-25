import hashlib
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import hardware_soak


class HardwareSoakTests(unittest.TestCase):
    def setUp(self):
        self.state = hardware_soak.FaultState()

    def request(self, path):
        return hardware_soak.fixture_response(self.state, path)

    def test_ready_and_unchanged_fixture_uses_stable_hash(self):
        first = self.request("/status")
        second = self.request("/status")

        self.assertEqual(first, second)
        self.assertEqual(first[0], 200)
        self.assertIn(hardware_soak.READY_HASH.encode(), first[2])
        image = self.request("/calendar.bmp")
        self.assertEqual(
            hashlib.sha256(image[2]).hexdigest(),
            hardware_soak.READY_HASH,
        )

    def test_status_unavailable_returns_503_while_image_remains_available(self):
        self.state.set_mode("status-unavailable")

        status = self.request("/status")
        image = self.request("/calendar.bmp")

        self.assertEqual(status[0], 503)
        self.assertEqual(image[0], 200)
        self.assertEqual(image[1], "image/bmp")
        self.assertTrue(image[2].startswith(b"BM"))

    def test_image_failure_changes_hash_and_rejects_download(self):
        self.state.set_mode("image-failure")

        status = self.request("/status")
        image = self.request("/calendar.bmp")

        self.assertEqual(status[0], 200)
        self.assertIn(hardware_soak.FAILED_HASH.encode(), status[2])
        self.assertEqual(image[0], 503)

    def test_fixture_is_full_inkplate_portrait_bitmap(self):
        image = hardware_soak.monochrome_bmp()

        self.assertTrue(image.startswith(b"BM"))
        self.assertEqual(int.from_bytes(image[18:22], "little"), 825)
        self.assertEqual(int.from_bytes(image[22:26], "little"), 1200)

    def test_soak_controls_are_compile_time_only(self):
        library = (REPO_ROOT / "src/lib.cpp").read_text(encoding="utf-8")
        sketch = (REPO_ROOT / "src/src.ino").read_text(encoding="utf-8")

        self.assertIn("#ifdef INKPLATE_SOAK_BATTERY_VOLTAGE", library)
        self.assertIn("#ifdef INKPLATE_SOAK_SLEEP_SECONDS", library)
        self.assertIn(
            "status=failed retaining=previous",
            sketch,
        )


if __name__ == "__main__":
    unittest.main()

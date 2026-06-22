import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from pillow_renderer import PillowCalendarRenderer
from renderers import build_renderer


class PillowCalendarRendererTests(unittest.TestCase):
    def test_registry_builds_pillow_renderer(self):
        self.assertIsInstance(build_renderer("pillow"), PillowCalendarRenderer)

    def test_renders_complete_grayscale_calendar_atomically(self):
        snapshot = SimpleNamespace(
            daily_summary={
                "icon": "icon/cloudy.png",
                "alerts": {"active": True, "ids": ["alert-id"]},
                "temperature": {
                    "unit": "\N{DEGREE SIGN}C",
                    "value": 19,
                    "min": 10,
                    "max": 31,
                    "live": True,
                },
                "rain": {
                    "unit": "mm",
                    "value": 0.4,
                    "rate_unit": "mm/h",
                    "live": True,
                },
                "wind": {
                    "unit": "m/s",
                    "value": 5,
                    "direction": 245,
                    "direction_cardinal": "WSW",
                    "live": True,
                },
            },
            hourly_forecasts=[
                {
                    "dt": dt.datetime(
                        2026,
                        6,
                        22,
                        hour,
                        tzinfo=dt.timezone(dt.timedelta(hours=2)),
                    ),
                    "icon": "icon/cloudy.png",
                    "temperature": {
                        "unit": "\N{DEGREE SIGN}C",
                        "value": temperature,
                    },
                    "rain_probability": rain,
                }
                for hour, temperature, rain in (
                    (8, 19, 5),
                    (11, 24, 20),
                    (14, 29, 60),
                    (17, 27, 80),
                    (20, 23, 40),
                    (23, 20, 10),
                )
            ],
            generated_at=dt.datetime(
                2026,
                6,
                22,
                6,
                13,
                tzinfo=dt.timezone.utc,
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            map_path = root / "map.png"
            Image.new("L", (825, 400), 200).save(map_path)
            output = root / "calendar.png"

            PillowCalendarRenderer().render(
                snapshot,
                str(map_path),
                output,
                825,
                1200,
                {"supersample": 1},
            )

            self.assertTrue(output.is_file())
            self.assertFalse((root / ".calendar.tmp.png").exists())
            with Image.open(output) as rendered:
                self.assertEqual(rendered.mode, "L")
                self.assertEqual(rendered.size, (825, 1200))
                self.assertLess(rendered.getextrema()[0], 10)
                self.assertGreater(rendered.getextrema()[1], 245)

    def test_scales_to_configured_output_dimensions(self):
        snapshot = SimpleNamespace(
            daily_summary={
                "temperature": {"unit": "C", "value": 10},
                "icon": "icon/cloudy.png",
            },
            hourly_forecasts=[],
            generated_at=None,
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            output = pathlib.Path(temporary_dir) / "calendar.png"
            PillowCalendarRenderer().render(
                snapshot,
                "missing-map.png",
                output,
                600,
                448,
                {"supersample": 1},
            )
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (600, 448))

    def test_preserves_firefox_reference_map_and_circle_geometry(self):
        snapshot = SimpleNamespace(
            daily_summary={
                "temperature": {"unit": "C", "value": 10},
                "icon": "icon/cloudy.png",
                "rain": {"unit": "mm", "value": 0, "rate_unit": "mm/h"},
                "wind": {"unit": "kmh", "value": 1},
            },
            hourly_forecasts=[],
            generated_at=dt.datetime(
                2026,
                6,
                22,
                tzinfo=dt.timezone.utc,
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            map_path = root / "map.png"
            Image.new("L", (825, 400), 200).save(map_path)
            output = root / "calendar.png"
            PillowCalendarRenderer().render(
                snapshot,
                str(map_path),
                output,
                825,
                1200,
                {"supersample": 1},
            )
            with Image.open(output) as rendered:
                pixels = rendered.load()
                self.assertGreater(pixels[400, 239], 245)
                self.assertEqual(pixels[400, 240], 200)
                self.assertEqual(pixels[400, 639], 200)
                self.assertGreater(pixels[400, 640], 245)
                for center in ((94, 318), (94, 499), (731, 318), (731, 499)):
                    self.assertLess(pixels[center[0] - 45, center[1]], 10)
                self.assertEqual(pixels[20, 318], 200)
                self.assertEqual(pixels[805, 318], 200)


if __name__ == "__main__":
    unittest.main()

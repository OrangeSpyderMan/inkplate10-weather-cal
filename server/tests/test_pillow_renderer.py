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
                zero_bar_stroke = rendered.crop((70, 1104, 130, 1115))
                self.assertLess(zero_bar_stroke.getextrema()[0], 50)

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

    def test_preserves_map_and_circle_geometry(self):
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

    def test_rain_probability_bar_heights(self):
        from unittest.mock import MagicMock
        from pillow_renderer import CalendarCanvas

        canvas = CalendarCanvas(825, 1200, supersample=1)
        canvas.rough.line = MagicMock()
        canvas.rough.hatched_rectangle = MagicMock()
        canvas.rough.polyline = MagicMock()
        canvas.rough.ellipse = MagicMock()

        canvas._text = MagicMock()
        canvas._icon = MagicMock()
        canvas._outlined_text = MagicMock()

        hourly = [
            {"dt": dt.datetime(2026, 6, 22, 8), "icon": "icon/cloudy.png", "temperature": {"value": 10, "unit": "C"}, "rain_probability": 0},
            {"dt": dt.datetime(2026, 6, 22, 11), "icon": "icon/cloudy.png", "temperature": {"value": 10, "unit": "C"}, "rain_probability": 1},
            {"dt": dt.datetime(2026, 6, 22, 14), "icon": "icon/cloudy.png", "temperature": {"value": 10, "unit": "C"}, "rain_probability": 2},
        ]

        canvas._forecast(hourly)

        hatched_rect_calls = canvas.rough.hatched_rectangle.call_args_list
        self.assertEqual(len(hatched_rect_calls), 2)

        # 1% rain probability bar should be clamped to 4 pixels minimum height
        box_1 = hatched_rect_calls[0][0][0]
        left_1, top_1, right_1, bottom_1 = box_1
        self.assertEqual(bottom_1 - top_1, 4)

        # 2% rain probability bar height (5.7 rounded to 6)
        box_2 = hatched_rect_calls[1][0][0]
        left_2, top_2, right_2, bottom_2 = box_2
        self.assertEqual(bottom_2 - top_2, 6)

        # 0% rain probability should be drawn as a baseline using line()
        self.assertTrue(canvas.rough.line.called)

    def test_renders_all_eight_configured_forecast_slots(self):
        from unittest.mock import MagicMock
        from pillow_renderer import CalendarCanvas

        canvas = CalendarCanvas(825, 1200, supersample=1)
        canvas.rough.line = MagicMock()
        canvas.rough.hatched_rectangle = MagicMock()
        canvas.rough.polyline = MagicMock()
        canvas.rough.ellipse = MagicMock()
        canvas._text = MagicMock()
        canvas._icon = MagicMock()
        canvas._outlined_text = MagicMock()
        hourly = [
            {
                "dt": dt.datetime(2026, 7, 13, 21) + dt.timedelta(hours=3 * index),
                "icon": "icon/cloudy.png",
                "temperature": {"value": 20 + index, "unit": "C"},
                "rain_probability": index,
            }
            for index in range(8)
        ]

        canvas._forecast(hourly)

        self.assertEqual(canvas._icon.call_count, 8)
        self.assertEqual(canvas._outlined_text.call_count, 8)
        rendered_hours = [call.args[0] for call in canvas._text.call_args_list[:8]]
        self.assertEqual(
            rendered_hours,
            ["9pm", "12am", "3am", "6am", "9am", "12pm", "3pm", "6pm"],
        )

    def test_small_nonzero_rain_bar_survives_scaled_border_inset(self):
        from pillow_renderer import CalendarCanvas

        for width, height, supersample in (
            (825, 1200, 1),
            (825, 1200, 2),
            (600, 448, 1),
            (600, 448, 2),
        ):
            with self.subTest(
                width=width,
                height=height,
                supersample=supersample,
            ):
                canvas = CalendarCanvas(
                    width,
                    height,
                    supersample=supersample,
                )
                canvas._hatched_bar(100, 1106, 200, 1110)
                if supersample > 1:
                    canvas.image = canvas.image.resize(
                        (width, height),
                        Image.Resampling.LANCZOS,
                    )

                left = canvas._x(100) // supersample
                right = canvas._x(200) // supersample
                top = max(0, canvas._y(1100) // supersample)
                bottom = min(height, canvas._y(1110) // supersample + 1)
                bar = canvas.image.crop((left, top, right, bottom))
                self.assertLess(bar.getextrema()[0], 250)

    def test_wind_measurement_text_keeps_clear_bottom_margin(self):
        from pillow_renderer import CalendarCanvas

        center = (94, 499)
        canvas = CalendarCanvas(825, 1200, supersample=1)
        canvas._measurement_circle(
            center,
            "18",
            "km/h",
            detail="WSW",
        )

        white_pixels = [
            (x, y)
            for y in range(center[1] - 55, center[1] + 56)
            for x in range(center[0] - 20, center[0] + 21)
            if canvas.image.getpixel((x, y)) > 245
        ]
        self.assertTrue(white_pixels)
        self.assertLessEqual(
            max(y for _, y in white_pixels),
            center[1] + 50,
        )


if __name__ == "__main__":
    unittest.main()

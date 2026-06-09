import datetime as dt
import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from views.calendar import CalendarPage


class CalendarPageTests(unittest.TestCase):
    def test_temperature_line_uses_monotone_curve(self):
        page = CalendarPage(825, 1200)
        page.template(
            map_url="map.png",
            daily_summary={
                "icon": "icon/cloudy.png",
                "temperature": {
                    "unit": "\N{DEGREE SIGN}C",
                    "value": 16,
                },
            },
            hourly_forecasts=[
                {
                    "dt": dt.datetime(2026, 6, 9, 18),
                    "icon": "icon/cloudy.png",
                    "temperature": {
                        "unit": "\N{DEGREE SIGN}C",
                        "value": 13,
                    },
                    "rain_probability": 25,
                },
                {
                    "dt": dt.datetime(2026, 6, 9, 21),
                    "icon": "icon/rainy.png",
                    "temperature": {
                        "unit": "\N{DEGREE SIGN}C",
                        "value": 17,
                    },
                    "rain_probability": 50,
                },
            ],
        )

        html = str(page.airium)
        self.assertIn("cubicInterpolationMode: 'monotone'", html)
        self.assertIn("lineTension: 0.4", html)

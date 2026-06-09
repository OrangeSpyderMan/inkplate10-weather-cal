import datetime as dt
import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from views.calendar import CalendarPage


class CalendarPageTests(unittest.TestCase):
    def test_renders_warning_triangle_for_active_weather_alert(self):
        html = self._render({"active": True, "ids": ["alert-id"]})

        self.assertIn('id="weather-alert"', html)
        self.assertIn("Active weather alert", html)

    def test_omits_warning_triangle_without_active_weather_alert(self):
        html = self._render({"active": False, "ids": []})

        self.assertNotIn('id="weather-alert"', html)

    def _render(self, alerts):
        page = CalendarPage(825, 1200)
        page.template(
            map_url="map.png",
            daily_summary={
                "icon": "icon/cloudy.png",
                "alerts": alerts,
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
                }
            ],
        )
        return str(page.airium)


if __name__ == "__main__":
    unittest.main()

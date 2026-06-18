import datetime as dt
import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from views.calendar import CalendarPage


class CalendarPageTests(unittest.TestCase):
    def test_renders_notification_icon_for_active_weather_alert(self):
        html = self._render({"active": True, "ids": ["alert-id"]})

        self.assertIn('id="weather-alert"', html)
        self.assertIn('src="icon/notification.png"', html)
        self.assertIn("Active weather alert", html)

    def test_omits_notification_icon_without_active_weather_alert(self):
        html = self._render({"active": False, "ids": []})

        self.assertNotIn('id="weather-alert"', html)

    def test_renders_live_rain_and_wind_in_left_measurement_circles(self):
        html = self._render(
            {"active": False, "ids": []},
            rain={
                "unit": "mm",
                "value": 0.4,
                "rate_unit": "mm/h",
                "rate_basis": "instantaneous",
                "live": True,
            },
            wind={
                "unit": "kmh",
                "value": 18,
                "direction": 245,
                "direction_cardinal": "WSW",
                "live": True,
            },
        )

        self.assertIn('id="rain"', html)
        self.assertIn('id="wind"', html)
        self.assertIn("mm/h", html)
        self.assertIn("WSW", html)
        self.assertIn('src="icon/compass.png"', html)
        self.assertIn('class="wind-compass-badge"', html)
        self.assertIn('class="wind-compass"', html)
        self.assertIn("--wind-direction: 245deg", html)
        self.assertEqual(html.count('class="live-radio"'), 2)

    def test_renders_provider_fallback_without_live_antenna(self):
        html = self._render(
            {"active": False, "ids": []},
            rain={
                "unit": "mm",
                "value": 1.2,
                "rate_unit": "mm/h",
                "rate_basis": "last_hour_average",
            },
            wind={"unit": "m/s", "value": 5},
        )

        self.assertIn('id="rain"', html)
        self.assertIn('id="wind"', html)
        self.assertNotIn('class="live-radio"', html)
        self.assertIn("18", html)
        self.assertIn("km/h", html)

    def test_omits_missing_rain_and_wind_circles(self):
        html = self._render({"active": False, "ids": []})

        self.assertNotIn('id="rain"', html)
        self.assertNotIn('id="wind"', html)

    def test_renders_snapshot_generation_time_in_utc_footer(self):
        html = self._render(
            {"active": False, "ids": []},
            generated_at=dt.datetime(
                2026,
                6,
                18,
                9,
                32,
                tzinfo=dt.timezone(dt.timedelta(hours=2)),
            ),
        )

        self.assertIn('id="last-refreshed"', html)
        self.assertIn("Last Refreshed: 07:32 UTC, 18 June 2026", html)

    def _render(self, alerts, rain=None, wind=None, generated_at=None):
        page = CalendarPage(825, 1200)
        daily_summary = {
            "icon": "icon/cloudy.png",
            "alerts": alerts,
            "temperature": {
                "unit": "\N{DEGREE SIGN}C",
                "value": 16,
            },
        }
        if rain is not None:
            daily_summary["rain"] = rain
        if wind is not None:
            daily_summary["wind"] = wind
        page.template(
            map_url="map.png",
            daily_summary=daily_summary,
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
            generated_at=generated_at,
        )
        return str(page.airium)


if __name__ == "__main__":
    unittest.main()

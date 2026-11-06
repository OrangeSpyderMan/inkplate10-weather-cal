import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_proxy.config import expand, read_environment, validate
from weather_proxy.migrate import activate, build_plan, write_plan


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "server/config/config.yaml"
        self.source.parent.mkdir(parents=True)
        self.environment = self.root / "weather.env"
        self.environment.write_text('WEATHER_API_KEY="private-forecast-key"\nNETATMO_CLIENT_ID=id\nNETATMO_CLIENT_SECRET="secret\\$dollar"\nNETATMO_REFRESH_TOKEN=old-seed\n')
        self.calendar = {
            "server": {"refreshminutes": 180, "port": 8080},
            "weather": {"service": "openweathermapv3", "apikey": "${WEATHER_API_KEY}",
                        "metric": False, "num_hourly_forecasts": 6, "forecastslicehours": 3},
            "current_conditions": {"source": "weather"}, "location": "Landry, FR",
            "google": {"apikey": "${GOOGLE_API_KEY}", "staticmaps_mapid": "keep-map"},
            "outputs": {"default": "custom", "profiles": {"custom": {"width": 825}}},
            "mqtt": {"weather": {"enabled": True, "broker": "old-broker", "base_topic": "inkplate/weather"},
                     "diagnostics": {"enabled": True, "topic": "keep-diagnostics"}},
        }
        self.save()
        self.destination = self.root / "plan"

    def save(self):
        self.source.write_text(yaml.safe_dump(self.calendar))

    def plan(self, **overrides):
        options = {"latitude": 45.57, "longitude": 6.74, "timezone_name": "Europe/Paris",
                   "proxy_url": "http://proxy:8090", "location_id": "landry", "env_path": self.environment}
        options.update(overrides)
        return build_plan(self.source, **options)

    def ready(self, *, stale=False):
        start = datetime.now(timezone.utc)
        return {"schema_version": "2.1", "location": {"id": "landry", "timezone": "Europe/Paris"},
                "units": "imperial", "forecast": {"available": True, "stale": stale},
                "source": "openweathermapv3", "current": {"icon": "icon/clear.png", "temperature": {"unit": "°F", "value": 50}},
                "hourly": [{"dt": (start + timedelta(hours=index)).isoformat(), "icon": "icon/clear.png",
                            "temperature": {"unit": "°F", "value": 50}, "rain_probability": 0}
                           for index in range(1, 25)]}

    def test_all_forecast_providers_preserve_calendar_and_private_credentials(self):
        for provider in ("openweathermapv3", "openweathermapv4", "accuweather"):
            with self.subTest(provider=provider):
                self.calendar["weather"]["service"] = provider
                self.save()
                plan = self.plan(mqtt_broker="lan-broker")
                self.assertEqual(plan["proxy"]["accounts"]["forecast"]["provider"], provider)
                self.assertEqual(plan["proxy"]["mqtt"]["broker"], "lan-broker")
                self.assertEqual(plan["calendar"]["outputs"], self.calendar["outputs"])
                self.assertEqual(plan["calendar"]["google"], self.calendar["google"])
                self.assertEqual(plan["calendar"]["mqtt"], self.calendar["mqtt"])
                self.assertFalse(plan["calendar"]["weather"]["metric"])
                self.assertNotIn("apikey", plan["calendar"]["weather"])
                self.assertEqual(plan["secrets"]["WEATHER_API_KEY"], "private-forecast-key")
                validate(expand(copy.deepcopy(plan["proxy"]), plan["secrets"]))
        self.assertNotIn("private-forecast-key", yaml.safe_dump(plan["proxy"]))

    def test_latest_netatmo_token_and_legacy_module_selection(self):
        self.calendar.pop("current_conditions")
        self.calendar["current_temperature"] = {"source": "netatmo", "netatmo": {
            "client_id": "${NETATMO_CLIENT_ID}", "client_secret": "${NETATMO_CLIENT_SECRET}",
            "refresh_token": "${NETATMO_REFRESH_TOKEN}", "device_id": "station", "module_id": "outdoor",
            "wind_module_id": "wind", "rain_module_id": "rain"}}
        self.save()
        tokens = self.root / "server/data/netatmo-token.json"
        tokens.parent.mkdir()
        tokens.write_text(json.dumps({"refresh_token": "latest-rotated"}))
        plan = self.plan(netatmo_user_id="owner")
        self.assertEqual(plan["secrets"]["NETATMO_REFRESH_TOKEN"], "latest-rotated")
        self.assertEqual(plan["secrets"]["NETATMO_CLIENT_SECRET"], "secret$dollar")
        self.assertEqual(plan["proxy"]["locations"]["landry"]["observations"][0]["wind_module_id"], "wind")
        self.assertNotIn("current_temperature", plan["calendar"])
        self.assertEqual(plan["calendar"]["current_conditions"], {"source": "weather"})
        write_plan(plan, self.destination)
        tokens.write_text(json.dumps({"refresh_token": "rotated-again"}))
        with self.assertRaisesRegex(ValueError, "tokens changed"):
            activate(self.destination)

    def test_dry_run_has_no_writes_or_secrets_in_output(self):
        original = self.source.read_bytes()
        output = io.StringIO()
        with mock.patch("sys.stdout", output), mock.patch("requests.get") as get:
            write_plan(self.plan(), self.destination, dry_run=True)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.source.read_bytes(), original)
        self.assertNotIn("private-forecast-key", output.getvalue())
        get.assert_not_called()

    def test_private_outputs_and_unchanged_source(self):
        original = self.source.read_bytes()
        write_plan(self.plan(), self.destination)
        self.assertEqual(self.source.read_bytes(), original)
        for name in ("weather.env", "weather-proxy.yaml", "calendar-proxy.yaml", "migration.json"):
            self.assertEqual((self.destination / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_environment(self.destination / "weather.env")["WEATHER_API_KEY"], "private-forecast-key")

    def test_cutover_requires_usable_forecast_and_preserves_backup(self):
        os.chmod(self.source, 0o666)
        original = self.source.read_bytes()
        write_plan(self.plan(), self.destination)
        response = mock.Mock(status_code=200)
        response.json.return_value = self.ready(stale=True)
        with mock.patch("weather_proxy.migrate.requests.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "fresh forecast"):
                activate(self.destination)
            self.assertEqual(self.source.read_bytes(), original)
            response.json.return_value = self.ready()
            activate(self.destination, verify_url="http://localhost:8090")
        self.assertEqual(self.source.with_name("config.yaml.direct-provider.bak").read_bytes(), original)
        self.assertEqual(yaml.safe_load(self.source.read_text())["weather"]["service"], "weatherproxy")
        self.assertEqual(self.source.stat().st_mode & 0o022, 0)
        self.assertEqual(self.source.with_name("config.yaml.direct-provider.bak").stat().st_mode & 0o777, 0o600)

    def test_changed_source_is_not_overwritten(self):
        write_plan(self.plan(), self.destination)
        self.source.write_text(self.source.read_text() + "# edited\n")
        with mock.patch("weather_proxy.migrate.requests.get") as get, self.assertRaisesRegex(ValueError, "configuration changed"):
            activate(self.destination)
        get.assert_not_called()

    def test_cutover_rechecks_source_after_network_response(self):
        write_plan(self.plan(), self.destination)
        response = mock.Mock(status_code=200)
        def snapshot():
            self.source.write_text(self.source.read_text() + "# changed during readiness check\n")
            return self.ready()
        response.json.side_effect = snapshot
        with mock.patch("weather_proxy.migrate.requests.get", return_value=response), self.assertRaisesRegex(ValueError, "configuration changed"):
            activate(self.destination)
        self.assertFalse(self.source.with_name("config.yaml.direct-provider.bak").exists())

    def test_cutover_waits_for_data_without_changing_calendar_early(self):
        write_plan(self.plan(), self.destination)
        unavailable = mock.Mock(status_code=503)
        ready = mock.Mock(status_code=200)
        ready.json.return_value = self.ready()
        with mock.patch("weather_proxy.migrate.requests.get", side_effect=[unavailable, ready]) as get, mock.patch("weather_proxy.migrate.time.sleep"):
            activate(self.destination, wait_seconds=1)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(yaml.safe_load(self.source.read_text())["weather"]["service"], "weatherproxy")

    def test_netatmo_cutover_requires_configured_module_data(self):
        self.calendar["current_conditions"] = {"source": "netatmo", "netatmo": {
            "client_id": "${NETATMO_CLIENT_ID}", "client_secret": "${NETATMO_CLIENT_SECRET}",
            "refresh_token": "${NETATMO_REFRESH_TOKEN}", "device_id": "station", "module_id": "outdoor"}}
        self.save()
        write_plan(self.plan(netatmo_user_id="owner"), self.destination)
        response = mock.Mock(status_code=200)
        response.json.return_value = self.ready()
        with mock.patch("weather_proxy.migrate.requests.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "Netatmo module"):
                activate(self.destination)
        self.assertEqual(yaml.safe_load(self.source.read_text())["weather"]["service"], "openweathermapv3")


if __name__ == "__main__":
    unittest.main()

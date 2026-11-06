"""Behavioral tests runnable in the standalone image without the calendar."""

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather.models import ForecastData
from weather.weatherproxy.weatherproxy import WeatherProxyService
from weather_proxy.api import create_app
from weather_proxy.config import validate
from weather_proxy.state import convert_units, iso
from weather_proxy.storage import SQLiteStorage, open_storage
from weather_proxy.worker import Worker
from weather.http import RateLimited, request_budget, get


NOW = 1800000000.0


def configuration():
    return {
        "accounts": {
            "owm": {"provider": "openweathermapv3", "apikey": "private-key"},
            "station": {"provider": "netatmo", "client_id": "id", "client_secret": "private-secret",
                        "refresh_token": "private-refresh", "user_id": "owner"},
        },
        "locations": {
            "landry": {"name": "Landry", "latitude": 45.57, "longitude": 6.74,
                       "timezone": "Europe/Paris", "forecast": {"account": "owm"},
                       "observations": [{"account": "station", "device_id": "main", "module_id": "outdoor",
                                         "wind_module_id": "wind", "rain_module_id": "rain"}]},
            "home": {"name": "Home", "latitude": 50, "longitude": 1,
                     "timezone": "Europe/London", "observations": [{"account": "station", "device_id": "second"}]},
        },
    }


def forecast(now=NOW):
    return {"current": {"icon": "icon/clear.png", "temperature": {"unit": "°C", "value": 10, "min": 5, "max": 15}},
            "hourly": [{"dt": iso(now + index * 3600), "icon": "icon/clear.png",
                        "temperature": {"unit": "°C", "value": 10}, "rain_probability": 0}
                       for index in range(1, 25)],
            "source": "openweathermapv3", "fetched_at": iso(now)}


def stations(now=NOW):
    return {"body": {"devices": [
        {"_id": "main", "dashboard_data": {"time_utc": now, "Temperature": 21, "CO2": 700},
         "modules": [{"_id": "outdoor", "dashboard_data": {"time_utc": now, "Temperature": 12.4, "Humidity": 65}},
                     {"_id": "wind", "dashboard_data": {"time_utc": now, "WindStrength": 10, "GustStrength": 20}},
                     {"_id": "rain", "dashboard_data": {"time_utc": now, "Rain": 1.2, "sum_rain_1": 2}}]},
        {"_id": "second", "dashboard_data": {"time_utc": now, "Temperature": 19}},
    ]}}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "proxy.sqlite3"
        self.store = SQLiteStorage(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_revision_restart_and_isolation(self):
        self.assertEqual(self.store.put("weather/a", {"current": 1}), 1)
        self.assertEqual(self.store.put("weather/a", {"current": 2}), 2)
        restarted = SQLiteStorage(self.path)
        self.assertEqual(restarted.get("weather/a"), {"value": {"current": 2}, "revision": 2})
        restarted.put("tokens/private", {"token": "secret"})
        self.assertEqual(list(restarted.scan("weather/")), ["weather/a"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_atomic_budget_with_competing_clients(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.reserve("shared", NOW, 5, 3600), range(20)))
        self.assertEqual(sum(results), 5)
        self.assertTrue(self.store.reserve("shared", NOW + 3600, 5, 3600))

    def test_durable_deduplication_and_worker_lease(self):
        self.assertTrue(self.store.enqueue("digest", "a", {"value": 1}, NOW))
        self.assertFalse(self.store.enqueue("digest", "a", {"value": 1}, NOW))
        self.assertEqual(len(SQLiteStorage(self.path).pending()), 1)
        self.store.acknowledge("digest")
        self.assertEqual(self.store.pending(), [])
        self.assertFalse(self.store.enqueue("digest", "a", {}, NOW + 10))
        self.assertTrue(self.store.lease("worker", "first", NOW, 30))
        self.assertFalse(self.store.lease("worker", "second", NOW, 30))
        self.assertTrue(self.store.lease("worker", "second", NOW + 31, 30))
        self.store.release("worker", "first")
        self.assertFalse(self.store.lease("worker", "third", NOW + 32, 30))

    def test_future_backend_has_explicit_failure(self):
        with self.assertRaisesRegex(ValueError, "Unsupported storage backend"):
            open_storage({"backend": "cloud"})


class ProxyTests(StorageTests):
    def setUp(self):
        super().setUp()
        self.config = validate(configuration())
        self.now = NOW
        self.worker = Worker(self.config, self.store, clock=lambda: self.now)
        self.worker.providers["netatmo/station"] = mock.Mock()
        self.worker.providers["netatmo/station"].get_stations_data.return_value = stations()
        self.provider = mock.Mock()
        data = forecast()
        self.provider.fetch.return_value = ForecastData.from_dicts(data["current"], data["hourly"])
        self.worker.providers["landry"] = self.provider
        self.app = create_app(self.config, self.store, lambda: self.now)
        self.client = self.app.test_client()

    def send_event(self, payload=None, **headers):
        payload = payload or {"user_id": "owner", "device_id": "main", "push_type": "device_event"}
        raw = json.dumps(payload).encode()
        signature = hmac.new(b"private-secret", raw, hashlib.sha256).hexdigest()
        return self.client.post("/api/v1/integrations/netatmo/webhook", data=raw,
                                content_type="application/json", headers={"X-Netatmo-secret": signature, **headers})

    def test_discovery_has_metadata_and_no_credentials(self):
        response = self.client.get("/api/v1/locations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json["locations"]], ["landry", "home"])
        self.assertNotIn("private", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/v1/locations/missing/weather").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/locations/landry/weather").status_code, 503)

    def test_cache_reads_do_not_fetch_and_one_account_supplies_two_locations(self):
        self.worker.tick()
        for _ in range(10):
            self.assertEqual(self.client.get("/api/v1/locations/landry/weather").status_code, 200)
        self.worker.providers["netatmo/station"].get_stations_data.assert_called_once()
        self.provider.fetch.assert_called_once()
        payload = self.client.get("/api/v1/locations/landry/weather").json
        self.assertEqual(payload["current"]["temperature"]["value"], 12.4)
        self.assertEqual(payload["current"]["temperature"]["min"], 5)
        self.assertEqual(payload["current"]["wind"]["value"], 10)
        self.assertEqual(self.client.get("/api/v1/locations/home/weather").json["current"]["temperature"]["value"], 19)
        self.assertEqual(payload["measurement_metadata"]["temperature"]["observed_at"], iso(NOW))

    def test_etag_and_units(self):
        self.worker.tick()
        response = self.client.get("/api/v1/locations/landry/weather")
        self.assertEqual(self.client.get("/api/v1/locations/landry/weather", headers={"If-None-Match": response.headers["ETag"]}).status_code, 304)
        imperial = self.client.get("/api/v1/locations/landry/weather?units=imperial").json
        self.assertAlmostEqual(imperial["current"]["temperature"]["value"], 54.32)
        self.assertEqual(imperial["current"]["rain"]["rate_unit"], "in/h")
        self.assertEqual(imperial["observations"][0]["measurements"]["temperature"]["unit"], "°F")
        self.assertEqual(self.client.get("/api/v1/locations/landry/weather?units=kelvin").status_code, 400)

    def test_webhook_signature_identity_and_duplicate_persistence(self):
        self.assertEqual(self.send_event(**{"X-Netatmo-secret": "invalid"}).status_code, 403)
        self.assertEqual(self.send_event({"user_id": "other", "push_type": "device_event"}).status_code, 403)
        self.assertEqual(self.send_event({"user_id": "owner", "device_id": "unknown", "push_type": "device_event"}).json, {"accepted": False})
        self.assertEqual(self.send_event().json, {"accepted": True})
        self.assertEqual(self.send_event().json, {"accepted": False})
        self.assertEqual(len(SQLiteStorage(self.path).pending()), 1)
        with self.assertLogs(self.app.logger, level="ERROR"), mock.patch.object(self.store, "enqueue", side_effect=OSError("disk full")):
            self.assertEqual(self.send_event().status_code, 500)

    def test_events_coalesce_and_do_not_refresh_forecast(self):
        self.worker.tick()
        self.now += 10
        self.send_event()
        self.worker.tick()
        self.worker.providers["netatmo/station"].get_stations_data.assert_called_once()
        self.now += 60
        self.worker.tick()
        self.assertEqual(self.worker.providers["netatmo/station"].get_stations_data.call_count, 2)
        self.provider.fetch.assert_called_once()
        self.assertEqual(self.store.pending(), [])

    def test_partial_old_and_same_measurements_do_not_erase_or_refresh(self):
        self.worker.fetch_observations("station")
        self.now += 60
        self.worker.save_module("station", "main", {"_id": "outdoor", "dashboard_data": {"time_utc": NOW + 60, "Humidity": 70}})
        self.worker.save_module("station", "main", {"_id": "outdoor", "dashboard_data": {"time_utc": NOW - 60, "Temperature": 1}})
        self.worker.save_module("station", "main", {"_id": "outdoor", "dashboard_data": {"time_utc": NOW, "Temperature": 2}})
        measurements = self.store.get("observations/station/main/outdoor")["value"]["measurements"]
        self.assertEqual(measurements["temperature"]["value"], 12.4)
        self.assertEqual(measurements["temperature"]["received_at"], iso(NOW))
        self.assertEqual(measurements["humidity"]["value"], 70)

    def test_stale_observations_fall_back_and_stopped_worker_does_not_look_live(self):
        self.worker.tick()
        self.now += 1900
        old = self.client.get("/api/v1/locations/landry/weather").json
        self.assertFalse(old["current"]["temperature"]["live"])
        self.worker.tick()
        current = self.client.get("/api/v1/locations/landry/weather").json
        self.assertEqual(current["current"]["temperature"]["value"], 10)
        self.assertEqual(current["measurement_metadata"]["temperature"]["kind"], "forecast")

    def test_upstream_failures_keep_cached_state_and_backoff_hides_secrets(self):
        self.worker.tick()
        self.now += 11000
        self.provider.fetch.side_effect = ValueError("https://provider?apikey=private-key")
        self.worker.tick()
        count = self.provider.fetch.call_count
        self.worker.tick()
        self.assertEqual(self.provider.fetch.call_count, count)
        self.assertEqual(self.client.get("/api/v1/locations/landry/weather").status_code, 200)
        self.assertNotIn("private-key", self.client.get("/api/v1/status").get_data(as_text=True))

    def test_shared_account_cooldown_and_budget(self):
        self.config["accounts"]["owm"]["requests_per_hour"] = 1
        self.worker.guard("owm")
        with self.assertRaises(RateLimited):
            self.worker.guard("owm")
        response = mock.Mock(status_code=429, headers={"Retry-After": "120"})
        with mock.patch("weather.http.requests.get", return_value=response):
            with self.assertRaises(RateLimited) as caught:
                get("https://upstream.example")
        self.assertEqual(caught.exception.retry_after, 120)
        response.close.assert_called_once()

    def test_api_auth_does_not_bypass_webhook_signature(self):
        self.config["api_token"] = "local-token"
        self.assertEqual(self.client.get("/api/v1/locations").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/locations", headers={"Authorization": "Bearer local-token"}).status_code, 200)
        self.assertEqual(self.send_event().status_code, 200)
        self.assertEqual(self.send_event(**{"X-Netatmo-secret": "invalid", "Authorization": "Bearer local-token"}).status_code, 403)

    def test_no_repeated_publication_revision_without_state_change(self):
        self.worker.tick()
        original = self.store.get("weather/landry")
        self.now += 1
        self.worker.tick()
        self.assertEqual(self.store.get("weather/landry"), original)

    def test_calendar_adapter_receives_normalized_forecast_and_metadata(self):
        self.worker.tick()
        adapter = WeatherProxyService("http://proxy", "landry", num_hours=2, forecast_slice_hours=1)
        response = mock.Mock(status_code=200)
        response.json.return_value = self.client.get("/api/v1/locations/landry/weather").json
        with mock.patch("weather.weatherproxy.weatherproxy.http.get", return_value=response), mock.patch("weather.weatherproxy.weatherproxy.datetime") as date:
            from datetime import datetime, timezone
            date.now.return_value = datetime.fromtimestamp(NOW, timezone.utc)
            date.fromisoformat.side_effect = datetime.fromisoformat
            result = adapter.fetch()
        self.assertEqual(len(result.hourly), 2)
        self.assertEqual(result.current.temperature.value, 12.4)
        self.assertEqual(adapter.location_metadata()["id"], "landry")

    def test_mqtt_reconnect_replays_without_changing_measurement_times(self):
        from weather_proxy.mqtt import Publisher
        self.worker.tick()
        payload = self.client.get("/api/v1/locations/landry/weather").json
        with mock.patch("weather_proxy.mqtt.mqtt.Client") as factory:
            client = factory.return_value
            client.publish.return_value.rc = 0
            client.publish.return_value.is_published.return_value = True
            publisher = Publisher({"broker": "broker"})
            publisher.on_connect(client, None, None, 0, None)
            self.assertTrue(publisher.publish("landry", payload))
            count = client.publish.call_count
            self.assertTrue(publisher.publish("landry", payload))
            self.assertEqual(client.publish.call_count, count)
            publisher.on_connect(client, None, None, 0, None)
            self.assertTrue(publisher.publish("landry", payload))
            message = json.loads(client.publish.call_args_list[-4].args[1])
            self.assertEqual(message["measurement_metadata"], payload["measurement_metadata"])
            self.assertTrue(publisher.remove("landry"))
            self.assertEqual(client.publish.call_args.args[1], b"")
            publisher.close()

    def test_competing_worker_cannot_fetch_or_publish(self):
        self.worker.tick()
        second = Worker(self.config, SQLiteStorage(self.path), clock=lambda: self.now)
        with mock.patch.object(second, "fetch_observations") as fetch:
            self.assertFalse(second.tick())
            fetch.assert_not_called()
        self.worker.close()
        self.assertTrue(second.tick())
        second.close()

    def test_location_change_cannot_serve_previous_coordinates_forecast(self):
        self.worker.tick()
        self.config["locations"]["landry"]["latitude"] = 40
        self.assertEqual(self.client.get("/api/v1/locations/landry/weather").status_code, 503)
        self.worker.tick()
        self.assertEqual(self.provider.fetch.call_count, 2)
        self.assertEqual(self.client.get("/api/v1/locations/landry/weather").json["location"]["latitude"], 40)


if __name__ == "__main__":
    unittest.main()

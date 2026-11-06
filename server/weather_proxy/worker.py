import copy
import hashlib
import json
import random
import time
import uuid

from weather.http import RateLimited, request_budget
from weather.models import cardinal_direction
from weather.netatmo.netatmo import NetatmoRealtimeService
from weather.providers import FORECAST_PROVIDERS

from .state import compose, iso, location_signature


class StoredNetatmo(NetatmoRealtimeService):
    def __init__(self, account_id, account, store):
        super().__init__(account["client_id"], account["client_secret"], account["refresh_token"], None)
        self.account_id, self.store = account_id, store
        self.identity = hashlib.sha256(json.dumps({key: account[key] for key in
            ("client_id", "client_secret", "refresh_token", "user_id")}, sort_keys=True).encode()).hexdigest()

    def _load_token_data(self):
        document = self.store.get(f"tokens/{self.account_id}")
        if document and document["value"].get("identity") == self.identity:
            return document["value"]["token"]
        return {"refresh_token": self.refresh_token}

    def _save_token_data(self, data):
        self.store.put(f"tokens/{self.account_id}", {"identity": self.identity, "token": data})


class Worker:
    def __init__(self, config, store, publisher=None, clock=time.time):
        self.config, self.store, self.publisher, self.clock = config, store, publisher, clock
        self.owner = uuid.uuid4().hex
        self.providers = {}

    def guard(self, account_id):
        now = self.clock()
        if not self.store.lease("worker", self.owner, now, 300):
            raise RateLimited(30)
        account = self.config["accounts"][account_id]
        cooldown = self.store.get(f"cooldown/{account_id}")
        if cooldown and cooldown["value"]["until"] > now:
            raise RateLimited(cooldown["value"]["until"] - now)
        for window, limit in ((3600, account.get("requests_per_hour", 100)),
                              (10, account.get("requests_per_10_seconds", 2 if account["provider"] == "netatmo" else 20))):
            if not self.store.reserve(account_id, now, int(limit), window):
                raise RateLimited(window)

    def job(self, key, account, interval, operation, force=False):
        now = self.clock()
        document = self.store.get(f"jobs/{key}")
        status = document["value"] if document else {}
        if status.get("next_at", 0) > now and not (force and status.get("retry_at", 0) <= now):
            return
        try:
            with request_budget(lambda: self.guard(account)):
                operation()
            status = {"next_at": self.clock() + interval, "last_success_at": iso(self.clock()),
                      "failures": 0, "retry_at": 0, "error": None}
        except Exception as exc:
            failures = status.get("failures", 0) + 1
            delay = min(3600, 30 * 2 ** min(failures - 1, 7)) + random.uniform(0, 5)
            if isinstance(exc, RateLimited):
                delay = max(delay, exc.retry_after)
                self.store.put(f"cooldown/{account}", {"until": self.clock() + delay})
            # Provider exception text can contain URLs, credentials or bodies.
            # Operational status deliberately records only a safe category.
            status.update(next_at=self.clock() + delay, retry_at=self.clock() + delay,
                          failures=failures, error=type(exc).__name__)
        self.store.put(f"jobs/{key}", status)

    def fetch_forecast(self, name, location):
        settings = location["forecast"]
        account = self.config["accounts"][settings["account"]]
        if name not in self.providers:
            self.providers[name] = FORECAST_PROVIDERS[account["provider"]].build({
                "apikey": account["apikey"], "location": location, "metric": True,
                "num_hours": settings.get("num_hours", 12 if account["provider"] == "accuweather" else 24), "forecast_slice_hours": 1,
                "forecast_lead_minutes": 0,
            })
        forecast = self.providers[name].fetch().validate()
        current = forecast.current_dict()
        for field in ("temperature", "wind", "rain"):
            if field in current:
                current[field].update(source=account["provider"], live=False)
        value = {"current": current, "hourly": forecast.hourly_dicts(),
                 "source": account["provider"], "fetched_at": iso(self.clock()),
                 "configuration": location_signature(location)}
        # Date objects belong to the provider model, not the storage contract.
        value = json.loads(json.dumps(value, default=lambda value: value.isoformat()))
        self.store.put(f"forecast/{name}", value)

    def netatmo(self, account_id):
        key = f"netatmo/{account_id}"
        if key not in self.providers:
            self.providers[key] = StoredNetatmo(account_id, self.config["accounts"][account_id], self.store)
        return self.providers[key]

    def fetch_observations(self, account_id):
        data = self.netatmo(account_id).get_stations_data()
        devices = data.get("body", {}).get("devices")
        if not isinstance(devices, list):
            raise ValueError("Missing station data")
        for device in devices:
            station = device["_id"]
            for module in [device, *device.get("modules", [])]:
                self.save_module(account_id, station, module)

    def save_module(self, account, station, module):
        module_id = module["_id"]
        key = f"observations/{account}/{station}/{module_id}"
        existing = self.store.get(key)
        value = copy.deepcopy(existing["value"]) if existing else {
            "account": account, "device_id": station, "module_id": module_id,
            "source": "netatmo", "measurements": {},
        }
        value["reachable"] = module.get("reachable", True)
        dashboard = module.get("dashboard_data", {})
        measured_at = dashboard.get("time_utc")
        now = self.clock()
        if isinstance(measured_at, (int, float)) and 0 < measured_at <= now + 300:
            measured_at = iso(measured_at)
            fields = {}
            for raw, normalized, unit in (("Temperature", "temperature", "°C"),
                    ("Humidity", "humidity", "%"), ("CO2", "co2", "ppm"),
                    ("Pressure", "pressure", "hPa"), ("Noise", "noise", "dB")):
                if raw in dashboard:
                    fields[normalized] = {"value": dashboard[raw], "unit": unit}
            if "WindStrength" in dashboard:
                fields["wind"] = {"value": dashboard["WindStrength"], "unit": "kmh"}
                for raw, normalized in (("GustStrength", "gust"), ("WindAngle", "direction")):
                    if raw in dashboard:
                        fields["wind"][normalized] = dashboard[raw]
                if "direction" in fields["wind"]:
                    fields["wind"]["direction_cardinal"] = cardinal_direction(fields["wind"]["direction"])
            if "Rain" in dashboard:
                fields["rain"] = {"value": dashboard["Rain"], "unit": "mm",
                                  "rate_unit": "mm/h", "rate_basis": "instantaneous"}
                for raw, normalized in (("sum_rain_1", "last_hour"), ("sum_rain_24", "last_24_hours")):
                    if raw in dashboard:
                        fields["rain"][normalized] = dashboard[raw]
            for field, measurement in fields.items():
                old = value["measurements"].get(field)
                if not old or measured_at > old["observed_at"]:
                    value["measurements"][field] = {
                        **measurement, "observed_at": measured_at, "received_at": iso(now),
                    }
        if not existing or value != existing["value"]:
            self.store.put(key, value)

    def accept_events(self):
        for event in self.store.pending():
            # Weather payloads are not specified sufficiently to apply deltas.
            # Persist invalidation before ack: a crash can only repeat a poll.
            if event["payload"].get("push_type") in ("device_event", "topology_changed", "home_event_changed", "webhook_activation"):
                self.store.put(f"invalidated/{event['account']}", {"at": event["received"]})
            self.store.put(f"webhook/{event['account']}", {"last_received_at": iso(event["received"]),
                                                       "type": event["payload"].get("push_type")})
            self.store.acknowledge(event["digest"])

    def tick(self):
        if not self.store.lease("worker", self.owner, self.clock(), 300):
            return False
        self.accept_events()
        if self.publisher:
            published = self.store.get("mqtt/locations")
            known = set(published["value"]["names"]) if published else set()
            active = set(self.config["locations"])
            removed = {name for name in known - active if self.publisher.remove(name)}
            self.store.put("mqtt/locations", {"names": sorted((known - removed) | active)})
        for account_id, account in self.config["accounts"].items():
            if account["provider"] != "netatmo":
                continue
            invalidation = self.store.get(f"invalidated/{account_id}")
            job = self.store.get(f"jobs/observations/{account_id}")
            last_success = job and job["value"].get("last_success_at")
            force = bool(invalidation and (not last_success or
                         iso(invalidation["value"]["at"]) > last_success))
            if last_success:
                from .state import epoch
                force = force and self.clock() - epoch(last_success) >= account.get("event_min_seconds", 60)
            self.job(f"observations/{account_id}", account_id, account.get("reconcile_minutes", 30) * 60,
                     lambda account_id=account_id: self.fetch_observations(account_id), force)
        for name, location in self.config["locations"].items():
            self.store.lease("worker", self.owner, self.clock(), 300)
            if location.get("forecast"):
                account = location["forecast"]["account"]
                cached = self.store.get(f"forecast/{name}")
                changed = not cached or cached["value"].get("configuration") != location_signature(location)
                self.job(f"forecast/{name}", account, location["forecast"].get("refresh_minutes", 180) * 60,
                         lambda: self.fetch_forecast(name, location), force=changed)
            payload = compose(self.store, name, location, self.config["accounts"], self.clock())
            if payload is None:
                continue
            existing = self.store.get(f"weather/{name}")
            comparison = {k: v for k, v in existing["value"].items() if k not in ("revision", "generated_at")} if existing else None
            if payload != comparison:
                revision = (existing["revision"] + 1) if existing else 1
                payload.update(revision=revision, generated_at=iso(self.clock()))
                self.store.put(f"weather/{name}", payload)
            else:
                payload = existing["value"]
            if self.publisher:
                success = self.publisher.publish(name, payload)
                self.store.put(f"mqtt/status/{name}", {"success": success,
                               "revision": payload["revision"], "attempt_at": iso(self.clock())})
        self.store.put("worker/status", {"last_tick_at": iso(self.clock())})
        return True

    def close(self):
        self.store.release("worker", self.owner)

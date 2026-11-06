"""Cache-only HTTP reads and durable, authenticated webhook ingestion."""

import hashlib
import hmac
import json
import re
import time

from flask import Flask, abort, jsonify, request

from .config import load_config, validate
from .state import convert_units, public_location, location_signature
from .storage import open_storage


def create_app(config=None, store=None, clock=time.time):
    config = validate(config) if config is not None else load_config()
    store = store or open_storage(config.get("storage", {}))
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

    @app.before_request
    def authorize():
        if request.path in ("/api/v1/health", "/api/v1/integrations/netatmo/webhook"):
            return
        token = config.get("api_token")
        if token and not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
            abort(401)

    @app.get("/api/v1/health")
    def health():
        return jsonify(status="ok")

    @app.get("/api/v1/locations")
    def locations():
        return conditional({"locations": [public_location(name, location)
                            for name, location in config["locations"].items()]})

    def location_data(name):
        if name not in config["locations"]:
            abort(404)
        document = store.get(f"weather/{name}")
        if document is None:
            abort(503, description="Location data is not yet available")
        payload = document["value"]
        if payload.get("configuration") != location_signature(config["locations"][name]):
            abort(503, description="Waiting for data for the updated location configuration")
        units = request.args.get("units", "metric")
        if units not in ("metric", "imperial"):
            abort(400, description="units must be metric or imperial")
        payload = convert_units(payload, units)
        # Readers also detect a stopped worker without updating cached records.
        now = clock()
        from .state import epoch
        if payload.get("forecast", {}).get("fetched_at"):
            maximum = config["locations"][name].get("forecast", {}).get("stale_minutes", 360) * 60
            payload["forecast"]["stale"] |= now - epoch(payload["forecast"]["fetched_at"]) > maximum
        for observation in payload.get("observations", []):
            for measurement in observation["measurements"].values():
                limits = [source.get("stale_minutes", 30) * 60 for source in config["locations"][name].get("observations", [])
                          if source["account"] == observation["account"] and source["device_id"] == observation["device_id"]]
                measurement["stale"] |= now - epoch(measurement["observed_at"]) > min(limits, default=1800)
        for field, metadata in payload.get("measurement_metadata", {}).items():
            if metadata.get("kind") == "forecast":
                metadata["stale"] = payload["forecast"]["stale"]
                continue
            matches = [measurement for observation in payload.get("observations", [])
                       if observation["module_id"] == metadata["module_id"]
                       for key, measurement in observation["measurements"].items() if key == field]
            if any(item["stale"] for item in matches):
                metadata["stale"] = True
                if isinstance(payload["current"].get(field), dict):
                    payload["current"][field]["live"] = False
        return payload

    @app.get("/api/v1/locations/<name>/weather")
    def weather(name):
        return conditional(location_data(name))

    @app.get("/api/v1/locations/<name>/observations")
    def observations(name):
        payload = location_data(name)
        return conditional({key: payload[key] for key in
                            ("schema_version", "location", "revision", "generated_at", "units", "observations")})

    @app.get("/api/v1/status")
    def status():
        return conditional({"worker": store.get("worker/status"),
                            "jobs": store.scan("jobs/"), "webhooks": store.scan("webhook/"),
                            "mqtt": store.scan("mqtt/status/")})

    @app.post("/api/v1/integrations/netatmo/webhook")
    def webhook():
        if request.mimetype != "application/json":
            abort(415)
        raw = request.get_data()
        signature = request.headers.get("X-Netatmo-secret", "")
        if not re.fullmatch("[0-9a-f]{64}", signature):
            abort(403)
        candidates = []
        for name, account in config["accounts"].items():
            if account["provider"] != "netatmo":
                continue
            expected = hmac.new(account["client_secret"].encode(), raw, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                candidates.append((name, account))
        if not candidates:
            abort(403)
        try:
            payload = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, UnicodeDecodeError):
            abort(400)
        if not isinstance(payload, dict) or not isinstance(payload.get("push_type"), str):
            abort(400)
        candidates = [(name, account) for name, account in candidates if payload.get("user_id") == account["user_id"]]
        if len(candidates) != 1:
            abort(403)
        name, account = candidates[0]
        devices = {source["device_id"] for location in config["locations"].values()
                   for source in location.get("observations", []) if source["account"] == name}
        if payload.get("device_id") and payload["device_id"] not in devices:
            return jsonify(accepted=False)
        if payload["push_type"] not in ("device_event", "topology_changed", "home_event_changed", "webhook_activation", "webhook_deletion"):
            return jsonify(accepted=False)
        digest = hashlib.sha256(name.encode() + b"\0" + raw).hexdigest()
        # Errors propagate as 500 so Netatmo can retry; never acknowledge a lost write.
        accepted = store.enqueue(digest, name, payload, clock())
        return jsonify(accepted=accepted)

    return app


def conditional(payload):
    response = jsonify(payload)
    response.cache_control.no_cache = True
    response.set_etag(hashlib.sha256(response.get_data()).hexdigest())
    return response.make_conditional(request)

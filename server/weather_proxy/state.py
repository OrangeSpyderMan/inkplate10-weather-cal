"""Pure composition and unit conversion shared by HTTP and MQTT."""

import copy
import hashlib
import json
from datetime import datetime, timezone


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def public_location(name, config):
    return {"id": name, **{key: config[key] for key in
            ("name", "latitude", "longitude", "timezone")}}


def location_signature(location):
    return hashlib.sha256(json.dumps(location, sort_keys=True).encode()).hexdigest()


def convert_units(payload, units):
    result = copy.deepcopy(payload)
    def convert(value):
        if isinstance(value, list):
            for item in value:
                convert(item)
        elif isinstance(value, dict):
            unit = value.get("unit")
            factor, offset, target = 1, 0, unit
            if units == "imperial":
                if unit == "°C":
                    factor, offset, target = 1.8, 32, "°F"
                elif unit in ("kmh", "km/h", "m/s"):
                    factor, target = (2.2369362921 if unit == "m/s" else .6213711922), "mph"
                elif unit == "mm":
                    factor, target = 1 / 25.4, "in"
            if target != unit:
                for key in ("value", "min", "max", "gust", "last_hour", "last_24_hours"):
                    if isinstance(value.get(key), (int, float)):
                        value[key] = round(value[key] * factor + offset, 3)
                value["unit"] = target
                if "rate_unit" in value:
                    value["rate_unit"] = "in/h" if target == "in" else value["rate_unit"]
            for item in value.values():
                if isinstance(item, (dict, list)):
                    convert(item)
    convert(result)
    result["units"] = units
    return result


def read_observations(store, location, now):
    records = []
    for source in location.get("observations", []):
        prefix = f"observations/{source['account']}/{source['device_id']}/"
        for key, document in store.scan(prefix).items():
            record = copy.deepcopy(document["value"])
            stale_seconds = source.get("stale_minutes", 30) * 60
            for measurement in record["measurements"].values():
                measurement["stale"] = (not record.get("reachable", True) or
                                         now - epoch(measurement["observed_at"]) > stale_seconds)
            records.append(record)
    return records


def compose(store, name, location, accounts, now):
    forecast_doc = store.get(f"forecast/{name}")
    observations = read_observations(store, location, now)
    if not forecast_doc and not observations:
        return None
    forecast = forecast_doc["value"] if forecast_doc else None
    if forecast and forecast.get("configuration") != location_signature(location):
        forecast = None
    if not forecast and not observations:
        return None
    current = copy.deepcopy(forecast["current"]) if forecast else {}
    forecast_stale = not forecast or now - epoch(forecast["fetched_at"]) > location.get("forecast", {}).get("stale_minutes", 360) * 60
    metadata = {field: {"source": forecast["source"], "kind": "forecast",
                        "fetched_at": forecast["fetched_at"], "stale": forecast_stale}
                for field in current} if forecast else {}
    for source in location.get("observations", []):
        for record in observations:
            if record["account"] != source["account"] or record["device_id"] != source["device_id"]:
                continue
            module = record["module_id"]
            for field, measurement in record["measurements"].items():
                selected = source.get("wind_module_id" if field == "wind" else
                                      "rain_module_id" if field == "rain" else "module_id") or source.get("module_id") or source["device_id"]
                if selected != module or measurement["stale"]:
                    continue
                metadata[field] = {key: measurement[key] for key in ("observed_at", "received_at", "stale")}
                metadata[field].update(source="netatmo", kind="observation", module_id=module)
                if field in ("temperature", "wind", "rain"):
                    value = {key: val for key, val in measurement.items()
                             if key not in ("observed_at", "received_at", "stale")}
                    if field == "temperature":
                        for key in ("min", "max"):
                            if key in current.get(field, {}):
                                value[key] = current[field][key]
                    current[field] = {**value, "source": "netatmo", "live": True}
                else:
                    current[field] = measurement["value"]
    stale = not forecast or now - epoch(forecast["fetched_at"]) > location.get("forecast", {}).get("stale_minutes", 360) * 60
    hourly = [entry for entry in forecast["hourly"] if epoch(entry["dt"]) > now] if forecast else []
    if forecast and not hourly and forecast["hourly"]:
        stale = True
    return {
        "schema_version": "2.1", "location": public_location(name, location),
        "configuration": location_signature(location),
        "source": forecast["source"] if forecast else "netatmo", "units": "metric",
        "current": current, "hourly": hourly, "observations": observations,
        "measurement_metadata": metadata,
        "forecast": {"available": forecast is not None,
                     "fetched_at": forecast["fetched_at"] if forecast else None, "stale": stale},
    }

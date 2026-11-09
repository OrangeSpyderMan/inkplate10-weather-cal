import math
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand(value, environ=None):
    environ = os.environ if environ is None else environ
    if isinstance(value, dict):
        return {k: expand(v, environ) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, environ) for v in value]
    if not isinstance(value, str):
        return value
    def substitute(match):
        key, default = match.groups()
        if key in environ:
            return environ[key]
        if default is not None:
            return default
        raise ValueError(f"Missing environment variable {key}")
    return ENV.sub(substitute, value)


def positive(value, name):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("WeatherProxy configuration must be a mapping")
    accounts = config.setdefault("accounts", {})
    locations = config.setdefault("locations", {})
    for name, account in accounts.items():
        if not ID.fullmatch(name):
            raise ValueError("Invalid account ID")
        provider = account.get("provider")
        if provider not in ("openweathermapv3", "openweathermapv4", "accuweather", "netatmo"):
            raise ValueError(f"Unsupported account provider for {name}")
        required = ("client_id", "client_secret", "refresh_token", "user_id") if provider == "netatmo" else ("apikey",)
        if any(not isinstance(account.get(key), str) or not account[key] for key in required):
            raise ValueError(f"Missing account credentials/identity for {name}")
        for key, default in (("requests_per_hour", 100), ("requests_per_10_seconds", 2)):
            number = positive(account.get(key, default), key)
            if int(number) != number:
                raise ValueError(f"{key} must be an integer")
            if key in account:
                account[key] = int(number)
        account["reconcile_minutes"] = positive(account.get("reconcile_minutes", 30), "reconcile_minutes")
        account["event_min_seconds"] = positive(account.get("event_min_seconds", 60), "event_min_seconds")
    for name, location in locations.items():
        if not ID.fullmatch(name):
            raise ValueError("Invalid location ID")
        for key, bound in (("latitude", 90), ("longitude", 180)):
            number = float(location[key])
            if not math.isfinite(number) or abs(number) > bound:
                raise ValueError(f"Invalid {key} for {name}")
            location[key] = number
        ZoneInfo(location["timezone"])
        location.setdefault("name", name)
        forecast = location.get("forecast")
        if forecast:
            account = accounts.get(forecast.get("account"), {})
            if account.get("provider") not in ("openweathermapv3", "openweathermapv4", "accuweather"):
                raise ValueError(f"Invalid forecast account for {name}")
            forecast["refresh_minutes"] = positive(forecast.get("refresh_minutes", 180), "refresh_minutes")
            forecast["stale_minutes"] = positive(forecast.get("stale_minutes", 360), "stale_minutes")
            hours = positive(forecast.get("num_hours", 12 if account["provider"] == "accuweather" else 24), "num_hours")
            if int(hours) != hours or hours > (12 if account["provider"] == "accuweather" else 47):
                raise ValueError("num_hours exceeds the supported provider horizon")
            forecast["num_hours"] = int(hours)
        for source in location.get("observations", []):
            if accounts.get(source.get("account"), {}).get("provider") != "netatmo" or not source.get("device_id"):
                raise ValueError(f"Invalid observation source for {name}")
            source["stale_minutes"] = positive(source.get("stale_minutes", 30), "stale_minutes")
    if config.get("api_token") and not isinstance(config["api_token"], str):
        raise ValueError("api_token must be a string")
    return config


def load_config(path=None):
    path = Path(path or os.environ.get("WEATHER_PROXY_CONFIG", "weather-proxy.yaml")).resolve()
    config = validate(expand(yaml.safe_load(path.read_text())))
    storage = config.setdefault("storage", {})
    storage_path = Path(os.environ.get("WEATHER_PROXY_STORAGE_PATH", storage.get("path", "data/weather-proxy.sqlite3")))
    storage["path"] = str(storage_path)
    if not storage_path.is_absolute():
        storage["path"] = str(path.parent / storage_path)
    config["_directory"] = str(path.parent)
    return config


def read_environment(path):
    """Read literal KEY=value secrets; never execute or interpolate shell code."""
    path = Path(path)
    if not path.exists():
        return {}
    import json
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("Invalid WeatherProxy environment file")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                # Existing calendar installers also escape dollars for Compose.
                if len(value) < 2 or not value.endswith('"'):
                    raise ValueError("Invalid quoted environment value") from None
                value = re.sub(r'\\([\\"$])', r'\1', value[1:-1])
        elif len(value) >= 2 and value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        result[key] = value
    return result


def load_environment(path):
    for key, value in read_environment(path).items():
        os.environ.setdefault(key, value)

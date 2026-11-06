"""Offline calendar-to-proxy conversion and explicit, verified calendar cutover."""

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote, urlsplit

import requests
import yaml

from .config import expand, read_environment, validate


SUPPORTED = {"openweathermapv3", "openweathermapv4", "accuweather"}


class ProxyNotReady(ValueError):
    pass


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_plan(calendar_path, *, latitude, longitude, timezone_name, proxy_url,
               location_id="calendar", name=None, env_path=None, netatmo_user_id=None,
               netatmo_device_id=None, token_path=None, mqtt_broker=None):
    path = Path(calendar_path).resolve()
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        raise ValueError("Invalid YAML in calendar configuration") from None
    if not isinstance(raw, dict):
        raise ValueError("Calendar configuration must be a mapping")
    weather = raw.get("weather", {})
    provider = weather.get("service")
    if provider not in SUPPORTED:
        raise ValueError("Migration requires a direct OWM or AccuWeather calendar configuration")
    parts = urlsplit(proxy_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Calendar proxy URL must be HTTP(S), without embedded credentials")
    file_environment = read_environment(env_path) if env_path else {}
    # An explicitly supplied environment file is authoritative over this shell.
    environment = {**os.environ, **file_environment}
    resolved_weather = expand(weather, environment)
    api_key = resolved_weather.get("apikey")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("Existing forecast API key is missing")
    secrets = {"WEATHER_API_KEY": api_key}
    calendar_server = path.parent.parent if path.parent.name == "config" else path.parent
    server = raw.get("server", {})
    refresh = float(server.get("refreshminutes", float(server.get("refreshhours", 3)) * 60))
    if not math.isfinite(refresh) or refresh <= 0:
        raise ValueError("Migration requires a positive calendar refresh interval")
    count = int(weather.get("num_hourly_forecasts", 6))
    spacing = int(weather.get("forecastslicehours", 3))
    lead = int(weather.get("forecastleadminutes", 15))
    if count < 0 or spacing <= 0 or lead < 0:
        raise ValueError("Invalid calendar forecast settings")
    horizon = 12 if provider == "accuweather" else max(24, count * spacing + math.ceil(lead / 60) + 1)
    if (provider == "accuweather" and count > 12) or horizon > 47:
        raise ValueError("Calendar forecast selection exceeds the proxy provider horizon")
    proxy = {
        "server": {"host": "0.0.0.0", "port": 8090},
        "storage": {"backend": "sqlite", "path": "/data/weather-proxy.sqlite3"},
        "accounts": {"forecast": {"provider": provider, "apikey": "${WEATHER_API_KEY}"}},
        "locations": {location_id: {
            "name": name or str(raw.get("location", location_id)),
            "latitude": latitude, "longitude": longitude, "timezone": timezone_name,
            "forecast": {"account": "forecast", "refresh_minutes": refresh,
                         "stale_minutes": max(60, refresh * 2), "num_hours": horizon},
        }},
    }
    api_token = environment.get("WEATHER_PROXY_TOKEN", "")
    if api_token:
        if file_environment.get("WEATHER_PROXY_TOKEN") != api_token:
            raise ValueError("Put WEATHER_PROXY_TOKEN in the calendar's existing environment file before migration")
        secrets["WEATHER_PROXY_TOKEN"] = api_token
        proxy["api_token"] = "${WEATHER_PROXY_TOKEN}"
    realtime = raw.get("current_conditions", raw.get("current_temperature", {})) or {}
    source = realtime.get("source", "weather")
    if source not in ("weather", "netatmo"):
        raise ValueError("Unsupported current-conditions provider for migration")
    token_used = None
    observations_required = []
    if source == "netatmo":
        netatmo = expand(realtime.get("netatmo", realtime), environment)
        account = {"provider": "netatmo", "user_id": netatmo_user_id or netatmo.get("user_id") or environment.get("NETATMO_USER_ID")}
        if not account["user_id"]:
            raise ValueError("Netatmo migration requires the authorized Netatmo user ID")
        for key in ("client_id", "client_secret", "refresh_token"):
            variable = f"NETATMO_{key.upper()}"
            secrets[variable] = netatmo.get(key, "")
            account[key] = "${" + variable + "}"
        token_file = Path(token_path) if token_path else Path(netatmo.get("token_file", "data/netatmo-token.json"))
        if not token_file.is_absolute():
            token_file = calendar_server / token_file
        if token_file.is_file():
            # The seed in YAML/.env may have been rotated many times since setup.
            persisted = json.loads(token_file.read_text())
            if persisted.get("refresh_token"):
                secrets["NETATMO_REFRESH_TOKEN"] = persisted["refresh_token"]
                token_used = str(token_file.resolve())
        device = netatmo_device_id or netatmo.get("device_id")
        if not device:
            raise ValueError("Netatmo migration requires a station ID; the old 'first station' default is ambiguous")
        selection = {"account": "netatmo", "device_id": device}
        selection.update({key: netatmo[key] for key in ("module_id", "wind_module_id", "rain_module_id") if netatmo.get(key)})
        observations_required = [{"device_id": device, "module_id": module}
                                 for module in {selection.get("module_id", device), selection.get("wind_module_id"), selection.get("rain_module_id")} if module]
        proxy["accounts"]["netatmo"] = account
        proxy["locations"][location_id]["observations"] = [selection]
    mqtt = copy.deepcopy(expand((raw.get("mqtt") or {}).get("weather", {}), environment))
    if mqtt:
        mqtt.update(base_topic="weatherproxy", client_id=f"weatherproxy-{location_id}", qos=1)
        if mqtt_broker:
            mqtt["broker"] = mqtt_broker
        for key in ("username", "password"):
            if mqtt.get(key):
                variable = f"MQTT_{key.upper()}"
                secrets[variable] = mqtt[key]
                mqtt[key] = "${" + variable + "}"
        proxy["mqtt"] = mqtt
    validate(expand(copy.deepcopy(proxy), secrets))
    candidate = copy.deepcopy(raw)
    for key in ("apikey",):
        candidate["weather"].pop(key, None)
    candidate["weather"].update(service="weatherproxy", url=proxy_url.rstrip("/"), location=location_id)
    if api_token:
        candidate["weather"]["token"] = "${WEATHER_PROXY_TOKEN}"
    candidate["current_conditions"] = {"source": "weather"}
    candidate.pop("current_temperature", None)
    return {
        "proxy": proxy, "calendar": candidate, "secrets": secrets,
        "source": str(path), "source_sha256": fingerprint(path),
        "provider": provider, "location_id": location_id, "proxy_url": proxy_url.rstrip("/"),
        "token_file_used": token_used,
        "token_file_sha256": fingerprint(token_used) if token_used else None,
        "env_file": str(Path(env_path).resolve()) if env_path else None,
        "env_file_sha256": fingerprint(env_path) if env_path else None,
        "observations_required": observations_required,
    }


def write_plan(plan, directory, dry_run=False):
    directory = Path(directory)
    paths = [directory / name for name in ("weather-proxy.yaml", "weather.env", "calendar-proxy.yaml", "migration.json")]
    if any(path.exists() for path in paths):
        raise ValueError("Migration output already exists; choose a new output directory")
    summary = f"Migrate {plan['provider']} to location {plan['location_id']}; preserve calendar display configuration"
    print(summary)
    if plan["token_file_used"]:
        print("Use the latest persisted Netatmo refresh token")
    if dry_run:
        print(f"Would write private migration files under {directory}; calendar remains unchanged")
        return
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    metadata = {key: plan[key] for key in ("source", "source_sha256", "provider", "location_id", "proxy_url", "token_file_used", "token_file_sha256", "env_file", "env_file_sha256", "observations_required")}
    contents = [yaml.safe_dump(plan["proxy"], sort_keys=False),
                "".join(f"{key}={json.dumps(value)}\n" for key, value in plan["secrets"].items()),
                yaml.safe_dump(plan["calendar"], sort_keys=False), json.dumps(metadata, indent=2)]
    for path, content in zip(paths, contents):
        private_write(path, content)
    ignore = directory / ".gitignore"
    existing = ignore.read_text() if ignore.exists() else ""
    private_write(ignore, existing + "\nweather-proxy.yaml\nweather.env\ncalendar-proxy.yaml\nmigration.json\n")


def activate(directory, *, verify_url=None, dry_run=False, wait_seconds=0):
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError("Migration wait must be finite and non-negative")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return _activate_once(directory, verify_url=verify_url, dry_run=dry_run)
        except (ProxyNotReady, requests.RequestException) as exc:
            if time.monotonic() >= deadline:
                message = str(exc) if isinstance(exc, ProxyNotReady) else "Proxy API is not reachable; calendar was not changed"
                raise ValueError(message) from None
            time.sleep(min(2, max(0, deadline - time.monotonic())))


def _activate_once(directory, *, verify_url=None, dry_run=False):
    directory = Path(directory)
    metadata = json.loads((directory / "migration.json").read_text())
    source = Path(metadata["source"])
    def unchanged():
        if fingerprint(source) != metadata["source_sha256"]:
            raise ValueError("Calendar configuration changed since migration was prepared; regenerate the plan")
        if metadata["token_file_used"] and fingerprint(metadata["token_file_used"]) != metadata["token_file_sha256"]:
            raise ValueError("Netatmo tokens changed; stop the old producer and regenerate the migration plan")
        if metadata.get("env_file") and fingerprint(metadata["env_file"]) != metadata["env_file_sha256"]:
            raise ValueError("Calendar environment changed since migration was prepared; regenerate the plan")
    unchanged()
    candidate = yaml.safe_load((directory / "calendar-proxy.yaml").read_text())
    if dry_run:
        print("Would verify proxy weather, back up the direct configuration, and atomically switch the calendar")
        return
    token = read_environment(directory / "weather.env").get("WEATHER_PROXY_TOKEN")
    url = (verify_url or metadata["proxy_url"]).rstrip("/")
    response = requests.get(f"{url}/api/v1/locations/{quote(metadata['location_id'], safe='')}/weather",
                            params={"units": "metric" if candidate["weather"].get("metric", True) else "imperial"},
                            headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=20)
    try:
        if response.status_code != 200:
            error = ProxyNotReady if response.status_code in (502, 503, 504) else ValueError
            raise error(f"Proxy is not ready for calendar migration ({response.status_code})")
        weather = response.json()
    finally:
        response.close()
    if not weather.get("forecast", {}).get("available") or weather["forecast"].get("stale"):
        raise ProxyNotReady("Proxy does not have a fresh forecast; calendar was not changed")
    if weather.get("location", {}).get("id") != metadata["location_id"]:
        raise ValueError("Proxy returned a different location")
    for required in metadata.get("observations_required", []):
        available = any(all(record.get(key) == value for key, value in required.items())
                        and any(not measurement.get("stale", True) for measurement in record.get("measurements", {}).values())
                        for record in weather.get("observations", []))
        if not available:
            raise ProxyNotReady("Proxy has no fresh readings for a configured Netatmo module; calendar was not changed")
    # Verify the calendar's actual selection, not just HTTP liveness.
    from weather.weatherproxy.weatherproxy import WeatherProxyService
    adapter = WeatherProxyService(url, metadata["location_id"], metric=candidate["weather"].get("metric", True),
        num_hours=int(candidate["weather"].get("num_hourly_forecasts", 6)),
        forecast_slice_hours=int(candidate["weather"].get("forecastslicehours", 3)),
        forecast_lead_minutes=int(candidate["weather"].get("forecastleadminutes", 15)), token=token)
    adapter.forecast_from_payload(weather)
    unchanged()
    backup = source.with_name(source.name + ".direct-provider.bak")
    if backup.exists():
        raise ValueError("Direct-provider backup already exists; preserve it and choose a fresh migration plan")
    original = source.stat()
    # Exclusive creation also prevents two activators from replacing the backup.
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(source.read_text())
    if os.geteuid() == 0:
        os.chown(backup, original.st_uid, original.st_gid)
    private_write(source, yaml.safe_dump(candidate, sort_keys=False), mode=original.st_mode & 0o777)
    if os.geteuid() == 0:
        os.chown(source, original.st_uid, original.st_gid)
    print(f"Calendar switched; direct configuration backup: {backup}")
    print("Restart the calendar web and producer services to load the new configuration")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--timezone")
    parser.add_argument("--location-id", default="calendar")
    parser.add_argument("--name")
    parser.add_argument("--proxy-url")
    parser.add_argument("--netatmo-user-id")
    parser.add_argument("--netatmo-device-id")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--mqtt-broker")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--verify-url")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args(argv)
    try:
        if args.activate:
            activate(args.output_dir, verify_url=args.verify_url, dry_run=args.dry_run, wait_seconds=args.wait_seconds)
        else:
            if args.calendar_config is None or args.latitude is None or args.longitude is None or not args.timezone or not args.proxy_url:
                parser.error("Preparation requires --calendar-config, --latitude, --longitude, --timezone and --proxy-url")
            plan = build_plan(args.calendar_config, latitude=args.latitude, longitude=args.longitude,
                timezone_name=args.timezone, proxy_url=args.proxy_url, location_id=args.location_id, name=args.name,
                env_path=args.env_file, netatmo_user_id=args.netatmo_user_id, netatmo_device_id=args.netatmo_device_id,
                token_path=args.token_file, mqtt_broker=args.mqtt_broker)
            write_plan(plan, args.output_dir, args.dry_run)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

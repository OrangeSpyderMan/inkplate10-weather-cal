#!/usr/bin/env python3
"""Interactive server installer for Inkplate Weather Calendar.

The installer intentionally uses only the Python standard library so it can run
before the server virtualenv and Python package dependencies exist.
"""

from __future__ import annotations

import argparse
import filecmp
import getpass
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "server"
sys.path.insert(0, str(SERVER_DIR))

from output_profiles import (  # noqa: E402
    DEFAULT_HEIGHT as DEFAULT_IMAGE_HEIGHT,
    DEFAULT_OUTPUT_FILENAME,
    DEFAULT_OUTPUT_PROFILE,
    DEFAULT_RENDERER,
    DEFAULT_WIDTH as DEFAULT_IMAGE_WIDTH,
)
from build_version import (  # noqa: E402
    VERSION_MANIFEST_FILENAME,
    generate_version_manifest,
    read_version_manifest,
)
from weather.providers import FORECAST_PROVIDERS  # noqa: E402


APP_USER = "inkplate"
APP_GROUP = "inkplate"
INSTALL_DIR = Path("/srv/inkplate")
VENV_DIR = INSTALL_DIR / "inkplate_venv"
NATIVE_ENV_FILE = Path("/etc/inkplate/weather.env")
SERVICE_FILE = Path("/etc/systemd/system/inkplate.service")
PRODUCER_SERVICE_FILE = Path("/etc/systemd/system/inkplate-producer.service")
DIAGNOSTICS_SERVICE_FILE = Path(
    "/etc/systemd/system/inkplate-diagnostics.service"
)
DOCKER_ENV_FILE = Path(".env")
SERVER_CONFIG = Path("server/config/config.yaml")
PODMAN_COMPOSE_OVERRIDE = Path("docker-compose.podman.yml")
DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"
DEFAULT_REFRESH_MINUTES = 180
DEFAULT_FORECASTS = 6
DEFAULT_FORECAST_SLICE_HOURS = 3
DEFAULT_FORECAST_LEAD_MINUTES = 15
DEFAULT_LOCATION = "Landry, FR"
DEFAULT_WEATHER = "openweathermapv3"
WEATHER_PROVIDER_LABELS = {
    "openweathermapv4": "OpenWeatherMap One Call 4.0",
    "openweathermapv3": "OpenWeatherMap One Call 3.0",
    "accuweather": "AccuWeather",
}
PRIVILEGE_PREFIX: list[str] = []
INSTALLER_ANSWERS: dict[str, object] = {}
NON_INTERACTIVE = False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactively install the Inkplate weather calendar server."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print intended actions without writing files or running commands",
    )
    parser.add_argument(
        "--mode",
        choices=("docker", "podman", "proxmox", "systemd"),
        help="install mode; prompts when omitted",
    )
    parser.add_argument(
        "--answers",
        type=Path,
        help="JSON answers file used as prompt defaults or for --non-interactive",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="run without prompts; requires --answers",
    )
    args = parser.parse_args()

    repo_root = Path.cwd()
    ensure_repo_root(repo_root)
    configure_answers(args.answers, args.non_interactive)

    print("Inkplate Weather Calendar server installer")
    print("------------------------------------------")
    print("Press Ctrl-C at any time to cancel cleanly.")
    if args.dry_run:
        print("Dry run: no files will be written and no commands will be run.")
    print()

    mode = args.mode or answer_value("mode", None) or prompt_choice(
        "Install type",
        [
            ("docker", "Docker Compose install from this checkout"),
            ("podman", "Podman Compose install from this checkout"),
            ("proxmox", "Experimental Proxmox VE 9 OCI/LXC installer"),
            ("systemd", "Native systemd install under /srv/inkplate"),
        ],
        default="docker",
    )

    if mode not in ("docker", "podman", "proxmox", "systemd"):
        raise SystemExit(
            "ERROR: install mode must be 'docker', 'podman', 'proxmox', "
            "or 'systemd'."
        )

    if mode in ("docker", "podman"):
        install_compose(repo_root, args.dry_run, mode)
    elif mode == "proxmox":
        print(
            "Proxmox VE support is experimental and uses a dedicated installer."
        )
        print("Run: ./bin/install_proxmox")
        print("Preview first with: ./bin/install_proxmox --dry-run")
        return 0
    else:
        install_systemd(repo_root, args.dry_run)

    print()
    print("Installer finished.")
    return 0


def configure_answers(path: Path | None, non_interactive: bool) -> None:
    global INSTALLER_ANSWERS, NON_INTERACTIVE

    NON_INTERACTIVE = non_interactive
    if non_interactive and path is None:
        raise SystemExit("ERROR: --non-interactive requires --answers PATH.")
    if path is None:
        INSTALLER_ANSWERS = {}
        return
    if not path.exists():
        raise SystemExit(f"ERROR: answers file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit("ERROR: answers file must contain a JSON object.")
    INSTALLER_ANSWERS = data
    print(f"Loaded installer answers from {path}")


def ensure_repo_root(repo_root: Path) -> None:
    required = [
        repo_root / "server" / "server.py",
        repo_root / "server" / "web_server.py",
        repo_root / "server" / "requirements.txt",
        repo_root / "docker-compose.yml",
        repo_root / PODMAN_COMPOSE_OVERRIDE,
        repo_root / "bin" / "inkplate.service",
        repo_root / "bin" / "inkplate-producer.service",
        repo_root / "bin" / "inkplate-diagnostics.service",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("ERROR: run this installer from the repository root.", file=sys.stderr)
        for path in missing:
            print(f"Missing: {path}", file=sys.stderr)
        raise SystemExit(1)


def install_compose(repo_root: Path, dry_run: bool, mode: str) -> None:
    label = "Docker" if mode == "docker" else "Podman"
    compose_command = check_compose_runtime(mode, dry_run)
    if mode == "podman":
        compose_command = [
            *compose_command,
            "-f",
            "docker-compose.yml",
            "-f",
            str(PODMAN_COMPOSE_OVERRIDE),
        ]
    existing = [
        path
        for path in (
            repo_root / DOCKER_ENV_FILE,
            repo_root / SERVER_CONFIG,
        )
        if path.exists()
    ]
    action = choose_existing_action(label, existing)
    if action == "abort":
        print("No changes made.")
        return

    answers = None
    if action in ("fresh", "reconfigure", "update_reconfigure"):
        current_env = read_env_file(repo_root / DOCKER_ENV_FILE)
        current_config = read_simple_yaml(existing_config_path(repo_root))
        answers = collect_answers(current_env, current_config, mode=mode)
        write_text_atomic(
            repo_root / SERVER_CONFIG,
            render_config(answers, mode=mode),
            dry_run=dry_run,
            mode=0o644,
        )
        write_text_atomic(
            repo_root / DOCKER_ENV_FILE,
            render_env(
                answers,
                include_optional=answers["netatmo_enabled"],
                compose=True,
            ),
            dry_run=dry_run,
            mode=0o600,
        )

    ensure_compose_config_readable(repo_root, dry_run=dry_run)

    prepare_version_manifest(repo_root, dry_run=dry_run)
    start_now = prompt_yes_no(
        f"Start or update the {label} containers now?",
        default=True,
        key="start_now",
    )
    if start_now:
        if answers is not None:
            port = int(answers["port"])
        else:
            config = read_simple_yaml(existing_config_path(repo_root))
            port = int(config.get("server.port", DEFAULT_PORT))
        check_compose_host_port(
            compose_command,
            port,
            dry_run=dry_run,
        )
        try:
            run([*compose_command, "up", "--build", "-d"], dry_run=dry_run)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                f"ERROR: {label} Compose failed with exit status "
                f"{exc.returncode}. Review the errors above."
            ) from None
        print(f"Logs: {' '.join(compose_command)} logs -f")
    else:
        print(f"Start later with: {' '.join(compose_command)} up --build -d")


def ensure_compose_config_readable(
    repo_root: Path,
    *,
    dry_run: bool,
) -> None:
    config_path = repo_root / SERVER_CONFIG
    if not config_path.exists():
        return
    if dry_run:
        print(f"Would set {config_path} mode to 0o644")
        return
    os.chmod(config_path, 0o644)


def install_systemd(repo_root: Path, dry_run: bool) -> None:
    existing = [
        path
        for path in (
            INSTALL_DIR,
            NATIVE_ENV_FILE,
            SERVICE_FILE,
            PRODUCER_SERVICE_FILE,
            DIAGNOSTICS_SERVICE_FILE,
        )
        if path.exists()
    ]
    action = choose_existing_action("systemd", existing)
    if action == "abort":
        print("No changes made.")
        return

    validate_native_platform(dry_run)

    if action in ("fresh", "update", "update_reconfigure"):
        install_native_prerequisites(dry_run)
        ensure_system_user(dry_run)
        copy_repo(repo_root, INSTALL_DIR, dry_run)
        ensure_venv(dry_run)
        refresh_dependencies(dry_run)
        install_geckodriver(repo_root, dry_run)

    if action in ("fresh", "reconfigure", "update_reconfigure"):
        current_env = read_env_file(NATIVE_ENV_FILE)
        current_config = read_simple_yaml(existing_config_path(INSTALL_DIR))
        answers = collect_answers(current_env, current_config, mode="systemd")
        write_text_atomic(
            INSTALL_DIR / SERVER_CONFIG,
            render_config(answers, mode="systemd"),
            dry_run=dry_run,
            mode=0o600,
            sudo=True,
        )
        write_text_atomic(
            NATIVE_ENV_FILE,
            render_env(answers, include_optional=answers["netatmo_enabled"]),
            dry_run=dry_run,
            mode=0o600,
            sudo=True,
        )
        run(["chown", f"{APP_USER}:{APP_GROUP}", str(INSTALL_DIR / SERVER_CONFIG)], sudo=True, dry_run=dry_run)
        run(["chown", "root:root", str(NATIVE_ENV_FILE)], sudo=True, dry_run=dry_run)

    if action in ("fresh", "update", "update_reconfigure"):
        run(
            ["bin/install_service", "--unit-file", "bin/inkplate.service", "--no-start"],
            sudo=True,
            dry_run=dry_run,
        )
        run(
            [
                "bin/install_service",
                "--unit-file",
                "bin/inkplate-producer.service",
                "--service-name",
                "inkplate-producer",
                "--no-start",
            ],
            sudo=True,
            dry_run=dry_run,
        )
        run(
            [
                "bin/install_service",
                "--unit-file",
                "bin/inkplate-diagnostics.service",
                "--service-name",
                "inkplate-diagnostics",
                "--no-start",
            ],
            sudo=True,
            dry_run=dry_run,
        )

    if prompt_yes_no("Start or restart the systemd services now?", default=True, key="start_now"):
        run(
            [
                "systemctl",
                "restart",
                "inkplate-producer",
                "inkplate",
                "inkplate-diagnostics",
            ],
            sudo=True,
            dry_run=dry_run,
        )
        if action in ("fresh", "update"):
            remove_legacy_application_logs(dry_run)
        run(
            [
                "systemctl",
                "status",
                "inkplate-producer",
                "inkplate",
                "inkplate-diagnostics",
                "--no-pager",
                "-l",
            ],
            sudo=True,
            dry_run=dry_run,
            check=False,
        )
        print(
            "Logs: sudo journalctl -u inkplate-producer -u inkplate "
            "-u inkplate-diagnostics -f"
        )
    else:
        print(
            "Start later with: sudo systemctl start inkplate-producer "
            "inkplate inkplate-diagnostics"
        )


def existing_config_path(base_dir: Path) -> Path:
    return base_dir / SERVER_CONFIG


def choose_existing_action(label: str, existing: list[Path]) -> str:
    if not existing:
        return "fresh"

    print(f"Existing {label} installation files were found:")
    for path in existing:
        print(f"  - {path}")
    return prompt_choice(
        "How should the installer continue?",
        [
            ("update", "update app/dependencies and preserve config/secrets"),
            (
                "update_reconfigure",
                "update app/dependencies and rewrite config/secrets",
            ),
            ("reconfigure", "rewrite config/secrets only"),
            ("abort", "make no changes"),
        ],
        default="update",
        key="existing_action",
    )


def collect_answers(env: dict[str, str], config: dict[str, str], mode: str) -> dict[str, object]:
    print()
    print("Configuration")
    print("-------------")

    answers: dict[str, object] = {}
    answers["host"] = prompt_ip_address(
        "Server bind IP",
        default=config.get("server.host", DEFAULT_HOST),
        key="host",
    )
    answers["port"] = prompt_int(
        "Server port",
        default=int(config.get("server.port", DEFAULT_PORT)),
        minimum=1,
        maximum=65535,
        key="port",
    )
    answers["refresh_minutes"] = prompt_int(
        "Refresh interval in minutes",
        default=configured_refresh_minutes(config, INSTALLER_ANSWERS),
        minimum=1,
        maximum=24 * 60,
        key="refresh_minutes",
    )
    answers["location"] = prompt_text(
        "Location for weather/map lookup",
        default=config.get("location", DEFAULT_LOCATION),
        key="location",
    )
    answers["weather_service"] = prompt_choice(
        "Weather provider",
        weather_provider_choices(),
        default=config.get("weather.service", DEFAULT_WEATHER),
        key="weather_service",
    )
    answers["weather_api_key"] = prompt_secret(
        "Weather API key",
        default=env.get("WEATHER_API_KEY", ""),
        required=True,
        key="weather_api_key",
    )
    answers["google_api_key"] = prompt_secret(
        "Google Static Maps API key",
        default=env.get("GOOGLE_API_KEY", ""),
        required=True,
        key="google_api_key",
    )
    answers["google_staticmaps_mapid"] = prompt_secret(
        "Google Static Maps Map ID",
        default=env.get("GOOGLE_STATICMAPS_MAPID", ""),
        required=True,
        key="google_staticmaps_mapid",
    )
    answers["num_hourly_forecasts"] = prompt_int(
        "Number of forecast slots",
        default=int(config.get("weather.num_hourly_forecasts", DEFAULT_FORECASTS)),
        minimum=1,
        maximum=12,
        key="num_hourly_forecasts",
    )
    answers["forecast_slice_hours"] = prompt_int(
        "Forecast slot spacing in hours",
        default=int(
            config.get("weather.forecastslicehours", DEFAULT_FORECAST_SLICE_HOURS)
        ),
        minimum=1,
        maximum=24,
        key="forecast_slice_hours",
    )
    answers["forecast_lead_minutes"] = prompt_int(
        "Forecast lead time in minutes",
        default=int(
            config.get(
                "weather.forecastleadminutes",
                DEFAULT_FORECAST_LEAD_MINUTES,
            )
        ),
        minimum=0,
        maximum=180,
        key="forecast_lead_minutes",
    )
    answers["metric"] = prompt_yes_no("Use metric units?", default=parse_bool(config.get("weather.metric", "true")), key="metric")

    current_source = config.get(
        "current_conditions.source",
        config.get("current_temperature.source", "weather"),
    )
    answers["netatmo_enabled"] = prompt_yes_no(
        "Use Netatmo for live current conditions?",
        default=current_source == "netatmo",
        key="netatmo_enabled",
    )
    if answers["netatmo_enabled"]:
        answers["netatmo_client_id"] = prompt_secret("Netatmo client ID", default=env.get("NETATMO_CLIENT_ID", ""), required=True, key="netatmo_client_id")
        answers["netatmo_client_secret"] = prompt_secret("Netatmo client secret", default=env.get("NETATMO_CLIENT_SECRET", ""), required=True, key="netatmo_client_secret")
        answers["netatmo_refresh_token"] = prompt_secret("Netatmo refresh token", default=env.get("NETATMO_REFRESH_TOKEN", ""), required=True, key="netatmo_refresh_token")
        answers["netatmo_device_id"] = prompt_text("Netatmo device ID (optional)", default=env.get("NETATMO_DEVICE_ID", ""), required=False, key="netatmo_device_id")
        answers["netatmo_module_id"] = prompt_text("Netatmo temperature/humidity module ID (optional)", default=env.get("NETATMO_MODULE_ID", ""), required=False, key="netatmo_module_id")
        answers["netatmo_wind_module_id"] = prompt_text("Netatmo wind module ID (optional)", default=env.get("NETATMO_WIND_MODULE_ID", ""), required=False, key="netatmo_wind_module_id")
        answers["netatmo_rain_module_id"] = prompt_text("Netatmo rain module ID (optional)", default=env.get("NETATMO_RAIN_MODULE_ID", ""), required=False, key="netatmo_rain_module_id")
    else:
        answers["netatmo_client_id"] = env.get("NETATMO_CLIENT_ID", "")
        answers["netatmo_client_secret"] = env.get("NETATMO_CLIENT_SECRET", "")
        answers["netatmo_refresh_token"] = env.get("NETATMO_REFRESH_TOKEN", "")
        answers["netatmo_device_id"] = env.get("NETATMO_DEVICE_ID", "")
        answers["netatmo_module_id"] = env.get("NETATMO_MODULE_ID", "")
        answers["netatmo_wind_module_id"] = env.get("NETATMO_WIND_MODULE_ID", "")
        answers["netatmo_rain_module_id"] = env.get("NETATMO_RAIN_MODULE_ID", "")

    answers["mqtt_weather_enabled"] = prompt_yes_no(
        "Publish weather data to MQTT?",
        default=parse_bool(config.get("mqtt.weather.enabled", "false")),
        key="mqtt_weather_enabled",
    )
    default_mqtt_broker = container_host_alias(mode)
    if answers["mqtt_weather_enabled"]:
        answers["mqtt_weather_broker"] = prompt_text(
            "MQTT weather publisher broker",
            default=config.get("mqtt.weather.broker", default_mqtt_broker),
            required=False,
            key="mqtt_weather_broker",
        )
        answers["mqtt_weather_port"] = prompt_int(
            "MQTT weather publisher port",
            default=int(config.get("mqtt.weather.port", 1883)),
            minimum=1,
            maximum=65535,
            key="mqtt_weather_port",
        )
        answers["mqtt_weather_base_topic"] = prompt_text(
            "MQTT weather base topic",
            default=config.get(
                "mqtt.weather.base_topic", "inkplate/weather-calendar"
            ),
            required=False,
            key="mqtt_weather_base_topic",
        )
    else:
        answers["mqtt_weather_broker"] = config.get(
            "mqtt.weather.broker", default_mqtt_broker
        )
        answers["mqtt_weather_port"] = int(
            config.get("mqtt.weather.port", 1883)
        )
        answers["mqtt_weather_base_topic"] = config.get(
            "mqtt.weather.base_topic", "inkplate/weather-calendar"
        )
    answers["mqtt_diagnostics_enabled"] = prompt_yes_no(
        "Listen for Inkplate diagnostics on MQTT?",
        default=parse_bool(config.get("mqtt.diagnostics.enabled", "false")),
        key="mqtt_diagnostics_enabled",
    )
    if answers["mqtt_diagnostics_enabled"]:
        answers["mqtt_diagnostics_broker"] = prompt_text(
            "MQTT diagnostic listener broker",
            default=config.get("mqtt.diagnostics.broker", default_mqtt_broker),
            required=False,
            key="mqtt_diagnostics_broker",
        )
        answers["mqtt_diagnostics_port"] = prompt_int(
            "MQTT diagnostic listener port",
            default=int(config.get("mqtt.diagnostics.port", 1883)),
            minimum=1,
            maximum=65535,
            key="mqtt_diagnostics_port",
        )
        answers["mqtt_diagnostics_topic"] = prompt_text(
            "MQTT diagnostic topic",
            default=config.get(
                "mqtt.diagnostics.topic",
                "inkplate/weather-calendar/diagnostics",
            ),
            required=False,
            key="mqtt_diagnostics_topic",
        )
    else:
        answers["mqtt_diagnostics_broker"] = config.get(
            "mqtt.diagnostics.broker", default_mqtt_broker
        )
        answers["mqtt_diagnostics_port"] = int(
            config.get("mqtt.diagnostics.port", 1883)
        )
        answers["mqtt_diagnostics_topic"] = config.get(
            "mqtt.diagnostics.topic",
            "inkplate/weather-calendar/diagnostics",
        )
    return answers


def weather_provider_choices() -> list[tuple[str, str]]:
    ordered_names = [
        name for name in WEATHER_PROVIDER_LABELS if name in FORECAST_PROVIDERS
    ]
    ordered_names.extend(
        sorted(set(FORECAST_PROVIDERS) - set(ordered_names))
    )
    return [
        (name, WEATHER_PROVIDER_LABELS.get(name, name))
        for name in ordered_names
    ]


def configured_refresh_minutes(
    config: dict[str, str],
    answers: dict[str, object] | None = None,
) -> int:
    answers = answers or {}
    if "refresh_minutes" not in answers and "refresh_hours" in answers:
        return round(float(answers["refresh_hours"]) * 60)
    if "server.refreshminutes" in config:
        return int(float(config["server.refreshminutes"]))
    if "server.refreshhours" in config:
        return round(float(config["server.refreshhours"]) * 60)
    return DEFAULT_REFRESH_MINUTES


def render_config(answers: dict[str, object], mode: str) -> str:
    token_file = "data/netatmo-token.json"
    alwayson = "true"
    default_mqtt_broker = container_host_alias(mode)
    mqtt_weather_broker = (
        answers["mqtt_weather_broker"] or default_mqtt_broker
    )
    mqtt_diagnostics_broker = (
        answers["mqtt_diagnostics_broker"] or default_mqtt_broker
    )
    lines = [
        "---",
        "server:",
        "  enabled: true",
        f"  host: {json.dumps(str(answers.get('host', DEFAULT_HOST)))}",
        f"  port: {answers['port']}",
        f"  alwayson: {alwayson}",
        f"  refreshminutes: {answers['refresh_minutes']}",
        "weather:",
        f"  service: {answers['weather_service']}",
        "  apikey: ${WEATHER_API_KEY}",
        f"  num_hourly_forecasts: {answers['num_hourly_forecasts']}",
        "  forecastslicehours: {}".format(
            answers.get("forecast_slice_hours", DEFAULT_FORECAST_SLICE_HOURS)
        ),
        "  forecastleadminutes: {}".format(
            answers.get("forecast_lead_minutes", DEFAULT_FORECAST_LEAD_MINUTES)
        ),
        f"  metric: {yaml_bool(bool(answers['metric']))}",
        "current_conditions:",
        f"  source: {'netatmo' if answers['netatmo_enabled'] else 'weather'}",
        "  netatmo:",
        "    client_id: ${NETATMO_CLIENT_ID:-}",
        "    client_secret: ${NETATMO_CLIENT_SECRET:-}",
        "    refresh_token: ${NETATMO_REFRESH_TOKEN:-}",
        f"    token_file: {token_file}",
        "    device_id: ${NETATMO_DEVICE_ID:-}",
        "    module_id: ${NETATMO_MODULE_ID:-}",
        "    wind_module_id: ${NETATMO_WIND_MODULE_ID:-}",
        "    rain_module_id: ${NETATMO_RAIN_MODULE_ID:-}",
        "google:",
        "  apikey: ${GOOGLE_API_KEY}",
        "  staticmaps_mapid: ${GOOGLE_STATICMAPS_MAPID}",
        f"location: {answers['location']}",
        "outputs:",
        f"  default: {DEFAULT_OUTPUT_PROFILE}",
        "  profiles:",
        f"    {DEFAULT_OUTPUT_PROFILE}:",
        "      enabled: true",
        f"      renderer: {DEFAULT_RENDERER}",
        f"      width: {DEFAULT_IMAGE_WIDTH}",
        f"      height: {DEFAULT_IMAGE_HEIGHT}",
        f"      filename: {DEFAULT_OUTPUT_FILENAME}",
        "mqtt:",
        "  weather:",
        f"    enabled: {yaml_bool(bool(answers['mqtt_weather_enabled']))}",
        f"    broker: {mqtt_weather_broker}",
        f"    port: {answers['mqtt_weather_port']}",
        "    base_topic: "
        f"{answers['mqtt_weather_base_topic'] or 'inkplate/weather-calendar'}",
        "    retain: true",
        "    qos: 0",
        "  diagnostics:",
        f"    enabled: {yaml_bool(bool(answers['mqtt_diagnostics_enabled']))}",
        f"    broker: {mqtt_diagnostics_broker}",
        f"    port: {answers['mqtt_diagnostics_port']}",
        "    topic: "
        f"{answers['mqtt_diagnostics_topic'] or 'inkplate/weather-calendar/diagnostics'}",
        "    qos: 0",
        "",
    ]
    return "\n".join(lines)


def render_env(
    answers: dict[str, object],
    include_optional: bool,
    compose: bool = False,
) -> str:
    values = {
        "WEATHER_API_KEY": str(answers["weather_api_key"]),
        "GOOGLE_API_KEY": str(answers["google_api_key"]),
        "GOOGLE_STATICMAPS_MAPID": str(answers["google_staticmaps_mapid"]),
    }
    if compose:
        values["INKPLATE_SERVER_PORT"] = str(
            answers.get("port", DEFAULT_PORT)
        )
    if include_optional:
        values.update(
            {
                "NETATMO_CLIENT_ID": str(answers["netatmo_client_id"]),
                "NETATMO_CLIENT_SECRET": str(answers["netatmo_client_secret"]),
                "NETATMO_REFRESH_TOKEN": str(answers["netatmo_refresh_token"]),
                "NETATMO_DEVICE_ID": str(answers["netatmo_device_id"]),
                "NETATMO_MODULE_ID": str(answers["netatmo_module_id"]),
                "NETATMO_WIND_MODULE_ID": str(answers["netatmo_wind_module_id"]),
                "NETATMO_RAIN_MODULE_ID": str(answers["netatmo_rain_module_id"]),
            }
        )
    lines = [
        "# Generated by bin/install_server. Re-run the installer to update.",
    ]
    lines.extend(f"{key}={format_env_value(value)}" for key, value in values.items())
    lines.append("")
    return "\n".join(lines)


def container_host_alias(mode: str) -> str:
    if mode == "docker":
        return "host.docker.internal"
    if mode == "podman":
        return "host.containers.internal"
    return "localhost"


def check_compose_runtime(mode: str, dry_run: bool) -> list[str]:
    if mode == "docker":
        check_docker_compose(dry_run)
        return ["docker", "compose"]
    return check_podman_compose(dry_run)


def check_compose_host_port(
    compose_command: list[str],
    port: int,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"Would check: host TCP port {port} is available")
        return
    if compose_service_is_running(compose_command, "inkplate"):
        return
    if tcp_port_is_available(port):
        return
    raise SystemExit(
        f"ERROR: host TCP port {port} is already in use. "
        "Stop the existing listener or rerun the installer and choose a "
        "different Server port. Inspect it with: "
        f"ss -ltnp 'sport = :{port}'"
    )


def compose_service_is_running(
    compose_command: list[str],
    service: str,
) -> bool:
    result = subprocess.run(
        [*compose_command, "ps", "-q", service],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def tcp_port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def check_docker_compose(dry_run: bool) -> None:
    if dry_run:
        print("Would check: docker compose version")
        print("Would check: docker info")
        print("Would check Docker socket access when using the default local socket.")
        return
    if not shutil.which("docker"):
        raise SystemExit("ERROR: docker was not found on PATH.")
    result = subprocess.run(["docker", "compose", "version"], text=True)
    if result.returncode != 0:
        raise SystemExit("ERROR: 'docker compose' is not available.")
    docker_info = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if docker_info.returncode != 0:
        socket_hint = docker_socket_hint()
        raise SystemExit(
            "ERROR: Docker is installed, but this user cannot talk to the Docker "
            f"daemon. {socket_hint}\nDocker error: {docker_info.stderr.strip()}"
        )
    print("Docker check: docker compose and daemon access are available.")


def check_podman_compose(dry_run: bool) -> list[str]:
    if dry_run:
        print("Would check: podman info")
        print("Would check: podman compose version or podman-compose version")
        return ["podman", "compose"]
    if not shutil.which("podman"):
        raise SystemExit("ERROR: podman was not found on PATH.")
    podman_info = subprocess.run(
        ["podman", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if podman_info.returncode != 0:
        raise SystemExit(
            "ERROR: Podman is installed but is not usable by this user.\n"
            f"Podman error: {podman_info.stderr.strip()}"
        )

    native_compose = subprocess.run(
        ["podman", "compose", "version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if native_compose.returncode == 0:
        print("Podman check: podman compose is available.")
        return ["podman", "compose"]

    if shutil.which("podman-compose"):
        external_compose = subprocess.run(
            ["podman-compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if external_compose.returncode == 0:
            print("Podman check: podman-compose is available.")
            return ["podman-compose"]

    raise SystemExit(
        "ERROR: neither 'podman compose' nor 'podman-compose' is available."
    )


def docker_socket_hint() -> str:
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host:
        return f"DOCKER_HOST is set to {docker_host!r}; check that endpoint and credentials."

    socket_path = Path("/var/run/docker.sock")
    if not socket_path.exists():
        return "The default Docker socket /var/run/docker.sock was not found; is the Docker daemon running?"
    if os.access(socket_path, os.R_OK | os.W_OK):
        return "The default Docker socket /var/run/docker.sock appears accessible; check that the daemon is running."
    return (
        "The default Docker socket /var/run/docker.sock exists but is not readable/writable "
        "by this user. Run Docker as root/rootless Docker, or add the user to the docker group."
    )


def validate_native_platform(dry_run: bool) -> None:
    if not Path("/etc/os-release").exists() and not dry_run:
        raise SystemExit("ERROR: /etc/os-release not found; native install expects Debian/Ubuntu.")
    if not shutil.which("apt") and not dry_run:
        raise SystemExit("ERROR: apt not found; native install supports Debian/Ubuntu hosts.")
    if not Path("/run/systemd/system").exists() and not dry_run:
        raise SystemExit("ERROR: systemd is not available on this host.")
    arch = normalized_arch()
    if arch not in ("amd64", "arm64"):
        raise SystemExit(f"ERROR: unsupported architecture for Geckodriver: {platform.machine()}")
    print("Native install target: Debian/Ubuntu with systemd.")
    configure_privilege_escalation(dry_run)


def configure_privilege_escalation(dry_run: bool) -> None:
    global PRIVILEGE_PREFIX

    if os.geteuid() == 0:
        PRIVILEGE_PREFIX = []
        print("Privilege check: running as root.")
        return

    candidates = [
        ("sudo", ["sudo", "-v"], ["sudo"]),
        ("doas", ["doas", "true"], ["doas"]),
        ("run0", ["run0", "true"], ["run0"]),
    ]
    available = [
        (name, check_cmd, prefix)
        for name, check_cmd, prefix in candidates
        if shutil.which(name)
    ]

    if not available:
        raise SystemExit(
            "ERROR: native systemd install needs root privileges, but this user is "
            "not root and no supported elevation command was found. Run as root or "
            "install/configure one of: sudo, doas, run0."
        )

    name, check_cmd, prefix = available[0]
    PRIVILEGE_PREFIX = prefix
    if dry_run:
        print(f"Privilege check: would use {name} for system changes.")
        return

    print(f"Privilege check: validating {name} access.")
    result = subprocess.run(check_cmd, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: {name} is installed, but privilege elevation failed. "
            "Run the installer as root or fix the user's elevation permissions."
        )
    print(f"Privilege check: {name} elevation is available.")


def install_native_prerequisites(dry_run: bool) -> None:
    firefox_package = choose_firefox_package(dry_run)
    packages = [
        "ca-certificates",
        "curl",
        firefox_package,
        "python3",
        "python3-pip",
        "python3-venv",
    ]
    run(["apt-get", "update"], sudo=True, dry_run=dry_run)
    run(["apt-get", "install", "-y", "--no-install-recommends", *packages], sudo=True, dry_run=dry_run)


def choose_firefox_package(dry_run: bool) -> str:
    if dry_run:
        return "firefox-esr"
    result = subprocess.run(
        ["apt-cache", "show", "firefox-esr"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return "firefox-esr" if result.returncode == 0 else "firefox"


def ensure_system_user(dry_run: bool) -> None:
    result = subprocess.run(["id", APP_USER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        print(f"System user already exists: {APP_USER}")
        return
    run(
        ["useradd", "--system", "--create-home", "--user-group", "--home-dir", str(INSTALL_DIR), APP_USER],
        sudo=True,
        dry_run=dry_run,
    )


def copy_repo(repo_root: Path, install_dir: Path, dry_run: bool) -> None:
    prepare_version_manifest(repo_root, dry_run=dry_run)
    if repo_root.resolve() == install_dir.resolve():
        print(f"Repository is already at {install_dir}; skipping copy.")
        return
    print(f"Copying application files to {install_dir}")
    if dry_run:
        print(f"Would create {install_dir} and copy repository files excluding local/generated data.")
        return
    run(["mkdir", "-p", str(install_dir)], sudo=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "inkplate"
        ignore = install_copy_ignore(repo_root)
        shutil.copytree(repo_root, tmp_path, ignore=ignore)
        run(["cp", "-a", f"{tmp_path}/.", str(install_dir)], sudo=True)
    verify_installed_runtime(repo_root, install_dir)
    run(["mkdir", "-p", str(install_dir / "server" / "config"), str(install_dir / "server" / "data")], sudo=True)
    run(["chown", "-R", f"{APP_USER}:{APP_GROUP}", str(install_dir)], sudo=True)


def prepare_version_manifest(repo_root: Path, dry_run: bool) -> dict:
    existing = read_version_manifest(repo_root)
    has_git_checkout = (repo_root / ".git").exists()
    if dry_run:
        version = existing.get("version", "<derived from checkout>")
        print(f"Would record application version: {version}")
        return existing
    if not has_git_checkout and not existing:
        raise SystemExit(
            "ERROR: application version metadata is unavailable. Run the "
            "installer from a Git checkout or a deployment bundle containing "
            f"{VERSION_MANIFEST_FILENAME}."
        )
    try:
        manifest = (
            generate_version_manifest(repo_root)
            if has_git_checkout
            else existing
        )
    except ValueError as exc:
        raise SystemExit(
            f"ERROR: unable to generate {VERSION_MANIFEST_FILENAME}: {exc}"
        ) from None
    print(f"Recorded application version: {manifest['version']}")
    return manifest


def verify_installed_runtime(repo_root: Path, install_dir: Path) -> None:
    runtime_files = (
        Path(VERSION_MANIFEST_FILENAME),
        Path("server/server.py"),
        Path("server/producer_config.py"),
        Path("bin/install_server.py"),
    )
    mismatches = []
    for relative in runtime_files:
        try:
            matches = filecmp.cmp(
                repo_root / relative,
                install_dir / relative,
                shallow=False,
            )
        except OSError:
            matches = False
        if not matches:
            mismatches.append(str(relative))
    if mismatches:
        raise SystemExit(
            "ERROR: installed application files do not match the checkout: "
            + ", ".join(mismatches)
        )
    print("Verified installed application files match the checkout.")


def ensure_venv(dry_run: bool) -> None:
    py = VENV_DIR / "bin" / "python"
    if py.exists():
        print(f"Virtualenv already exists: {VENV_DIR}")
        return
    run(["python3", "-m", "venv", str(VENV_DIR)], sudo=True, dry_run=dry_run)
    run(["chown", "-R", f"{APP_USER}:{APP_GROUP}", str(VENV_DIR)], sudo=True, dry_run=dry_run)


def refresh_dependencies(dry_run: bool) -> None:
    run(
        ["bin/refresh_deps", "--venv", str(VENV_DIR), "--requirements", str(INSTALL_DIR / "server" / "requirements.txt")],
        sudo=True,
        dry_run=dry_run,
    )
    run(["chown", "-R", f"{APP_USER}:{APP_GROUP}", str(VENV_DIR)], sudo=True, dry_run=dry_run)


def remove_legacy_application_logs(dry_run: bool) -> None:
    run(
        [
            "find",
            str(INSTALL_DIR),
            "-maxdepth",
            "1",
            "-type",
            "f",
            "-name",
            "eink-cal-server.log*",
            "-delete",
        ],
        sudo=True,
        dry_run=dry_run,
    )


def install_copy_ignore(repo_root: Path):
    base_ignore = shutil.ignore_patterns(
        ".git",
        ".env",
        ".venv",
        "venv",
        "env",
        "build",
        ".tools",
        ".pytest_cache",
        "__pycache__",
        "*.pyc",
        "*.log",
        "netatmo-token.json",
    )

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(base_ignore(directory, names))
        try:
            relative = Path(directory).resolve().relative_to(repo_root.resolve())
        except ValueError:
            relative = Path()
        if relative == Path("server/config"):
            ignored.add("config.yaml")
        if relative == Path("server/views"):
            ignored.update({"calendar.png", "calendar.bmp"})
        if relative == Path("server/views/html"):
            ignored.update({"calendar.html", "map.png", "map.bmp"})
        return ignored

    return ignore


def install_geckodriver(repo_root: Path, dry_run: bool) -> None:
    version = dockerfile_geckodriver_version(repo_root / "Dockerfile")
    gecko_arch = {"amd64": "linux64", "arm64": "linux-aarch64"}[normalized_arch()]
    url = f"https://github.com/mozilla/geckodriver/releases/download/{version}/geckodriver-{version}-{gecko_arch}.tar.gz"
    archive = Path("/tmp") / f"geckodriver-{version}-{gecko_arch}.tar.gz"
    run(["curl", "-fsSL", "-o", str(archive), url], sudo=True, dry_run=dry_run)
    run(["tar", "xzf", str(archive), "-C", "/usr/local/bin"], sudo=True, dry_run=dry_run)
    run(["chmod", "0755", "/usr/local/bin/geckodriver"], sudo=True, dry_run=dry_run)


def dockerfile_geckodriver_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^ARG\s+GECKOVERSION=(\S+)", text, re.MULTILINE)
    if not match:
        raise SystemExit("ERROR: could not find GECKOVERSION in Dockerfile.")
    return match.group(1)


def normalized_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = unquote_env_value(value.strip())
    return values


def read_simple_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    stack: list[tuple[int, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = strip_inline_comment(raw.strip())
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path_parts = [item[1] for item in stack] + [key]
        if value:
            values[".".join(path_parts)] = value.strip("'\"")
        else:
            stack.append((indent, key))
    return values


def strip_inline_comment(value: str) -> str:
    if "#" not in value:
        return value
    return value.split("#", 1)[0].rstrip()


def write_text_atomic(path: Path, text: str, dry_run: bool, mode: int, sudo: bool = False) -> None:
    if dry_run:
        print(f"Would write {path} with mode {oct(mode)}")
        print_preview(path, text)
        return

    if sudo and os.geteuid() != 0:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        try:
            if path.exists():
                backup = backup_path(path)
                run(["cp", "-a", str(path), str(backup)], sudo=True)
                print(f"Backed up {path} to {backup}")
            run(["mkdir", "-p", str(path.parent)], sudo=True)
            run(["install", "-m", oct(mode)[2:], str(tmp_path), str(path)], sudo=True)
        finally:
            tmp_path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = backup_path(path)
        shutil.copy2(path, backup)
        print(f"Backed up {path} to {backup}")
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    os.chmod(tmp_path, mode)
    tmp_path.replace(path)


def backup_path(path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.bak.{timestamp}")


def run(cmd: list[str], dry_run: bool = False, sudo: bool = False, check: bool = True) -> subprocess.CompletedProcess[str] | None:
    full_cmd = list(cmd)
    if sudo and os.geteuid() != 0:
        if not PRIVILEGE_PREFIX:
            raise SystemExit(
                "ERROR: this action needs root privileges, but no elevation "
                "command has been configured."
            )
        full_cmd = PRIVILEGE_PREFIX + full_cmd
    printable = " ".join(full_cmd)
    if dry_run:
        print(f"Would run: {printable}")
        return None
    print(f"Running: {printable}")
    return subprocess.run(full_cmd, check=check, text=True)


def print_preview(path: Path, text: str) -> None:
    print(f"Preview for {path}:")
    preview = redact_env_text(text) if path.name in (".env", "weather.env") else text
    for line in preview.rstrip().splitlines():
        print(f"  {line}")


def redact_env_text(text: str) -> str:
    redacted = []
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            redacted.append(line)
            continue
        key, value = line.split("=", 1)
        redacted.append(f"{key}=<redacted>" if value else line)
    return "\n".join(redacted)


def answer_value(key: str | None, default):
    if key is None or key not in INSTALLER_ANSWERS:
        return default
    return INSTALLER_ANSWERS[key]


def require_non_interactive_value(key: str | None, default, required: bool):
    value = answer_value(key, None)
    if value is not None:
        return value
    if default not in ("", None):
        return default
    if required:
        raise SystemExit(f"ERROR: missing required non-interactive answer: {key}")
    return default


def prompt_choice(label: str, options: list[tuple[str, str]], default: str, key: str | None = None) -> str:
    allowed = {key for key, _ in options}
    answered = answer_value(key, None)
    if answered is not None:
        answered = str(answered)
        if answered not in allowed:
            raise SystemExit(f"ERROR: answer {key} must be one of: {', '.join(sorted(allowed))}")
        if NON_INTERACTIVE:
            print(f"{label}: {answered}")
        return answered
    if NON_INTERACTIVE:
        if default not in allowed:
            raise SystemExit(f"ERROR: missing required non-interactive answer: {key}")
        print(f"{label}: {default}")
        return default
    while True:
        print(label + ":")
        for index, (key, description) in enumerate(options, start=1):
            suffix = " [default]" if key == default else ""
            print(f"  {index}. {key} - {description}{suffix}")
        raw = input(f"Choose [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        if raw in allowed:
            return raw
        print("Please choose one of the listed options.")


def prompt_text(label: str, default: str = "", required: bool = True, key: str | None = None) -> str:
    if key in INSTALLER_ANSWERS or NON_INTERACTIVE:
        value = require_non_interactive_value(key, default, required)
        value = "" if value is None else str(value)
        print(f"{label}: {value if value else '<empty>'}")
        return value
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print("This value is required.")


def prompt_ip_address(label: str, default: str, key: str) -> str:
    while True:
        value = prompt_text(label, default=default, key=key)
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if key in INSTALLER_ANSWERS or NON_INTERACTIVE:
                raise SystemExit(
                    f"ERROR: {key} must be an IPv4 or IPv6 address"
                )
            print("Please enter an IPv4 or IPv6 address.")
            default = value
            continue
        return value


def prompt_secret(label: str, default: str = "", required: bool = True, key: str | None = None) -> str:
    if key in INSTALLER_ANSWERS or NON_INTERACTIVE:
        value = require_non_interactive_value(key, default, required)
        value = "" if value is None else str(value)
        print(f"{label}: {'<provided>' if value else '<empty>'}")
        return value
    while True:
        suffix = " [keep existing]" if default else ""
        value = getpass.getpass(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print("This value is required.")


def prompt_int(label: str, default: int, minimum: int, maximum: int, key: str | None = None) -> int:
    if key in INSTALLER_ANSWERS or NON_INTERACTIVE:
        value = require_non_interactive_value(key, default, required=True)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise SystemExit(f"ERROR: answer {key} must be an integer.")
        if not minimum <= parsed <= maximum:
            raise SystemExit(f"ERROR: answer {key} must be from {minimum} to {maximum}.")
        print(f"{label}: {parsed}")
        return parsed
    while True:
        value = input(f"{label} [{default}]: ").strip()
        if not value:
            return default
        try:
            parsed = int(value)
        except ValueError:
            print("Enter a number.")
            continue
        if minimum <= parsed <= maximum:
            return parsed
        print(f"Enter a value from {minimum} to {maximum}.")


def prompt_yes_no(label: str, default: bool, key: str | None = None) -> bool:
    if key in INSTALLER_ANSWERS or NON_INTERACTIVE:
        value = answer_value(key, default)
        parsed = parse_bool(value)
        print(f"{label}: {'yes' if parsed else 'no'}")
        return parsed
    default_text = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{default_text}]: ").strip().lower()
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("Enter yes or no.")


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def format_env_value(value: str) -> str:
    if value == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:=+@%-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def run_cli(main_func=main) -> int:
    try:
        return main_func()
    except KeyboardInterrupt:
        print("\nInstaller cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(run_cli())

#!/usr/bin/env python3
"""Interactive server installer for Inkplate Weather Calendar.

The installer intentionally uses only the Python standard library so it can run
before the server virtualenv and Python package dependencies exist.
"""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


APP_USER = "inkplate"
APP_GROUP = "inkplate"
INSTALL_DIR = Path("/srv/inkplate")
VENV_DIR = INSTALL_DIR / "inkplate_venv"
NATIVE_ENV_FILE = Path("/etc/inkplate/env")
SERVICE_FILE = Path("/etc/systemd/system/inkplate.service")
DOCKER_ENV_FILE = Path(".env")
SERVER_CONFIG = Path("server/config.yaml")
DEFAULT_PORT = 8080
DEFAULT_REFRESH_HOURS = 3
DEFAULT_FORECASTS = 6
DEFAULT_LOCATION = "Landry, FR"
DEFAULT_WEATHER = "openweathermapv3"
DEFAULT_IMAGE_WIDTH = 825
DEFAULT_IMAGE_HEIGHT = 1200


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
        choices=("docker", "systemd"),
        help="install mode; prompts when omitted",
    )
    args = parser.parse_args()

    repo_root = Path.cwd()
    ensure_repo_root(repo_root)

    print("Inkplate Weather Calendar server installer")
    print("------------------------------------------")
    if args.dry_run:
        print("Dry run: no files will be written and no commands will be run.")
    print()

    mode = args.mode or prompt_choice(
        "Install type",
        [
            ("docker", "Docker Compose install from this checkout"),
            ("systemd", "Native systemd install under /srv/inkplate"),
        ],
        default="docker",
    )

    if mode == "docker":
        install_docker(repo_root, args.dry_run)
    else:
        install_systemd(repo_root, args.dry_run)

    print()
    print("Installer finished.")
    return 0


def ensure_repo_root(repo_root: Path) -> None:
    required = [
        repo_root / "server" / "server.py",
        repo_root / "server" / "requirements.txt",
        repo_root / "docker-compose.yml",
        repo_root / "bin" / "inkplate.service",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("ERROR: run this installer from the repository root.", file=sys.stderr)
        for path in missing:
            print(f"Missing: {path}", file=sys.stderr)
        raise SystemExit(1)


def install_docker(repo_root: Path, dry_run: bool) -> None:
    existing = [
        path
        for path in (repo_root / DOCKER_ENV_FILE, repo_root / SERVER_CONFIG)
        if path.exists()
    ]
    action = choose_existing_action("Docker", existing)
    if action == "abort":
        print("No changes made.")
        return

    if action in ("fresh", "reconfigure"):
        current_env = read_env_file(repo_root / DOCKER_ENV_FILE)
        current_config = read_simple_yaml(repo_root / SERVER_CONFIG)
        answers = collect_answers(current_env, current_config, mode="docker")
        write_text_atomic(
            repo_root / SERVER_CONFIG,
            render_config(answers, mode="docker"),
            dry_run=dry_run,
            mode=0o644,
        )
        write_text_atomic(
            repo_root / DOCKER_ENV_FILE,
            render_env(answers, include_optional=answers["netatmo_enabled"]),
            dry_run=dry_run,
            mode=0o600,
        )

    check_docker_compose(dry_run)
    if prompt_yes_no("Start or update the Docker container now?", default=True):
        run(["docker", "compose", "up", "--build", "-d"], dry_run=dry_run)
        print("Logs: docker compose logs -f")
    else:
        print("Start later with: docker compose up --build -d")


def install_systemd(repo_root: Path, dry_run: bool) -> None:
    existing = [
        path
        for path in (INSTALL_DIR, NATIVE_ENV_FILE, SERVICE_FILE)
        if path.exists()
    ]
    action = choose_existing_action("systemd", existing)
    if action == "abort":
        print("No changes made.")
        return

    validate_native_platform(dry_run)

    if action in ("fresh", "update"):
        install_native_prerequisites(dry_run)
        ensure_system_user(dry_run)
        copy_repo(repo_root, INSTALL_DIR, dry_run)
        ensure_venv(dry_run)
        refresh_dependencies(dry_run)
        install_geckodriver(repo_root, dry_run)

    if action in ("fresh", "reconfigure"):
        current_env = read_env_file(NATIVE_ENV_FILE)
        current_config = read_simple_yaml(INSTALL_DIR / SERVER_CONFIG)
        answers = collect_answers(current_env, current_config, mode="systemd")
        write_text_atomic(
            INSTALL_DIR / SERVER_CONFIG,
            render_config(answers, mode="systemd"),
            dry_run=dry_run,
            mode=0o640,
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

    if action in ("fresh", "update"):
        run(
            ["bin/install_service", "--unit-file", "bin/inkplate.service", "--no-start"],
            dry_run=dry_run,
        )

    if prompt_yes_no("Start or restart the systemd service now?", default=True):
        run(["systemctl", "restart", "inkplate"], sudo=True, dry_run=dry_run)
        run(["systemctl", "status", "inkplate", "--no-pager", "-l"], sudo=True, dry_run=dry_run, check=False)
        print("Logs: sudo journalctl -u inkplate -f")
    else:
        print("Start later with: sudo systemctl start inkplate")


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
            ("reconfigure", "rewrite config/secrets only"),
            ("abort", "make no changes"),
        ],
        default="update",
    )


def collect_answers(env: dict[str, str], config: dict[str, str], mode: str) -> dict[str, object]:
    print()
    print("Configuration")
    print("-------------")

    answers: dict[str, object] = {}
    answers["port"] = prompt_int(
        "Server port",
        default=int(config.get("server.port", DEFAULT_PORT)),
        minimum=1,
        maximum=65535,
    )
    answers["refresh_hours"] = prompt_int(
        "Refresh interval in hours",
        default=int(config.get("server.refreshhours", DEFAULT_REFRESH_HOURS)),
        minimum=1,
        maximum=24,
    )
    answers["location"] = prompt_text(
        "Location for weather/map lookup",
        default=config.get("location", DEFAULT_LOCATION),
    )
    answers["weather_service"] = prompt_choice(
        "Weather provider",
        [
            ("openweathermapv3", "OpenWeatherMap One Call 3.0"),
            ("accuweather", "AccuWeather"),
        ],
        default=config.get("weather.service", DEFAULT_WEATHER),
    )
    answers["weather_api_key"] = prompt_secret(
        "Weather API key",
        default=env.get("WEATHER_API_KEY", ""),
        required=True,
    )
    answers["google_api_key"] = prompt_secret(
        "Google Static Maps API key",
        default=env.get("GOOGLE_API_KEY", ""),
        required=True,
    )
    answers["google_staticmaps_mapid"] = prompt_secret(
        "Google Static Maps Map ID",
        default=env.get("GOOGLE_STATICMAPS_MAPID", ""),
        required=True,
    )
    answers["num_hourly_forecasts"] = prompt_int(
        "Number of forecast slots",
        default=int(config.get("weather.num_hourly_forecasts", DEFAULT_FORECASTS)),
        minimum=1,
        maximum=12,
    )
    answers["metric"] = prompt_yes_no("Use metric units?", default=parse_bool(config.get("weather.metric", "true")))

    current_source = config.get("current_temperature.source", "weather")
    answers["netatmo_enabled"] = prompt_yes_no(
        "Use Netatmo for live current temperature?",
        default=current_source == "netatmo",
    )
    if answers["netatmo_enabled"]:
        answers["netatmo_client_id"] = prompt_secret("Netatmo client ID", default=env.get("NETATMO_CLIENT_ID", ""), required=True)
        answers["netatmo_client_secret"] = prompt_secret("Netatmo client secret", default=env.get("NETATMO_CLIENT_SECRET", ""), required=True)
        answers["netatmo_refresh_token"] = prompt_secret("Netatmo refresh token", default=env.get("NETATMO_REFRESH_TOKEN", ""), required=True)
        answers["netatmo_device_id"] = prompt_text("Netatmo device ID (optional)", default=env.get("NETATMO_DEVICE_ID", ""), required=False)
        answers["netatmo_module_id"] = prompt_text("Netatmo module ID (optional)", default=env.get("NETATMO_MODULE_ID", ""), required=False)
    else:
        answers["netatmo_client_id"] = env.get("NETATMO_CLIENT_ID", "")
        answers["netatmo_client_secret"] = env.get("NETATMO_CLIENT_SECRET", "")
        answers["netatmo_refresh_token"] = env.get("NETATMO_REFRESH_TOKEN", "")
        answers["netatmo_device_id"] = env.get("NETATMO_DEVICE_ID", "")
        answers["netatmo_module_id"] = env.get("NETATMO_MODULE_ID", "")

    answers["mqtt_enabled"] = prompt_yes_no(
        "Enable MQTT client logging?",
        default=parse_bool(config.get("mqtt.enabled", "false")),
    )
    default_mqtt_host = "host.docker.internal" if mode == "docker" else "localhost"
    answers["mqtt_host"] = prompt_text(
        "MQTT host",
        default=config.get("mqtt.host", default_mqtt_host),
        required=False,
    )
    answers["mqtt_port"] = prompt_int(
        "MQTT port",
        default=int(config.get("mqtt.port", 1883)),
        minimum=1,
        maximum=65535,
    )
    answers["mqtt_topic"] = prompt_text(
        "MQTT topic",
        default=config.get("mqtt.topic", "mqtt/eink-cal-client"),
        required=False,
    )
    return answers


def render_config(answers: dict[str, object], mode: str) -> str:
    token_file = "data/netatmo-token.json" if mode == "docker" else "netatmo-token.json"
    alwayson = "true"
    mqtt_host = answers["mqtt_host"] or ("host.docker.internal" if mode == "docker" else "localhost")
    lines = [
        "---",
        "server:",
        "  enabled: true",
        f"  port: {answers['port']}",
        f"  alwayson: {alwayson}",
        f"  refreshhours: {answers['refresh_hours']}",
        "weather:",
        f"  service: {answers['weather_service']}",
        "  apikey: ${WEATHER_API_KEY}",
        f"  num_hourly_forecasts: {answers['num_hourly_forecasts']}",
        f"  metric: {yaml_bool(bool(answers['metric']))}",
        "current_temperature:",
        f"  source: {'netatmo' if answers['netatmo_enabled'] else 'weather'}",
        "  netatmo:",
        "    client_id: ${NETATMO_CLIENT_ID:-}",
        "    client_secret: ${NETATMO_CLIENT_SECRET:-}",
        "    refresh_token: ${NETATMO_REFRESH_TOKEN:-}",
        f"    token_file: {token_file}",
        "    device_id: ${NETATMO_DEVICE_ID:-}",
        "    module_id: ${NETATMO_MODULE_ID:-}",
        "google:",
        "  apikey: ${GOOGLE_API_KEY}",
        "  staticmaps_mapid: ${GOOGLE_STATICMAPS_MAPID}",
        f"location: {answers['location']}",
        "image:",
        f"  width: {DEFAULT_IMAGE_WIDTH}",
        f"  height: {DEFAULT_IMAGE_HEIGHT}",
        "mqtt:",
        f"  enabled: {yaml_bool(bool(answers['mqtt_enabled']))}",
        f"  host: {mqtt_host}",
        f"  port: {answers['mqtt_port']}",
        f"  topic: {answers['mqtt_topic'] or 'mqtt/eink-cal-client'}",
        "",
    ]
    return "\n".join(lines)


def render_env(answers: dict[str, object], include_optional: bool) -> str:
    values = {
        "WEATHER_API_KEY": str(answers["weather_api_key"]),
        "GOOGLE_API_KEY": str(answers["google_api_key"]),
        "GOOGLE_STATICMAPS_MAPID": str(answers["google_staticmaps_mapid"]),
    }
    if include_optional:
        values.update(
            {
                "NETATMO_CLIENT_ID": str(answers["netatmo_client_id"]),
                "NETATMO_CLIENT_SECRET": str(answers["netatmo_client_secret"]),
                "NETATMO_REFRESH_TOKEN": str(answers["netatmo_refresh_token"]),
                "NETATMO_DEVICE_ID": str(answers["netatmo_device_id"]),
                "NETATMO_MODULE_ID": str(answers["netatmo_module_id"]),
            }
        )
    lines = [
        "# Generated by bin/install_server. Re-run the installer to update.",
    ]
    lines.extend(f"{key}={format_env_value(value)}" for key, value in values.items())
    lines.append("")
    return "\n".join(lines)


def check_docker_compose(dry_run: bool) -> None:
    if dry_run:
        print("Would check: docker compose version")
        return
    if not shutil.which("docker"):
        raise SystemExit("ERROR: docker was not found on PATH.")
    result = subprocess.run(["docker", "compose", "version"], text=True)
    if result.returncode != 0:
        raise SystemExit("ERROR: 'docker compose' is not available.")


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
    run(["mkdir", "-p", str(install_dir / "server" / "data")], sudo=True)
    run(["chown", "-R", f"{APP_USER}:{APP_GROUP}", str(install_dir)], sudo=True)


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
        "*.png",
        "*.bmp",
        "netatmo-token.json",
    )

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(base_ignore(directory, names))
        try:
            relative = Path(directory).resolve().relative_to(repo_root.resolve())
        except ValueError:
            relative = Path()
        if relative == Path("server"):
            ignored.add("config.yaml")
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
        full_cmd.insert(0, "sudo")
    printable = " ".join(full_cmd)
    if dry_run:
        print(f"Would run: {printable}")
        return None
    print(f"Running: {printable}")
    return subprocess.run(full_cmd, check=check, text=True)


def prompt_choice(label: str, options: list[tuple[str, str]], default: str) -> str:
    allowed = {key for key, _ in options}
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


def prompt_text(label: str, default: str = "", required: bool = True) -> str:
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


def prompt_secret(label: str, default: str = "", required: bool = True) -> str:
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


def prompt_int(label: str, default: int, minimum: int, maximum: int) -> int:
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


def prompt_yes_no(label: str, default: bool) -> bool:
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


if __name__ == "__main__":
    raise SystemExit(main())

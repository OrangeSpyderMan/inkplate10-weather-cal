#!/usr/bin/env python3
"""Install WeatherProxy without requiring calendar or Python dependencies first."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def write_file(path, content, dry_run, mode=0o600):
    if dry_run:
        print(f"Would write {path} (mode {mode:04o})")
        return
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


def systemd_unit(directory, command):
    # systemd command parsing supports double quoted arguments, not shell quoting.
    executable = json.dumps(str(directory / "venv/bin/python3"))
    config = json.dumps(str(directory / "weather-proxy.yaml"))
    return f"""[Unit]
Description=WeatherProxy {command}
After=network-online.target
Wants=network-online.target

[Service]
User=weatherproxy
Group=weatherproxy
WorkingDirectory={json.dumps(str(directory))}
Environment={json.dumps(f"WEATHER_PROXY_ENV_FILE={directory}/weather.env")}
Environment={json.dumps(f"WEATHER_PROXY_STORAGE_PATH={directory}/data/weather-proxy.sqlite3")}
ExecStart={executable} -m weather_proxy {command} --config {config}
Restart=on-failure
RestartSec=5
TimeoutStopSec=40
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={json.dumps(str(directory / "data"))}

[Install]
WantedBy=multi-user.target
"""


def compose_config(directory, image, port, build=False, mode="docker"):
    # JSON is a YAML subset, and avoids introducing a bootstrap PyYAML dependency.
    service = {
        "image": image, "init": True, "restart": "unless-stopped",
        "ports": [f"{port}:8090"],
        "user": f"{os.getuid() or 10001}:{os.getgid() or 10001}",
        "volumes": [f"{directory}:/config:ro", f"{directory / 'data'}:/data:rw"],
        "healthcheck": {"test": ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/v1/health', timeout=5)"],
                        "interval": "30s", "timeout": "10s", "retries": 3},
    }
    if build:
        service["build"] = {"context": str(ROOT), "dockerfile": "Dockerfile.weatherproxy"}
    if mode == "podman":
        if os.getuid():
            service["userns_mode"] = "keep-id"
        service["volumes"] = [value + ",Z" for value in service["volumes"]]
    return {"services": {"weather-proxy": service}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("docker", "podman", "systemd", "oci", "proxmox"), default="docker")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="Complete multi-location proxy YAML; preserves all configured accounts")
    source.add_argument("--migrate-calendar", type=Path, help="Convert an existing direct-provider calendar configuration")
    parser.add_argument("--env-file", type=Path, help="Literal KEY=value secrets file")
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--image", default="ghcr.io/orangespyderman/weatherproxy:next")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--build", action="store_true", help="Build the checkout instead of pulling a published image")
    parser.add_argument("--replace-config", action="store_true", help="Explicitly replace existing config/secrets (backups retained)")
    parser.add_argument("--start", action="store_true", help="Start or restart after configuration")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ctid", type=int)
    parser.add_argument("--storage")
    parser.add_argument("--tag")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--timezone")
    parser.add_argument("--location-id", default="calendar")
    parser.add_argument("--proxy-url", help="Proxy URL as reachable from the calendar runtime")
    parser.add_argument("--netatmo-user-id")
    parser.add_argument("--netatmo-device-id")
    parser.add_argument("--token-file", type=Path, help="Latest calendar Netatmo token file when outside the normal server data directory")
    parser.add_argument("--mqtt-broker", help="Override migrated broker address for the new deployment network")
    parser.add_argument("--activate-calendar", action="store_true", help="Verify proxy weather, back up, and switch the calendar config after deployment")
    parser.add_argument("--verify-url", help="Proxy URL reachable from this installer host, if different from --proxy-url")
    parser.add_argument("--migration-timeout", type=float, default=180, help="Seconds to wait for proxy weather before automatic calendar activation")
    parser.add_argument("--migration-dir", type=Path, help="Private output directory for a new migration plan")
    args = parser.parse_args(argv)
    if not (args.config or args.migrate_calendar).is_file() or (args.env_file and not args.env_file.is_file()):
        parser.error("Configuration and supplied environment files must exist")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    directory = (args.install_dir or Path("/srv/weatherproxy" if args.mode == "systemd" else "./weather-proxy-install")).resolve()
    if args.mode == "systemd" and not args.dry_run and os.geteuid() != 0:
        parser.error("systemd installation requires root")
    if any(char in str(directory) for char in "\n\r%"):
        parser.error("Installation path contains unsupported characters")
    migration_directory = None
    if args.activate_calendar and not args.migrate_calendar:
        parser.error("--activate-calendar requires --migrate-calendar")
    if args.activate_calendar and not args.start and args.mode != "proxmox":
        parser.error("--activate-calendar requires --start; alternatively activate the prepared plan later")
    if args.migrate_calendar:
        if args.latitude is None or args.longitude is None or not args.timezone or not args.proxy_url:
            parser.error("Migration requires --latitude, --longitude, --timezone, and --proxy-url")
        sys.path.insert(0, str(ROOT / "server"))
        try:
            from weather_proxy.migrate import build_plan, write_plan, activate
        except ModuleNotFoundError:
            parser.error("Migration requires PyYAML/requests: run this installer with the existing calendar virtualenv Python, or use weather-proxy migrate-calendar from the installed proxy package")
        if not args.replace_config and any((directory / name).exists() for name in ("weather-proxy.yaml", "weather.env")):
            parser.error("Proxy config already exists; select a new install directory or explicitly use --replace-config")
        migration_directory = (args.migration_dir or directory / "migration").resolve()
        try:
            plan = build_plan(args.migrate_calendar, latitude=args.latitude, longitude=args.longitude,
                timezone_name=args.timezone, proxy_url=args.proxy_url, location_id=args.location_id,
                env_path=args.env_file, netatmo_user_id=args.netatmo_user_id,
                netatmo_device_id=args.netatmo_device_id, token_path=args.token_file, mqtt_broker=args.mqtt_broker)
            write_plan(plan, migration_directory, args.dry_run)
        except ValueError as exc:
            parser.error(str(exc))
        args.config, args.env_file = migration_directory / "weather-proxy.yaml", migration_directory / "weather.env"
    if args.mode == "proxmox":
        command = [sys.executable, str(ROOT / "bin/install_proxmox.py"), "--component", "weather-proxy",
                   "--proxy-config", str(args.config.resolve())]
        for key in ("ctid", "storage", "tag"):
            if getattr(args, key) is not None:
                command.extend([f"--{key}", str(getattr(args, key))])
        if args.env_file:
            command.extend(["--proxy-env-file", str(args.env_file.resolve())])
        if args.dry_run:
            command.append("--dry-run")
        if args.yes:
            command.append("--yes")
        if args.dry_run and args.migrate_calendar:
            print("Would deploy the generated proxy configuration through the Proxmox OCI installer")
            if args.activate_calendar:
                print("Would verify proxy readiness and switch the calendar after deployment")
            return 0
        result = subprocess.run(command, check=False).returncode
        if result == 0 and args.activate_calendar:
            activate(migration_directory, verify_url=args.verify_url, wait_seconds=args.migration_timeout)
        return result
    def run(command):
        print(shlex.join(map(str, command)))
        if not args.dry_run:
            subprocess.run(list(map(str, command)), check=True)
    # Config must use /data in OCI images and the persistent data/ directory in
    # native installs. Validate through the installed runtime before starting.
    for source, filename in ((args.config, "weather-proxy.yaml"), (args.env_file, "weather.env")):
        destination = directory / filename
        if source is None:
            continue
        if destination.exists() and source.resolve() == destination:
            continue
        if destination.exists() and not args.replace_config:
            print(f"Preserving {destination}; use --replace-config to replace explicitly")
            continue
        if destination.exists() and args.replace_config and not args.dry_run:
            backup = destination.with_name(destination.name + ".bak")
            write_file(backup, destination.read_text(), False)
        write_file(destination, "" if args.dry_run else source.read_text(), args.dry_run)
    if args.mode == "systemd":
        # Re-running updates the package but preserves state, config and secrets.
        if args.dry_run or subprocess.run(["id", "weatherproxy"], capture_output=True).returncode:
            run(["useradd", "--system", "--user-group", "--home-dir", directory, "--shell", "/usr/sbin/nologin", "weatherproxy"])
        run(["chmod", "0755", directory])
        run(["python3", "-m", "venv", directory / "venv"])
        run([directory / "venv/bin/pip", "install", "--upgrade", ROOT])
        run(["mkdir", "-p", directory / "data"])
        run(["chown", "weatherproxy:weatherproxy", directory / "data", directory / "weather-proxy.yaml"])
        if args.env_file or (directory / "weather.env").exists():
            run(["chown", "weatherproxy:weatherproxy", directory / "weather.env"])
        run(["runuser", "-u", "weatherproxy", "--", "env",
             f"WEATHER_PROXY_ENV_FILE={directory}/weather.env",
             f"WEATHER_PROXY_STORAGE_PATH={directory}/data/weather-proxy.sqlite3",
             directory / "venv/bin/python3", "-m", "weather_proxy", "check",
             "--config", directory / "weather-proxy.yaml"])
        # Runtime loads secrets as the service user; validate without printing them.
        for command in ("web", "worker"):
            write_file(Path(f"/etc/systemd/system/weather-proxy-{command}.service"),
                       systemd_unit(directory, command), args.dry_run, 0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "weather-proxy-web", "weather-proxy-worker"])
        if args.start:
            run(["systemctl", "restart", "weather-proxy-web", "weather-proxy-worker"])
    else:
        image = "weatherproxy:local" if args.build else args.image
        if args.mode == "oci":
            if args.build:
                run(["podman", "build", "-f", ROOT / "Dockerfile.weatherproxy", "-t", image, ROOT])
            else:
                run(["podman", "pull", image])
            run(["podman", "save", "--format", "oci-archive", "--output", directory / "weather-proxy.oci.tar", image])
            write_file(directory / "mounts.json", json.dumps({"image": image, "config": "/config", "data": "/data", "uid": 10001, "port": 8090}, indent=2), args.dry_run)
        else:
            compose = compose_config(directory, image, args.port, args.build, args.mode)
            write_file(directory / "compose.yaml", json.dumps(compose, indent=2), args.dry_run, 0o644)
            # Bind data under the matching runtime UID; no world-readable secrets.
            if not args.dry_run:
                os.chmod(directory, 0o755)
                (directory / "data").mkdir(mode=0o700, exist_ok=True)
                for filename in ("weather-proxy.yaml", "weather.env"):
                    path = directory / filename
                    if path.exists() and os.geteuid() == 0:
                        os.chown(path, 10001, 10001)
                if os.geteuid() == 0:
                    os.chown(directory / "data", 10001, 10001)
            command = [args.mode, "compose", "-f", directory / "compose.yaml"]
            run([*command, "config", "--quiet"])
            if args.build:
                run([*command, "build"])
            else:
                run([*command, "pull"])
            run([*command, "run", "--rm", "weather-proxy", "check"])
            if args.start:
                run([*command, "up", "-d", "--no-build", "--force-recreate"])
    print(f"WeatherProxy deployment prepared in {directory}")
    if args.activate_calendar:
        if args.dry_run:
            print("Would verify proxy readiness, back up and switch the calendar configuration")
        else:
            activate(migration_directory, verify_url=args.verify_url, wait_seconds=args.migration_timeout)
    elif migration_directory:
        print(f"Calendar unchanged. Activate after deployment with: weather-proxy migrate-calendar --output-dir {migration_directory} --activate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

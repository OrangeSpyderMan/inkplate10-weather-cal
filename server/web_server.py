#!/usr/bin/env python3

import ipaddress
import os
import sys
from pathlib import Path

from configuration import load_config
from output_profiles import export_output_profiles, load_output_profiles


SERVER_DIR = Path(__file__).resolve().parent


def gunicorn_argv(config):
    server_config = config.get("server") or {}
    host = str(server_config.get("host", "0.0.0.0")).strip()
    port = server_config.get("port", 8080)
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            f"server.host must be an IPv4 or IPv6 address, got {host!r}"
        ) from exc
    bind_host = f"[{host}]" if address.version == 6 else host
    workers = int(os.environ.get("INKPLATE_WEB_WORKERS", "1"))
    threads = int(os.environ.get("INKPLATE_WEB_THREADS", "2"))
    return [
        sys.executable,
        "-m",
        "gunicorn",
        "--chdir",
        str(SERVER_DIR),
        "--bind",
        f"{bind_host}:{port}",
        "--workers",
        str(workers),
        "--threads",
        str(threads),
        "--access-logfile",
        "-",
        "--error-logfile",
        "-",
        "web:app",
    ]


def main():
    _, config = load_config()
    if not (config.get("server") or {}).get("enabled", True):
        return 0

    profiles, default_profile = load_output_profiles(config)
    export_output_profiles(profiles, default_profile)
    argv = gunicorn_argv(config)
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    main()

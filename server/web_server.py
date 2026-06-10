#!/usr/bin/env python3

import os
import sys
from pathlib import Path

from configuration import load_config
from output_profiles import export_output_profiles, load_output_profiles


SERVER_DIR = Path(__file__).resolve().parent


def gunicorn_argv(config):
    port = (config.get("server") or {}).get("port", 8080)
    workers = int(os.environ.get("INKPLATE_WEB_WORKERS", "1"))
    threads = int(os.environ.get("INKPLATE_WEB_THREADS", "2"))
    return [
        sys.executable,
        "-m",
        "gunicorn",
        "--chdir",
        str(SERVER_DIR),
        "--bind",
        f"0.0.0.0:{port}",
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

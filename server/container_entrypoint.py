#!/usr/bin/env python3

import os
import re
import signal
import subprocess
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parent
ENV_FILE = SERVER_DIR / "config" / "weather.env"


def load_environment(path=ENV_FILE):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = decode_env_value(value.strip())
        os.environ.setdefault(key, value)


def decode_env_value(value):
    if len(value) < 2 or value[0] != value[-1]:
        return value
    if value[0] == "'":
        return value[1:-1]
    if value[0] != '"':
        return value
    return re.sub(r"\\([\\\"$])", r"\1", value[1:-1])


def terminate(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()


def main():
    load_environment()
    producer = subprocess.Popen([sys.executable, str(SERVER_DIR / "server.py")])
    web = subprocess.Popen([sys.executable, str(SERVER_DIR / "web_server.py")])
    diagnostics = subprocess.Popen(
        [sys.executable, str(SERVER_DIR / "mqtt_diagnostics_server.py")]
    )
    processes = [producer, web, diagnostics]

    def stop(signum, frame):
        terminate(processes)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        web_status = web.poll()
        producer_status = producer.poll()
        diagnostics_status = diagnostics.poll()
        if web_status is not None:
            terminate(processes)
            return web_status
        if producer_status is not None:
            if producer_status != 0:
                terminate(processes)
                return producer_status
            return web.wait()
        if diagnostics_status not in (None, 0):
            terminate(processes)
            return diagnostics_status
        try:
            producer.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

import signal
import subprocess
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parent


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
    producer = subprocess.Popen([sys.executable, str(SERVER_DIR / "server.py")])
    web = subprocess.Popen([sys.executable, str(SERVER_DIR / "web_server.py")])
    processes = [producer, web]

    def stop(signum, frame):
        terminate(processes)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        web_status = web.poll()
        producer_status = producer.poll()
        if web_status is not None:
            terminate(processes)
            return web_status
        if producer_status is not None:
            if producer_status != 0:
                terminate(processes)
                return producer_status
            return web.wait()
        try:
            producer.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

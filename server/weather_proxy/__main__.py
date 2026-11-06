import argparse
import logging
import os
import signal
import subprocess
import sys
import threading

from .config import load_config, load_environment
from .storage import open_storage
from .worker import Worker


def main():
    if sys.argv[1:2] == ["migrate-calendar"]:
        from .migrate import main as migrate_calendar
        return migrate_calendar(sys.argv[2:])
    parser = argparse.ArgumentParser(description="WeatherProxy forecast and observation service")
    parser.epilog = "Prepare or activate a calendar migration with: weather-proxy migrate-calendar --help"
    parser.add_argument("command", choices=("serve", "web", "worker", "check", "webhooks", "register-webhook"), nargs="?", default="serve")
    parser.add_argument("--config")
    parser.add_argument("--account")
    parser.add_argument("--url")
    args = parser.parse_args()
    load_environment(os.environ.get("WEATHER_PROXY_ENV_FILE", "/config/weather.env"))
    config = load_config(args.config)
    if args.command == "check":
        print(f"Valid configuration: {len(config['locations'])} locations")
        return 0
    if args.command == "web":
        if args.config:
            os.environ["WEATHER_PROXY_CONFIG"] = os.path.abspath(args.config)
        server = config.get("server", {})
        host = server.get("host", "0.0.0.0")
        host = f"[{host}]" if ":" in host else host
        argv = [sys.executable, "-m", "gunicorn", "--bind", f"{host}:{server.get('port', 8090)}",
                "--workers", str(server.get("workers", 1)), "--threads", str(server.get("threads", 4)),
                "--access-logfile", "-", "weather_proxy.api:create_app()"]
        os.execv(sys.executable, argv)
    if args.command == "serve":
        children = [subprocess.Popen([sys.executable, "-m", "weather_proxy", command,
                                     *(["--config", args.config] if args.config else [])])
                    for command in ("web", "worker")]
        stopping = threading.Event()
        def stop(*unused):
            stopping.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        result = 0
        try:
            while not stopping.wait(.5):
                exited = [child.poll() for child in children if child.poll() is not None]
                if exited:
                    result = exited[0] or 1
                    break
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        return result
    store = open_storage(config.get("storage", {}))
    if args.command in ("webhooks", "register-webhook"):
        if args.account not in config["accounts"] or config["accounts"][args.account]["provider"] != "netatmo":
            parser.error("--account must name a Netatmo account")
        if args.command == "register-webhook" and (not args.url or not args.url.startswith("https://")):
            parser.error("--url must be a public HTTPS callback")
        from weather.http import request_budget
        worker = Worker(config, store)
        if not store.lease("worker", worker.owner, worker.clock(), 300):
            parser.error("Stop the worker before changing registration or refreshing tokens")
        try:
            with request_budget(lambda: worker.guard(args.account)):
                provider = worker.netatmo(args.account)
                existing = provider.webhooks()
                if args.command == "webhooks":
                    print(existing)
                else:
                    if not any(item.get("url") == args.url for item in existing.get("body", [])):
                        provider.webhooks(args.url)
                    print("Webhook registered")
        finally:
            worker.close()
        return 0
    from .mqtt import Publisher
    publisher = Publisher(config["mqtt"]) if config.get("mqtt", {}).get("enabled") else None
    worker = Worker(config, store, publisher)
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *unused: stopping.set())
    signal.signal(signal.SIGINT, lambda *unused: stopping.set())
    logging.basicConfig(level=logging.INFO)
    try:
        while not stopping.is_set():
            worker.tick()
            stopping.wait(2)
    finally:
        worker.close()
        if publisher:
            publisher.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

import logging
import logging.config
import os
import signal
import threading
from pathlib import Path

from artifacts import ArtifactStore
from configuration import load_config
from mqtt_diagnostics import MqttDiagnosticListener
from mqtt_identity import mqtt_client_id


SERVER_DIR = Path(__file__).resolve().parent


def configure_logging():
    path = os.environ.get(
        "INKPLATE_LOG_CONFIG",
        str(SERVER_DIR / "logging.ini"),
    )
    logging.config.fileConfig(path)
    return logging.getLogger("server")


def build_listener(config, store=None):
    mqtt_config = config.get("mqtt") or {}
    diagnostics_config = mqtt_config.get("diagnostics") or {}
    if not diagnostics_config.get("enabled", False):
        return None

    return MqttDiagnosticListener(
        broker=diagnostics_config.get("broker", "localhost"),
        port=diagnostics_config.get("port", 1883),
        topic=diagnostics_config.get(
            "topic",
            "inkplate/weather-calendar/diagnostics",
        ),
        qos=diagnostics_config.get("qos", 0),
        client_id=mqtt_client_id(
            "diagnostics",
            mqtt_config.get("instance_id"),
        ),
        store=store,
    )


def main():
    log = configure_logging()
    config_path, config = load_config()
    store = ArtifactStore(
        os.environ.get("INKPLATE_DATA_DIR", SERVER_DIR / "data")
    )
    listener = build_listener(config, store=store)
    if listener is None:
        log.info(
            "MQTT diagnostic listener is disabled in %s",
            config_path,
        )
        return 0

    if not listener.start():
        return 1

    stopped = threading.Event()

    def stop(signum, frame):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        stopped.wait()
    finally:
        listener.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

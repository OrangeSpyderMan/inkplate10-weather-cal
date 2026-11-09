"""Persistent MQTT connection; latest complete state is replayed on reconnect."""

import json
import threading

import paho.mqtt.client as mqtt


class Publisher:
    def __init__(self, config):
        self.root = config.get("base_topic", "weatherproxy").rstrip("/")
        self.qos = int(config.get("qos", 1))
        self.connected = threading.Event()
        self.generation = 0
        self.sent = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=config.get("client_id", "weatherproxy"))
        if config.get("username"):
            self.client.username_pw_set(config["username"], config.get("password"))
        if config.get("tls"):
            self.client.tls_set()
        self.client.will_set(f"{self.root}/availability", "offline", qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = lambda *args: self.connected.clear()
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.connect_async(config.get("broker", "localhost"), int(config.get("port", 1883)), 60)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, reason, properties):
        if reason == 0:
            self.generation += 1
            self.connected.set()
            client.publish(f"{self.root}/availability", "online", qos=1, retain=True)

    def publish(self, name, payload):
        if not self.connected.is_set():
            return False
        version = (payload["revision"], self.generation)
        if self.sent.get(name) == version:
            return True
        root = f"{self.root}/locations/{name}"
        current = {key: payload[key] for key in ("schema_version", "location", "revision", "generated_at", "units", "current", "measurement_metadata", "forecast")}
        messages = {"weather": payload, "current": current, "observations": {
            key: payload[key] for key in ("location", "revision", "generated_at", "units", "observations")},
            "status": {"revision": payload["revision"], "forecast": payload["forecast"]}}
        try:
            for suffix, value in messages.items():
                result = self.client.publish(f"{root}/{suffix}", json.dumps(value, allow_nan=False), qos=self.qos, retain=True)
                result.wait_for_publish(timeout=5)
                if result.rc != mqtt.MQTT_ERR_SUCCESS or not result.is_published():
                    return False
        except (RuntimeError, ValueError):
            return False
        self.sent[name] = version
        return True

    def remove(self, name):
        if not self.connected.is_set():
            return False
        try:
            for suffix in ("weather", "current", "observations", "status"):
                result = self.client.publish(f"{self.root}/locations/{name}/{suffix}", b"", qos=1, retain=True)
                result.wait_for_publish(timeout=5)
                if not result.is_published():
                    return False
        except (RuntimeError, ValueError):
            return False
        return True

    def close(self):
        if self.connected.is_set():
            result = self.client.publish(f"{self.root}/availability", "offline", qos=1, retain=True)
            result.wait_for_publish(timeout=5)
        self.client.disconnect()
        self.client.loop_stop()

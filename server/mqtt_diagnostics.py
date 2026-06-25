import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from redaction import exception_text


DIAGNOSTIC_SCHEMA_VERSION = "1.0"
MAX_DIAGNOSTIC_MESSAGE_LENGTH = 4096


class MqttDiagnosticListener:
    def __init__(
        self,
        broker,
        port=1883,
        topic="inkplate/weather-calendar/diagnostics",
        qos=0,
        client_id="inkplate-diagnostics-server",
        store=None,
        now=None,
    ):
        self.broker = broker
        self.port = port
        self.topic = topic
        self.qos = qos
        self.client_id = client_id
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.log = logging.getLogger("server")
        self.client_log = logging.getLogger("MQTT")
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
        )
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self):
        try:
            self.client.connect_async(self.broker, self.port, 60)
            self.client.loop_start()
        except Exception as exc:
            self.log.error(
                "Failed to start MQTT diagnostic listener: %s",
                exception_text(exc),
            )
            return False

        return True

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self.log.error(
                "Connection to MQTT diagnostic broker failed: %s", reason_code
            )
            return

        result, _ = client.subscribe(self.topic, qos=self.qos)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self.log.error(
                "Failed to subscribe to MQTT diagnostic topic %s: %s",
                self.topic,
                mqtt.error_string(result),
            )
            return

        self.log.info("Listening for Inkplate diagnostics on %s", self.topic)

    def _on_connect_fail(self, client, userdata):
        self.log.warning(
            "Unable to connect to MQTT diagnostic broker %s:%s; retrying",
            self.broker,
            self.port,
        )

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ):
        if reason_code != 0:
            self.log.warning(
                "Unexpected MQTT diagnostic broker disconnection: %s", reason_code
            )
        else:
            self.log.info("Disconnected from MQTT diagnostic broker")

    def _on_message(self, client, userdata, message):
        if message.retain:
            return

        value = message.payload.decode(errors="replace")
        self.client_log.info(value)
        if self.store is None:
            return

        try:
            self.store.write_diagnostic(
                {
                    "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
                    "received_at": self.now().isoformat(),
                    "topic": getattr(message, "topic", self.topic),
                    "message": value[:MAX_DIAGNOSTIC_MESSAGE_LENGTH],
                    "truncated": len(value)
                    > MAX_DIAGNOSTIC_MESSAGE_LENGTH,
                }
            )
        except OSError as exc:
            self.log.error(
                "Failed to persist Inkplate diagnostic: %s",
                exception_text(exc),
            )

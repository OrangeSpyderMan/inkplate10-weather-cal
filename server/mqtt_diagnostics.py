import logging

import paho.mqtt.client as mqtt


class MqttDiagnosticListener:
    def __init__(
        self,
        host,
        port=1883,
        topic="inkplate/weather-calendar/diagnostics",
        qos=0,
        client_id="inkplate-diagnostics-server",
    ):
        self.host = host
        self.port = port
        self.topic = topic
        self.qos = qos
        self.client_id = client_id
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
            self.client.connect_async(self.host, self.port, 60)
            self.client.loop_start()
        except Exception as exc:
            self.log.error("Failed to start MQTT diagnostic listener: %s", exc)
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
            self.host,
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

        self.client_log.info(message.payload.decode(errors="replace"))

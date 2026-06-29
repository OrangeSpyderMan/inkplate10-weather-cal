import json
import logging

import paho.mqtt.client as mqtt

from redaction import exception_text


def create_mqtt_client(client_id):
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )


class MqttWeatherPublisher:
    def __init__(
        self,
        broker,
        port=1883,
        base_topic="inkplate/weather",
        retain=True,
        qos=0,
        client_id="inkplate-weather-server",
    ):
        self.broker = broker
        self.port = port
        self.base_topic = base_topic.rstrip("/")
        self.retain = retain
        self.qos = qos
        self.client_id = client_id
        self.log = logging.getLogger("server")

    def publish_snapshot(self, snapshot):
        payload = snapshot.to_payload()
        current = payload["current"]
        messages = [
            (self.base_topic, payload),
            (f"{self.base_topic}/generated_at", payload["generated_at"]),
            (f"{self.base_topic}/current", current),
            (f"{self.base_topic}/hourly", payload["hourly"]),
            (
                f"{self.base_topic}/status",
                {
                "generated_at": payload["generated_at"],
                "source": payload["source"],
                "units": payload["units"],
            },
            ),
            (f"{self.base_topic}/current/rain", current.get("rain")),
            (f"{self.base_topic}/current/wind", current.get("wind")),
        ]
        return self.publish_messages(messages, "weather snapshot")

    def publish_server_status(self, status):
        return self.publish_messages(
            [(f"{self.base_topic}/server/status", status)],
            "server status",
        )

    def publish_messages(self, messages, description="messages"):
        client = create_mqtt_client(self.client_id)
        loop_started = False
        try:
            client.connect(self.broker, self.port, 60)
            client.loop_start()
            loop_started = True
            self.log.info(
                "Publishing MQTT %s to broker %s:%s under %s",
                description,
                self.broker,
                self.port,
                self.base_topic,
            )
            for topic, value in messages:
                encoded = (
                    ""
                    if value is None
                    else json.dumps(value, default=str)
                )
                result = client.publish(
                    topic,
                    encoded,
                    qos=self.qos,
                    retain=self.retain,
                )
                result.wait_for_publish(timeout=5)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(
                        "publish to {} failed: {}".format(
                            topic,
                            mqtt.error_string(result.rc),
                        )
                    )
            return {"success": True, "error": None}
        except Exception as exc:
            error = exception_text(exc)
            self.log.error("Failed to publish MQTT messages: %s", error)
            return {"success": False, "error": error}
        finally:
            if loop_started:
                client.loop_stop()
                client.disconnect()

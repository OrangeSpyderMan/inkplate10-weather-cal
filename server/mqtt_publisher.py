import json
import logging

import paho.mqtt.client as mqtt


def create_mqtt_client(client_id):
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )


class MqttWeatherPublisher:
    def __init__(
        self,
        host,
        port=1883,
        base_topic="inkplate/weather",
        retain=True,
        qos=0,
        client_id="inkplate-weather-server",
    ):
        self.host = host
        self.port = port
        self.base_topic = base_topic.rstrip("/")
        self.retain = retain
        self.qos = qos
        self.client_id = client_id
        self.log = logging.getLogger("server")

    def publish_snapshot(self, snapshot):
        payload = snapshot.to_payload()
        messages = {
            self.base_topic: payload,
            f"{self.base_topic}/current": payload["current"],
            f"{self.base_topic}/hourly": payload["hourly"],
            f"{self.base_topic}/status": {
                "generated_at": payload["generated_at"],
                "source": payload["source"],
                "units": payload["units"],
            },
        }

        client = create_mqtt_client(self.client_id)
        try:
            client.connect(self.host, self.port, 60)
            client.loop_start()
            for topic, value in messages.items():
                result = client.publish(
                    topic,
                    json.dumps(value, default=str),
                    qos=self.qos,
                    retain=self.retain,
                )
                result.wait_for_publish(timeout=5)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    self.log.error(
                        "Failed to publish weather snapshot to MQTT topic %s: %s",
                        topic,
                        mqtt.error_string(result.rc),
                    )
        except Exception as exc:
            self.log.error("Failed to publish weather snapshot to MQTT: %s", exc)
        finally:
            client.loop_stop()
            client.disconnect()

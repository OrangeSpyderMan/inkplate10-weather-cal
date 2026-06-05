import logging
import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import mqtt_diagnostics
import mqtt_publisher


class FakeMessage:
    def __init__(self, payload, retain=False):
        self.payload = payload
        self.retain = retain


class MqttDiagnosticListenerTests(unittest.TestCase):
    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_uses_callback_api_v2(self, client_class):
        mqtt_diagnostics.MqttDiagnosticListener("broker")

        client_class.assert_called_once_with(
            callback_api_version=mqtt_diagnostics.mqtt.CallbackAPIVersion.VERSION2,
            client_id="inkplate-diagnostics-server",
        )

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_subscribes_after_each_successful_connection(self, client_class):
        client = client_class.return_value
        client.subscribe.return_value = (
            mqtt_diagnostics.mqtt.MQTT_ERR_SUCCESS,
            1,
        )
        listener = mqtt_diagnostics.MqttDiagnosticListener(
            "broker", topic="inkplate/diagnostics", qos=1
        )

        listener._on_connect(client, None, None, 0, None)
        listener._on_connect(client, None, None, 0, None)

        self.assertEqual(
            client.subscribe.call_args_list,
            [
                mock.call("inkplate/diagnostics", qos=1),
                mock.call("inkplate/diagnostics", qos=1),
            ],
        )

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_ignores_retained_messages(self, client_class):
        listener = mqtt_diagnostics.MqttDiagnosticListener("broker")
        listener.client_log = mock.Mock()

        listener._on_message(
            client_class.return_value,
            None,
            FakeMessage(b"stale", retain=True),
        )
        listener._on_message(
            client_class.return_value,
            None,
            FakeMessage(b"current"),
        )

        listener.client_log.info.assert_called_once_with("current")

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_start_failure_is_non_fatal(self, client_class):
        client_class.return_value.connect_async.side_effect = OSError("invalid")
        listener = mqtt_diagnostics.MqttDiagnosticListener("broker")

        with self.assertLogs("server", logging.ERROR):
            self.assertFalse(listener.start())

        client_class.return_value.loop_start.assert_not_called()

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_starts_async_for_initial_connection_retries(self, client_class):
        listener = mqtt_diagnostics.MqttDiagnosticListener("broker", port=1884)

        self.assertTrue(listener.start())

        client_class.return_value.connect_async.assert_called_once_with(
            "broker", 1884, 60
        )
        client_class.return_value.loop_start.assert_called_once_with()


class MqttWeatherPublisherTests(unittest.TestCase):
    @mock.patch("mqtt_publisher.mqtt.Client")
    def test_uses_callback_api_v2(self, client_class):
        mqtt_publisher.create_mqtt_client("weather-client")

        client_class.assert_called_once_with(
            callback_api_version=mqtt_publisher.mqtt.CallbackAPIVersion.VERSION2,
            client_id="weather-client",
        )

    @mock.patch("mqtt_publisher.create_mqtt_client")
    def test_publishes_all_topics_with_paho_2_timeout(self, create_client):
        client = create_client.return_value
        result = mock.Mock(rc=mqtt_publisher.mqtt.MQTT_ERR_SUCCESS)
        client.publish.return_value = result
        snapshot = mock.Mock()
        snapshot.to_payload.return_value = {
            "generated_at": "2026-06-05T00:00:00+00:00",
            "source": "test",
            "units": "metric",
            "current": {},
            "hourly": [],
        }

        mqtt_publisher.MqttWeatherPublisher("broker").publish_snapshot(snapshot)

        self.assertEqual(client.publish.call_count, 4)
        self.assertEqual(
            result.wait_for_publish.call_args_list,
            [mock.call(timeout=5)] * 4,
        )
        client.loop_stop.assert_called_once_with()
        client.disconnect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

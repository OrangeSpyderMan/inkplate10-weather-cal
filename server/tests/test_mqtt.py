import logging
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import mqtt_diagnostics
import mqtt_diagnostics_server
import mqtt_publisher


class FakeMessage:
    def __init__(self, payload, retain=False, topic="inkplate/diagnostics"):
        self.payload = payload
        self.retain = retain
        self.topic = topic


class MqttDiagnosticListenerTests(unittest.TestCase):
    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_uses_callback_api_v2(self, client_class):
        mqtt_diagnostics.MqttDiagnosticListener(broker="broker")

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
            broker="broker", topic="inkplate/diagnostics", qos=1
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
        store = mock.Mock()
        listener = mqtt_diagnostics.MqttDiagnosticListener(
            broker="broker",
            store=store,
            now=lambda: dt.datetime(
                2026,
                6,
                25,
                12,
                tzinfo=dt.timezone.utc,
            ),
        )
        self.assertEqual(listener.client_log.name, "MQTT")
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
        store.append_diagnostic.assert_called_once_with(
            {
                "schema_version": "1.0",
                "received_at": "2026-06-25T12:00:00+00:00",
                "topic": "inkplate/diagnostics",
                "message": "current",
                "truncated": False,
            },
            limit=mqtt_diagnostics.MAX_RECENT_DIAGNOSTICS,
        )

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_caps_persisted_diagnostic_message(self, client_class):
        store = mock.Mock()
        listener = mqtt_diagnostics.MqttDiagnosticListener(
            broker="broker",
            store=store,
        )
        payload = b"x" * (
            mqtt_diagnostics.MAX_DIAGNOSTIC_MESSAGE_LENGTH + 1
        )

        listener._on_message(
            client_class.return_value,
            None,
            FakeMessage(payload),
        )

        diagnostic = store.append_diagnostic.call_args.args[0]
        self.assertEqual(
            len(diagnostic["message"]),
            mqtt_diagnostics.MAX_DIAGNOSTIC_MESSAGE_LENGTH,
        )
        self.assertTrue(diagnostic["truncated"])

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_persistence_failure_does_not_escape_callback(self, client_class):
        store = mock.Mock()
        store.append_diagnostic.side_effect = OSError("read-only")
        listener = mqtt_diagnostics.MqttDiagnosticListener(
            broker="broker",
            store=store,
        )

        with self.assertLogs("server", logging.ERROR) as logs:
            listener._on_message(
                client_class.return_value,
                None,
                FakeMessage(b"current"),
            )

        self.assertIn(
            "Failed to persist Inkplate diagnostic",
            logs.output[0],
        )

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_start_failure_is_non_fatal(self, client_class):
        client_class.return_value.connect_async.side_effect = OSError("invalid")
        listener = mqtt_diagnostics.MqttDiagnosticListener(broker="broker")

        with self.assertLogs("server", logging.ERROR):
            self.assertFalse(listener.start())

        client_class.return_value.loop_start.assert_not_called()

    @mock.patch("mqtt_diagnostics.mqtt.Client")
    def test_starts_async_for_initial_connection_retries(self, client_class):
        listener = mqtt_diagnostics.MqttDiagnosticListener(
            broker="broker", port=1884
        )

        self.assertTrue(listener.start())

        client_class.return_value.connect_async.assert_called_once_with(
            "broker", 1884, 60
        )
        client_class.return_value.loop_start.assert_called_once_with()


class MqttDiagnosticServerTests(unittest.TestCase):
    def test_disabled_listener_returns_none(self):
        self.assertIsNone(
            mqtt_diagnostics_server.build_listener(
                {"mqtt": {"diagnostics": {"enabled": False}}}
            )
        )

    @mock.patch("mqtt_diagnostics_server.MqttDiagnosticListener")
    def test_builds_enabled_listener_from_config(self, listener_class):
        store = mock.Mock()
        listener = mqtt_diagnostics_server.build_listener(
            {
                "mqtt": {
                    "diagnostics": {
                        "enabled": True,
                        "broker": "broker",
                        "port": 1884,
                        "topic": "inkplate/diagnostics",
                        "qos": 1,
                    }
                }
            },
            store=store,
        )

        self.assertEqual(listener, listener_class.return_value)
        listener_class.assert_called_once_with(
            broker="broker",
            port=1884,
            topic="inkplate/diagnostics",
            qos=1,
            store=store,
        )

    @mock.patch("mqtt_diagnostics_server.load_config")
    @mock.patch("mqtt_diagnostics_server.configure_logging")
    def test_disabled_server_exits_successfully(
        self,
        configure_logging,
        load_config,
    ):
        configure_logging.return_value = mock.Mock()
        load_config.return_value = (
            "/config.yaml",
            {"mqtt": {"diagnostics": {"enabled": False}}},
        )

        self.assertEqual(mqtt_diagnostics_server.main(), 0)

    @mock.patch("mqtt_diagnostics_server.load_config")
    @mock.patch("mqtt_diagnostics_server.configure_logging")
    @mock.patch("mqtt_diagnostics_server.build_listener")
    def test_listener_start_failure_exits_nonzero(
        self,
        build_listener,
        configure_logging,
        load_config,
    ):
        configure_logging.return_value = mock.Mock()
        load_config.return_value = ("/config.yaml", {})
        build_listener.return_value.start.return_value = False

        self.assertEqual(mqtt_diagnostics_server.main(), 1)


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
            "schema_version": "2.0",
            "generated_at": "2026-06-05T00:00:00+00:00",
            "source": "test",
            "units": "metric",
            "current": {},
            "hourly": [],
        }

        publisher = mqtt_publisher.MqttWeatherPublisher(
            broker="broker", port=1884, base_topic="inkplate/weather"
        )
        publisher.log = mock.Mock()
        publisher.publish_snapshot(snapshot)

        self.assertEqual(client.publish.call_count, 7)
        publisher.log.info.assert_called_once_with(
            "Publishing MQTT %s to broker %s:%s under %s",
            "weather snapshot",
            "broker",
            1884,
            "inkplate/weather",
        )
        self.assertEqual(
            result.wait_for_publish.call_args_list,
            [mock.call(timeout=5)] * 7,
        )
        self.assertEqual(
            [call.args[0] for call in client.publish.call_args_list],
            [
                "inkplate/weather",
                "inkplate/weather/generated_at",
                "inkplate/weather/current",
                "inkplate/weather/hourly",
                "inkplate/weather/status",
                "inkplate/weather/current/rain",
                "inkplate/weather/current/wind",
            ],
        )
        self.assertEqual(client.publish.call_args_list[-2].args[1], "")
        self.assertEqual(client.publish.call_args_list[-1].args[1], "")
        client.loop_stop.assert_called_once_with()
        client.disconnect.assert_called_once_with()

    @mock.patch("mqtt_publisher.create_mqtt_client")
    def test_publishes_dedicated_current_measurements(self, create_client):
        client = create_client.return_value
        result = mock.Mock(rc=mqtt_publisher.mqtt.MQTT_ERR_SUCCESS)
        client.publish.return_value = result
        snapshot = mock.Mock()
        snapshot.to_payload.return_value = {
            "schema_version": "2.0",
            "generated_at": "2026-06-05T00:00:00+00:00",
            "source": "test",
            "units": "metric",
            "current": {
                "rain": {"value": 0.4, "rate_unit": "mm/h"},
                "wind": {"value": 18, "unit": "kmh"},
            },
            "hourly": [],
        }

        publisher = mqtt_publisher.MqttWeatherPublisher(broker="broker")
        publish_result = publisher.publish_snapshot(snapshot)

        self.assertTrue(publish_result["success"])
        rain_call = client.publish.call_args_list[-2]
        wind_call = client.publish.call_args_list[-1]
        self.assertIn('"value": 0.4', rain_call.args[1])
        self.assertIn('"value": 18', wind_call.args[1])
        self.assertTrue(rain_call.kwargs["retain"])
        self.assertTrue(wind_call.kwargs["retain"])

    @mock.patch("mqtt_publisher.create_mqtt_client")
    def test_publish_failure_is_nonfatal_and_sanitized(self, create_client):
        create_client.return_value.connect.side_effect = OSError("offline")
        publisher = mqtt_publisher.MqttWeatherPublisher(broker="broker")

        result = publisher.publish_server_status({"producer": {"state": "ready"}})

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "OSError: offline")
        create_client.return_value.loop_start.assert_not_called()
        create_client.return_value.disconnect.assert_not_called()

    @mock.patch("mqtt_publisher.create_mqtt_client")
    def test_publishes_retained_server_status_topic(self, create_client):
        client = create_client.return_value
        client.publish.return_value = mock.Mock(
            rc=mqtt_publisher.mqtt.MQTT_ERR_SUCCESS
        )
        publisher = mqtt_publisher.MqttWeatherPublisher(
            broker="broker",
            base_topic="inkplate/weather-calendar",
        )
        publisher.log = mock.Mock()

        result = publisher.publish_server_status(
            {"schema_version": "1.0", "producer": {"state": "ready"}}
        )

        self.assertTrue(result["success"])
        publisher.log.info.assert_called_once_with(
            "Publishing MQTT %s to broker %s:%s under %s",
            "server status",
            "broker",
            1883,
            "inkplate/weather-calendar",
        )
        call = client.publish.call_args
        self.assertEqual(
            call.args[0],
            "inkplate/weather-calendar/server/status",
        )
        self.assertIn('"state": "ready"', call.args[1])
        self.assertTrue(call.kwargs["retain"])


if __name__ == "__main__":
    unittest.main()

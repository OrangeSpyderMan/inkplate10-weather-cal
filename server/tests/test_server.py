import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import server
from artifacts import ArtifactStore
from output_profiles import OutputProfile
from weather.models import CurrentConditions, Temperature
from weather.snapshot import WeatherSnapshot


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.log = mock.patch.object(server, "log", mock.Mock())
        self.log.start()

    def tearDown(self):
        self.log.stop()

    @mock.patch("server.logging.config.fileConfig")
    @mock.patch("server.logging.getLogger")
    def test_service_logging_config_overrides_local_profiles(
        self,
        get_logger,
        file_config,
    ):
        with mock.patch.dict(
            server.os.environ,
            {"INKPLATE_LOG_CONFIG": "/tmp/logging.service.ini"},
        ):
            configured_logger = server.configure_logging(debug=True)

        file_config.assert_called_once_with("/tmp/logging.service.ini")
        self.assertEqual(configured_logger, get_logger.return_value)

    @mock.patch("server.logging.config.fileConfig")
    def test_debug_logging_uses_console_only_development_profile(self, file_config):
        with mock.patch.dict(server.os.environ, {}, clear=True):
            server.configure_logging(debug=True)

        file_config.assert_called_once_with(
            str(SERVER_DIR / "logging.dev.ini"),
        )

    @mock.patch("server.ProducerConfig.from_config")
    @mock.patch("server.configure_logging")
    @mock.patch("server.load_config")
    def test_disabled_producer_exits_before_loading_provider_configuration(
        self,
        load_config,
        configure_logging,
        from_config,
    ):
        load_config.return_value = (
            "/config.yaml",
            {"server": {"enabled": False}},
        )
        configure_logging.return_value = mock.Mock()

        self.assertEqual(server.run(), 0)

        from_config.assert_not_called()

    def test_rejects_unregistered_renderer_before_production(self):
        profiles = {
            "future": OutputProfile(
                "future",
                "unknown",
                800,
                600,
            )
        }

        with self.assertRaisesRegex(ValueError, "unsupported output renderer"):
            server.build_output_renderers(profiles)

    def test_rejects_removed_firefox_renderer(self):
        profiles = {
            "portrait": OutputProfile(
                "portrait",
                "firefox",
                825,
                1200,
            )
        }

        with self.assertRaisesRegex(ValueError, "removed in v4"):
            server.build_output_renderers(profiles)

    @mock.patch("server.MqttWeatherPublisher")
    def test_weather_publisher_uses_shared_mqtt_instance_id(self, publisher):
        server.build_mqtt_weather_publisher(
            {
                "enabled": True,
                "instance_id": "4f9k2m",
            }
        )

        self.assertEqual(
            publisher.call_args.kwargs["client_id"],
            "inkplate-weather.4f9k2m",
        )

    def test_renders_each_profile_with_dimensions_and_options(self):
        portrait_renderer = mock.Mock()
        landscape_renderer = mock.Mock()
        snapshot = mock.Mock()
        profiles = {
            "portrait": OutputProfile(
                "portrait",
                "pillow",
                825,
                1200,
                options={"layout": "classic"},
            ),
            "landscape": OutputProfile(
                "landscape",
                "pillow",
                800,
                600,
                filename="weather.png",
                options={"layout": "compact"},
            ),
        }
        renderers = {
            "portrait": portrait_renderer,
            "landscape": landscape_renderer,
        }

        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            server.render_outputs(
                snapshot,
                "map.png",
                profiles,
                renderers,
                store,
            )

        portrait_renderer.render.assert_called_once_with(
            snapshot,
            "map.png",
            mock.ANY,
            825,
            1200,
            {"layout": "classic"},
        )
        landscape_renderer.render.assert_called_once_with(
            snapshot,
            "map.png",
            mock.ANY,
            800,
            600,
            {"layout": "compact"},
        )

    def test_realtime_failure_preserves_forecast_conditions(self):
        daily_summary = CurrentConditions(
            temperature=Temperature(
                unit="\N{DEGREE SIGN}C",
                value=10,
                minimum=5,
                maximum=15,
            )
        )
        realtime = mock.Mock()
        realtime.get_current_conditions.side_effect = RuntimeError("offline")

        result = server.apply_current_conditions(daily_summary, realtime)

        self.assertIs(result, daily_summary)
        self.assertEqual(result.temperature.value, 10)
        server.log.warning.assert_called_once_with(
            "Realtime conditions unavailable; using forecast conditions: %s",
            mock.ANY,
        )

    @mock.patch("server.render_outputs")
    @mock.patch("server.publish_weather_snapshot")
    @mock.patch("server.build_weather_snapshot")
    def test_completed_cycle_publishes_artifacts_and_readiness_last(
        self,
        build_snapshot,
        publish_snapshot,
        render_outputs,
    ):
        snapshot = WeatherSnapshot(
            daily_summary={},
            hourly_forecasts=[],
            weather_source="test",
            generated_at=dt.datetime(2026, 6, 9, tzinfo=dt.timezone.utc),
        )
        build_snapshot.return_value = snapshot

        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            profiles = {
                "inkplate10-portrait": OutputProfile(
                    "inkplate10-portrait",
                    "pillow",
                    825,
                    1200,
                )
            }
            renderers = {"inkplate10-portrait": mock.Mock()}
            output = store.output_path("inkplate10-portrait", "calendar.png")
            status = mock.Mock()

            def render(*args):
                output.parent.mkdir(parents=True)
                output.write_bytes(b"png")

            render_outputs.side_effect = render

            success = server.produce_artifacts(
                mock.Mock(),
                None,
                "test",
                True,
                mock.Mock(),
                "map.png",
                profiles,
                renderers,
                store,
                status=status,
                success_state="ready",
                next_refresh_seconds=900,
            )

            self.assertTrue(success)
            self.assertTrue(store.snapshot_path.is_file())
            self.assertTrue(store.ready_path.is_file())
            self.assertTrue(store.producer_cycle_complete(profiles))
            publish_snapshot.assert_called_once()
            self.assertEqual(status.transition.call_count, 2)
            self.assertEqual(
                status.transition.call_args_list[0].args[0],
                "refreshing",
            )
            self.assertEqual(
                status.transition.call_args_list[1].args[0],
                "ready",
            )
            completed_transition = status.transition.call_args_list[1]
            self.assertEqual(
                completed_transition.kwargs["next_refresh_at"]
                - completed_transition.kwargs["success_at"],
                dt.timedelta(seconds=900),
            )

    @mock.patch("server.render_outputs", side_effect=RuntimeError("render"))
    @mock.patch("server.publish_weather_snapshot")
    @mock.patch("server.build_weather_snapshot")
    def test_failed_render_does_not_publish_snapshot_or_readiness(
        self,
        build_snapshot,
        publish_snapshot,
        render_outputs,
    ):
        build_snapshot.return_value = WeatherSnapshot({}, [], "test")

        with tempfile.TemporaryDirectory() as temporary_dir:
            store = ArtifactStore(temporary_dir)
            profiles = {
                "inkplate10-portrait": OutputProfile(
                    "inkplate10-portrait",
                    "pillow",
                    825,
                    1200,
                )
            }
            renderers = {"inkplate10-portrait": mock.Mock()}
            status = mock.Mock()

            success = server.produce_artifacts(
                mock.Mock(),
                None,
                "test",
                True,
                mock.Mock(),
                "map.png",
                profiles,
                renderers,
                store,
                status=status,
            )

            self.assertFalse(success)
            self.assertFalse(store.snapshot_path.exists())
            self.assertFalse(store.ready_path.exists())
            publish_snapshot.assert_not_called()
            self.assertEqual(status.transition.call_count, 2)
            self.assertEqual(
                status.transition.call_args_list[1].args[0],
                "degraded",
            )
            self.assertEqual(
                status.transition.call_args_list[1].kwargs["error"]["stage"],
                "render",
            )

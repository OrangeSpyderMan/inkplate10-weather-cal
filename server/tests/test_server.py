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

    def test_rejects_unregistered_renderer_before_production(self):
        profiles = {
            "future": OutputProfile(
                "future",
                "pillow",
                800,
                600,
            )
        }

        with self.assertRaisesRegex(ValueError, "unsupported output renderer"):
            server.build_output_renderers(profiles)

    def test_renders_each_profile_with_dimensions_and_options(self):
        portrait_renderer = mock.Mock()
        landscape_renderer = mock.Mock()
        snapshot = mock.Mock()
        profiles = {
            "portrait": OutputProfile(
                "portrait",
                "firefox",
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
        daily_summary = {
            "temperature": {
                "unit": "\N{DEGREE SIGN}C",
                "value": 10,
                "min": 5,
                "max": 15,
            }
        }
        realtime = mock.Mock()
        realtime.get_current_conditions.side_effect = RuntimeError("offline")

        server.apply_current_conditions(daily_summary, realtime)

        self.assertEqual(daily_summary["temperature"]["value"], 10)
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
                    "firefox",
                    825,
                    1200,
                )
            }
            renderers = {"inkplate10-portrait": mock.Mock()}
            output = store.output_path("inkplate10-portrait", "calendar.png")

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
            )

            self.assertTrue(success)
            self.assertTrue(store.snapshot_path.is_file())
            self.assertTrue(store.ready_path.is_file())
            self.assertTrue(store.producer_cycle_complete(profiles))
            publish_snapshot.assert_called_once()

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
                    "firefox",
                    825,
                    1200,
                )
            }
            renderers = {"inkplate10-portrait": mock.Mock()}

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
            )

            self.assertFalse(success)
            self.assertFalse(store.snapshot_path.exists())
            self.assertFalse(store.ready_path.exists())
            publish_snapshot.assert_not_called()

import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "bin" / "install_server.py"
SPEC = importlib.util.spec_from_file_location("install_server", INSTALLER_PATH)
install_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_server)


class InstallerCopyTests(unittest.TestCase):
    def test_container_host_aliases_match_runtime(self):
        self.assertEqual(
            install_server.container_host_alias("docker"),
            "host.docker.internal",
        )
        self.assertEqual(
            install_server.container_host_alias("podman"),
            "host.containers.internal",
        )
        self.assertEqual(
            install_server.container_host_alias("systemd"),
            "localhost",
        )

    @mock.patch.object(install_server.shutil, "which")
    @mock.patch.object(install_server.subprocess, "run")
    def test_podman_compose_prefers_native_provider(self, run, which):
        which.return_value = "/usr/bin/podman"
        run.side_effect = [
            mock.Mock(returncode=0, stderr=""),
            mock.Mock(returncode=0),
        ]

        command = install_server.check_podman_compose(dry_run=False)

        self.assertEqual(command, ["podman", "compose"])

    @mock.patch.object(install_server.shutil, "which")
    @mock.patch.object(install_server.subprocess, "run")
    def test_podman_compose_falls_back_to_external_provider(self, run, which):
        which.side_effect = lambda command: {
            "podman": "/usr/bin/podman",
            "podman-compose": "/usr/bin/podman-compose",
        }.get(command)
        run.side_effect = [
            mock.Mock(returncode=0, stderr=""),
            mock.Mock(returncode=1),
            mock.Mock(returncode=0),
        ]

        command = install_server.check_podman_compose(dry_run=False)

        self.assertEqual(command, ["podman-compose"])

    def test_weather_choices_match_registered_forecast_providers(self):
        choices = install_server.weather_provider_choices()

        self.assertEqual(
            {name for name, _ in choices},
            set(install_server.FORECAST_PROVIDERS),
        )

    def test_rendered_config_uses_runtime_output_defaults(self):
        answers = {
            "port": 8080,
            "refresh_minutes": 180,
            "weather_service": "openweathermapv3",
            "num_hourly_forecasts": 6,
            "metric": True,
            "netatmo_enabled": False,
            "location": "Landry, FR",
            "mqtt_weather_enabled": False,
            "mqtt_weather_broker": "",
            "mqtt_weather_port": 1883,
            "mqtt_weather_base_topic": "",
            "mqtt_diagnostics_enabled": False,
            "mqtt_diagnostics_broker": "",
            "mqtt_diagnostics_port": 1883,
            "mqtt_diagnostics_topic": "",
        }

        config = install_server.render_config(answers, mode="docker")

        self.assertIn(
            f"  default: {install_server.DEFAULT_OUTPUT_PROFILE}",
            config,
        )
        self.assertIn(
            f"      renderer: {install_server.DEFAULT_RENDERER}",
            config,
        )
        self.assertIn(
            f"      width: {install_server.DEFAULT_IMAGE_WIDTH}",
            config,
        )
        self.assertIn(
            f"      height: {install_server.DEFAULT_IMAGE_HEIGHT}",
            config,
        )
        self.assertIn(
            f"  forecastslicehours: {install_server.DEFAULT_FORECAST_SLICE_HOURS}",
            config,
        )
        self.assertIn(
            f"  forecastleadminutes: {install_server.DEFAULT_FORECAST_LEAD_MINUTES}",
            config,
        )

    def test_rendered_config_uses_refresh_minutes(self):
        answers = {
            "port": 8080,
            "refresh_minutes": 15,
            "weather_service": "openweathermapv3",
            "num_hourly_forecasts": 6,
            "metric": True,
            "netatmo_enabled": False,
            "location": "Landry, FR",
            "mqtt_weather_enabled": False,
            "mqtt_weather_broker": "",
            "mqtt_weather_port": 1883,
            "mqtt_weather_base_topic": "",
            "mqtt_diagnostics_enabled": False,
            "mqtt_diagnostics_broker": "",
            "mqtt_diagnostics_port": 1883,
            "mqtt_diagnostics_topic": "",
        }

        config = install_server.render_config(answers, mode="systemd")

        self.assertIn("  refreshminutes: 15", config)

    def test_podman_config_uses_container_data_path(self):
        answers = {
            "port": 8080,
            "refresh_minutes": 15,
            "weather_service": "openweathermapv3",
            "num_hourly_forecasts": 6,
            "metric": True,
            "netatmo_enabled": False,
            "location": "Landry, FR",
            "mqtt_weather_enabled": False,
            "mqtt_weather_broker": "",
            "mqtt_weather_port": 1883,
            "mqtt_weather_base_topic": "",
            "mqtt_diagnostics_enabled": False,
            "mqtt_diagnostics_broker": "",
            "mqtt_diagnostics_port": 1883,
            "mqtt_diagnostics_topic": "",
        }

        config = install_server.render_config(answers, mode="podman")

        self.assertIn("    token_file: data/netatmo-token.json", config)
        self.assertIn("    broker: host.containers.internal", config)

    def test_legacy_refresh_hours_are_converted_for_reconfiguration(self):
        self.assertEqual(
            install_server.configured_refresh_minutes(
                {"server.refreshhours": "0.3"}
            ),
            18,
        )

    def test_refresh_minutes_take_precedence_during_reconfiguration(self):
        self.assertEqual(
            install_server.configured_refresh_minutes(
                {
                    "server.refreshminutes": "15",
                    "server.refreshhours": "3",
                }
            ),
            15,
        )

    def test_legacy_non_interactive_refresh_hours_are_converted(self):
        self.assertEqual(
            install_server.configured_refresh_minutes(
                {},
                {"refresh_hours": 0.25},
            ),
            15,
        )

    def test_preserves_committed_png_assets_and_ignores_generated_images(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            html_dir = root / "server" / "views" / "html"
            icon_dir = html_dir / "icon"
            pwa_icon_dir = root / "server" / "views" / "pwa" / "icons"
            icon_dir.mkdir(parents=True)
            pwa_icon_dir.mkdir(parents=True)

            ignore = install_server.install_copy_ignore(root)

            self.assertNotIn(
                "cloudy.png",
                ignore(str(icon_dir), ["cloudy.png"]),
            )
            self.assertNotIn(
                "weathercal-icon-192.png",
                ignore(
                    str(pwa_icon_dir),
                    ["weathercal-icon-192.png"],
                ),
            )
            self.assertIn(
                "map.png",
                ignore(str(html_dir), ["map.png", "styles.css"]),
            )
            self.assertIn(
                "calendar.html",
                ignore(str(html_dir), ["calendar.html", "styles.css"]),
            )

    def test_verifies_installed_runtime_files(self):
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as install_dir:
                source = pathlib.Path(source_dir)
                installed = pathlib.Path(install_dir)
                for relative in (
                    pathlib.Path("server/server.py"),
                    pathlib.Path("server/producer_config.py"),
                    pathlib.Path("bin/install_server.py"),
                ):
                    (source / relative).parent.mkdir(parents=True, exist_ok=True)
                    (installed / relative).parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    (source / relative).write_text("same", encoding="utf-8")
                    (installed / relative).write_text("same", encoding="utf-8")

                install_server.verify_installed_runtime(source, installed)

    def test_rejects_stale_installed_runtime_files(self):
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as install_dir:
                source = pathlib.Path(source_dir)
                installed = pathlib.Path(install_dir)
                for relative in (
                    pathlib.Path("server/server.py"),
                    pathlib.Path("server/producer_config.py"),
                    pathlib.Path("bin/install_server.py"),
                ):
                    (source / relative).parent.mkdir(parents=True, exist_ok=True)
                    (installed / relative).parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    (source / relative).write_text("new", encoding="utf-8")
                    (installed / relative).write_text("new", encoding="utf-8")
                (installed / "server/server.py").write_text(
                    "old",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    SystemExit,
                    "server/server.py",
                ):
                    install_server.verify_installed_runtime(source, installed)

    @mock.patch.object(install_server, "run")
    def test_removes_only_legacy_application_logs(self, run):
        install_server.remove_legacy_application_logs(dry_run=False)

        run.assert_called_once_with(
            [
                "find",
                "/srv/inkplate",
                "-maxdepth",
                "1",
                "-type",
                "f",
                "-name",
                "eink-cal-server.log*",
                "-delete",
            ],
            sudo=True,
            dry_run=False,
        )

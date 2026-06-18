import importlib.util
import io
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "bin" / "install_server.py"
SPEC = importlib.util.spec_from_file_location("install_server", INSTALLER_PATH)
install_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_server)


class InstallerCopyTests(unittest.TestCase):
    def test_keyboard_interrupt_exits_cleanly(self):
        stderr = io.StringIO()

        with mock.patch("sys.stderr", stderr):
            result = install_server.run_cli(
                mock.Mock(side_effect=KeyboardInterrupt)
            )

        self.assertEqual(result, 130)
        self.assertEqual(stderr.getvalue(), "\nInstaller cancelled.\n")

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

    @mock.patch.object(install_server.shutil, "which", return_value="/usr/bin/docker")
    @mock.patch.object(install_server.subprocess, "run")
    def test_docker_check_requires_compose_and_daemon_access(self, run, which):
        run.side_effect = [
            mock.Mock(returncode=0),
            mock.Mock(returncode=0, stderr=""),
        ]

        command = install_server.check_compose_runtime(
            "docker",
            dry_run=False,
        )

        self.assertEqual(command, ["docker", "compose"])
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["docker", "compose", "version"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "info"],
        )

    @mock.patch.object(install_server.shutil, "which", return_value="/usr/bin/docker")
    @mock.patch.object(install_server.subprocess, "run")
    def test_docker_check_rejects_unavailable_daemon(self, run, which):
        run.side_effect = [
            mock.Mock(returncode=0),
            mock.Mock(returncode=1, stderr="permission denied"),
        ]

        with self.assertRaisesRegex(SystemExit, "cannot talk to the Docker daemon"):
            install_server.check_compose_runtime("docker", dry_run=False)

    def test_compose_runtime_is_checked_before_any_questions(self):
        for mode in ("docker", "podman"):
            with self.subTest(mode=mode):
                with mock.patch.object(
                    install_server,
                    "check_compose_runtime",
                    side_effect=SystemExit("ERROR: runtime unavailable"),
                ) as check_runtime:
                    with mock.patch.object(
                        install_server,
                        "choose_existing_action",
                    ) as choose_existing_action:
                        with mock.patch.object(
                            install_server,
                            "collect_answers",
                        ) as collect_answers:
                            with tempfile.TemporaryDirectory() as temporary_dir:
                                with self.assertRaisesRegex(
                                    SystemExit,
                                    "runtime unavailable",
                                ):
                                    install_server.install_compose(
                                        pathlib.Path(temporary_dir),
                                        dry_run=False,
                                        mode=mode,
                                    )

                check_runtime.assert_called_once_with(mode, False)
                choose_existing_action.assert_not_called()
                collect_answers.assert_not_called()

    @mock.patch.object(install_server, "run")
    @mock.patch.object(install_server, "prompt_yes_no", return_value=True)
    @mock.patch.object(
        install_server,
        "choose_existing_action",
        return_value="update",
    )
    @mock.patch.object(
        install_server,
        "check_compose_runtime",
        return_value=["podman-compose"],
    )
    def test_podman_uses_logging_override(
        self,
        check_compose_runtime,
        choose_existing_action,
        prompt_yes_no,
        run,
    ):
        with tempfile.TemporaryDirectory() as temporary_dir:
            install_server.install_compose(
                pathlib.Path(temporary_dir),
                dry_run=False,
                mode="podman",
            )

        run.assert_called_once_with(
            [
                "podman-compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "docker-compose.podman.yml",
                "up",
                "--build",
                "-d",
            ],
            dry_run=False,
        )

    @mock.patch.object(
        install_server,
        "run",
        side_effect=subprocess.CalledProcessError(125, ["podman", "compose"]),
    )
    @mock.patch.object(install_server, "prompt_yes_no", return_value=True)
    @mock.patch.object(
        install_server,
        "choose_existing_action",
        return_value="update",
    )
    @mock.patch.object(
        install_server,
        "check_compose_runtime",
        return_value=["podman", "compose"],
    )
    def test_compose_failure_exits_without_python_traceback(
        self,
        check_compose_runtime,
        choose_existing_action,
        prompt_yes_no,
        run,
    ):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with self.assertRaisesRegex(
                SystemExit,
                "Podman Compose failed with exit status 125",
            ):
                install_server.install_compose(
                    pathlib.Path(temporary_dir),
                    dry_run=False,
                    mode="podman",
                )

    def test_disabled_mqtt_features_skip_connection_questions(self):
        answers = {
            "host": "0.0.0.0",
            "port": 8080,
            "refresh_minutes": 180,
            "location": "Landry, FR",
            "weather_service": "openweathermapv3",
            "weather_api_key": "weather-key",
            "google_api_key": "google-key",
            "google_staticmaps_mapid": "map-id",
            "num_hourly_forecasts": 6,
            "forecast_slice_hours": 3,
            "forecast_lead_minutes": 15,
            "metric": True,
            "netatmo_enabled": False,
            "mqtt_weather_enabled": False,
            "mqtt_diagnostics_enabled": False,
        }
        previous_answers = install_server.INSTALLER_ANSWERS
        previous_non_interactive = install_server.NON_INTERACTIVE
        install_server.INSTALLER_ANSWERS = answers
        install_server.NON_INTERACTIVE = True
        try:
            result = install_server.collect_answers({}, {}, mode="podman")
        finally:
            install_server.INSTALLER_ANSWERS = previous_answers
            install_server.NON_INTERACTIVE = previous_non_interactive

        self.assertEqual(
            result["mqtt_weather_broker"],
            "host.containers.internal",
        )
        self.assertEqual(result["mqtt_weather_port"], 1883)
        self.assertEqual(
            result["mqtt_diagnostics_broker"],
            "host.containers.internal",
        )
        self.assertEqual(result["mqtt_diagnostics_port"], 1883)

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

        self.assertIn('  host: "0.0.0.0"', config)
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
            "host": "192.0.2.10",
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

        self.assertIn('  host: "192.0.2.10"', config)
        self.assertIn("  refreshminutes: 15", config)

    def test_rendered_config_quotes_ipv6_bind_address(self):
        answers = {
            "host": "::",
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

        self.assertIn('  host: "::"', config)

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

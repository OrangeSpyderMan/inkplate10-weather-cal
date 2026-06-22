import importlib.util
import io
import json
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
    def write_version_manifest(self, root):
        (root / ".version.json").write_text(
            json.dumps(
                {
                    "version": "v3.2.0+gabc1234",
                    "revision": "abc1234",
                    "build_date": "2026-06-19T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

    def test_gitignore_covers_installer_secret_backups(self):
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/.env.bak.*", gitignore.splitlines())
        self.assertIn(
            "/server/config/config.yaml.bak.*",
            gitignore.splitlines(),
        )

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

    def test_compose_update_makes_existing_config_container_readable(self):
        for mode, command in (
            ("docker", ["docker", "compose"]),
            ("podman", ["podman", "compose"]),
        ):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as temporary_dir:
                    root = pathlib.Path(temporary_dir)
                    config_path = root / "server" / "config" / "config.yaml"
                    config_path.parent.mkdir(parents=True)
                    self.write_version_manifest(root)
                    config_path.write_text(
                        "server:\n  port: 8080\n",
                        encoding="utf-8",
                    )
                    config_path.chmod(0o600)

                    with mock.patch.object(
                        install_server,
                        "check_compose_runtime",
                        return_value=command,
                    ):
                        with mock.patch.object(
                            install_server,
                            "choose_existing_action",
                            return_value="update",
                        ):
                            with mock.patch.object(
                                install_server,
                                "prompt_yes_no",
                                return_value=False,
                            ):
                                install_server.install_compose(
                                    root,
                                    dry_run=False,
                                    mode=mode,
                                )

                    self.assertEqual(
                        config_path.stat().st_mode & 0o777,
                        0o644,
                    )

    def test_compose_config_and_env_use_separate_permissions(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            self.write_version_manifest(root)
            config_path = root / "server" / "config" / "config.yaml"
            env_path = root / ".env"
            config_path.parent.mkdir(parents=True)

            install_server.write_text_atomic(
                config_path,
                "server:\n",
                dry_run=False,
                mode=0o644,
            )
            install_server.write_text_atomic(
                env_path,
                "SECRET=value\n",
                dry_run=False,
                mode=0o600,
            )

            self.assertEqual(config_path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

    def test_podman_override_relabels_each_config_mount(self):
        override = (
            REPO_ROOT / "docker-compose.podman.yml"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            override.count(
                "./server/config:/srv/inkplate/server/config:ro,Z"
            ),
            3,
        )

    @mock.patch.object(install_server, "run")
    @mock.patch.object(install_server, "check_compose_host_port")
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
        check_compose_host_port,
        run,
    ):
        with tempfile.TemporaryDirectory() as temporary_dir:
            self.write_version_manifest(pathlib.Path(temporary_dir))
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
        check_compose_host_port.assert_called_once_with(
            [
                "podman-compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "docker-compose.podman.yml",
            ],
            8080,
            dry_run=False,
        )

    @mock.patch.object(
        install_server,
        "run",
        side_effect=subprocess.CalledProcessError(125, ["podman", "compose"]),
    )
    @mock.patch.object(install_server, "check_compose_host_port")
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
        check_compose_host_port,
        run,
    ):
        with tempfile.TemporaryDirectory() as temporary_dir:
            self.write_version_manifest(pathlib.Path(temporary_dir))
            with self.assertRaisesRegex(
                SystemExit,
                "Podman Compose failed with exit status 125",
            ):
                install_server.install_compose(
                    pathlib.Path(temporary_dir),
                    dry_run=False,
                    mode="podman",
                )

    @mock.patch.object(install_server, "compose_service_is_running")
    @mock.patch.object(install_server, "tcp_port_is_available")
    def test_compose_port_check_rejects_collision_for_docker_and_podman(
        self,
        tcp_port_is_available,
        compose_service_is_running,
    ):
        compose_service_is_running.return_value = False
        tcp_port_is_available.return_value = False

        for command in (
            ["docker", "compose"],
            [
                "podman",
                "compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "docker-compose.podman.yml",
            ],
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    SystemExit,
                    "host TCP port 8080 is already in use",
                ):
                    install_server.check_compose_host_port(
                        command,
                        8080,
                        dry_run=False,
                    )

    @mock.patch.object(
        install_server,
        "compose_service_is_running",
        return_value=True,
    )
    @mock.patch.object(install_server, "tcp_port_is_available")
    def test_compose_port_check_allows_own_running_service(
        self,
        tcp_port_is_available,
        compose_service_is_running,
    ):
        install_server.check_compose_host_port(
            ["docker", "compose"],
            8080,
            dry_run=False,
        )

        tcp_port_is_available.assert_not_called()

    def test_compose_env_uses_selected_server_port(self):
        env = install_server.render_env(
            {
                "weather_api_key": "weather",
                "google_api_key": "google",
                "google_staticmaps_mapid": "map",
                "port": 9090,
                "renderer": "pillow",
            },
            include_optional=False,
            compose=True,
        )

        self.assertIn("INKPLATE_SERVER_PORT=9090", env)
        self.assertIn("INKPLATE_BUILD_TARGET=pillow", env)
        self.assertIn(
            "INKPLATE_IMAGE=inkplate10-weather-cal:pillow-local",
            env,
        )

    def test_compose_update_preserves_secrets_and_aligns_image_flavour(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            env_path = pathlib.Path(temporary_dir) / ".env"
            env_path.write_text(
                "WEATHER_API_KEY=secret\nINKPLATE_BUILD_TARGET=full\n",
                encoding="utf-8",
            )

            install_server.update_compose_flavour_env(
                env_path,
                "pillow",
                dry_run=False,
            )

            updated = env_path.read_text(encoding="utf-8")
            self.assertIn("WEATHER_API_KEY=secret", updated)
            self.assertIn("INKPLATE_BUILD_TARGET=pillow", updated)
            self.assertIn(
                "INKPLATE_IMAGE=inkplate10-weather-cal:pillow-local",
                updated,
            )

    def test_compose_uses_selected_port_for_mapping_and_healthcheck(self):
        compose = (REPO_ROOT / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '"${INKPLATE_SERVER_PORT:-8080}:${INKPLATE_SERVER_PORT:-8080}"',
            compose,
        )
        self.assertIn(
            "127.0.0.1:${INKPLATE_SERVER_PORT:-8080}/api/v1/ready",
            compose,
        )
        self.assertIn(
            "target: ${INKPLATE_BUILD_TARGET:-full}",
            compose,
        )
        self.assertIn(
            "image: ${INKPLATE_IMAGE:-inkplate10-weather-cal:local}",
            compose,
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

    def test_rendered_config_uses_selected_pillow_renderer(self):
        answers = {
            "port": 8080,
            "refresh_minutes": 180,
            "weather_service": "openweathermapv3",
            "num_hourly_forecasts": 6,
            "metric": True,
            "netatmo_enabled": False,
            "location": "Landry, FR",
            "renderer": "pillow",
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

        self.assertIn("      renderer: pillow", config)

    def test_mixed_output_profiles_require_full_dependencies(self):
        config = {
            "outputs.default": "portrait",
            "outputs.profiles.portrait.renderer": "pillow",
            "outputs.profiles.landscape.renderer": "firefox",
        }

        self.assertEqual(
            install_server.configured_renderer(config),
            "pillow",
        )
        self.assertEqual(
            install_server.required_dependency_renderer(config),
            "firefox",
        )

    def test_all_pillow_profiles_allow_pillow_only_dependencies(self):
        config = {
            "outputs.default": "portrait",
            "outputs.profiles.portrait.renderer": "pillow",
            "outputs.profiles.landscape.renderer": "pillow",
        }

        self.assertEqual(
            install_server.required_dependency_renderer(config),
            "pillow",
        )

    @mock.patch.object(install_server, "run")
    @mock.patch.object(
        install_server,
        "choose_firefox_package",
        return_value="firefox-esr",
    )
    def test_native_prerequisites_follow_renderer(
        self,
        choose_firefox_package,
        run,
    ):
        install_server.install_native_prerequisites(False, "pillow")
        pillow_packages = run.call_args_list[1].args[0]
        self.assertNotIn("firefox-esr", pillow_packages)
        self.assertNotIn("curl", pillow_packages)
        choose_firefox_package.assert_not_called()

        run.reset_mock()
        install_server.install_native_prerequisites(False, "firefox")
        firefox_packages = run.call_args_list[1].args[0]
        self.assertIn("firefox-esr", firefox_packages)
        self.assertIn("curl", firefox_packages)

    @mock.patch.object(install_server, "run")
    def test_native_dependencies_follow_renderer(self, run):
        install_server.refresh_dependencies(False, "pillow")
        self.assertTrue(
            any(
                value.endswith("requirements-pillow-only.txt")
                for value in run.call_args_list[0].args[0]
            ),
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/srv/inkplate/inkplate_venv/bin/python",
                "-m",
                "pip",
                "uninstall",
                "-y",
                "airium",
                "selenium",
            ],
        )

        run.reset_mock()
        install_server.refresh_dependencies(False, "firefox")
        self.assertTrue(
            any(
                value.endswith("requirements.txt")
                for value in run.call_args_list[0].args[0]
            ),
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

    @mock.patch.object(install_server, "generate_version_manifest")
    def test_generates_version_manifest_for_checkout(
        self,
        generate_version_manifest,
    ):
        generate_version_manifest.return_value = {
            "version": "v3.2.0+gabc1234.dirty",
            "revision": "abc1234",
            "build_date": "2026-06-19T12:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as source_dir:
            source = pathlib.Path(source_dir)
            (source / ".git").mkdir()

            manifest = install_server.prepare_version_manifest(
                source,
                dry_run=False,
            )

        self.assertEqual(manifest["version"], "v3.2.0+gabc1234.dirty")
        generate_version_manifest.assert_called_once_with(source)

    @mock.patch.object(install_server, "generate_version_manifest")
    def test_preserves_version_manifest_supplied_by_remote_bundle(
        self,
        generate_version_manifest,
    ):
        manifest = {
            "version": "v3.2.0+gabc1234",
            "revision": "abc1234",
            "build_date": "2026-06-19T12:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as source_dir:
            source = pathlib.Path(source_dir)
            (source / ".version.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            result = install_server.prepare_version_manifest(
                source,
                dry_run=False,
            )

        self.assertEqual(result, manifest)
        generate_version_manifest.assert_not_called()

    def test_verifies_installed_runtime_files(self):
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as install_dir:
                source = pathlib.Path(source_dir)
                installed = pathlib.Path(install_dir)
                for relative in (
                    pathlib.Path(".version.json"),
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
                    pathlib.Path(".version.json"),
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

    def test_rejects_deployment_tree_without_git_or_version_manifest(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)

            with self.assertRaisesRegex(
                SystemExit,
                "application version metadata is unavailable",
            ):
                install_server.prepare_version_manifest(
                    root,
                    dry_run=False,
                )

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

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module("install_weather_proxy")


class ProxyInstallerTests(unittest.TestCase):
    def test_dry_run_generates_no_files_and_runs_no_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            for mode in ("docker", "podman", "systemd", "oci"):
                with self.subTest(mode=mode), mock.patch.object(installer.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(installer.main(["--mode", mode, "--config", str(ROOT / "weather-proxy.example.yaml"),
                                                     "--install-dir", str(target), "--dry-run"]), 0)
                    run.assert_not_called()
                    self.assertFalse(target.exists())

    def test_compose_identity_secrets_and_persistent_data(self):
        with mock.patch.object(installer.os, "getuid", return_value=1000), mock.patch.object(installer.os, "getgid", return_value=1000):
            config = installer.compose_config(Path("/srv/proxy"), "proxy:v4.2.0", 8091, mode="podman")
        service = config["services"]["weather-proxy"]
        self.assertEqual(service["user"], "1000:1000")
        self.assertEqual(service["userns_mode"], "keep-id")
        self.assertEqual(service["ports"], ["8091:8090"])
        self.assertIn("/srv/proxy/data:/data:rw,Z", service["volumes"])
        self.assertNotIn("environment", service)

    def test_existing_configuration_preserved_without_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = directory / "source.yaml"
            config.write_text("locations: {}")
            target = directory / "target"
            target.mkdir()
            existing = target / "weather-proxy.yaml"
            existing.write_text("existing settings")
            with mock.patch.object(installer.subprocess, "run"), mock.patch.object(installer.os, "chown"), contextlib.redirect_stdout(io.StringIO()):
                installer.main(["--config", str(config), "--install-dir", str(target)])
            self.assertEqual(existing.read_text(), "existing settings")

    def test_systemd_units_separate_lifecycle_and_persistent_write_boundary(self):
        web = installer.systemd_unit(Path("/srv/weatherproxy"), "web")
        worker = installer.systemd_unit(Path("/srv/weatherproxy"), "worker")
        self.assertIn("-m weather_proxy web", web)
        self.assertIn("-m weather_proxy worker", worker)
        self.assertIn('ReadWritePaths="/srv/weatherproxy/data"', worker)
        self.assertIn("UMask=0077", worker)
        self.assertIn("User=weatherproxy", web)

    def test_container_config_directory_is_private_to_runtime_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "source.yaml"
            config.write_text("locations: {}")
            target = root / "target"
            with mock.patch.object(installer.subprocess, "run"), mock.patch.object(installer.os, "chown"), contextlib.redirect_stdout(io.StringIO()):
                installer.main(["--config", str(config), "--install-dir", str(target)])
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual((target / "weather-proxy.yaml").stat().st_mode & 0o777, 0o600)

    def test_proxmox_component_paths_and_ownership(self):
        proxmox = module("install_proxmox")
        proxmox.configure_proxy_component()
        self.assertEqual(proxmox.IMAGE, "ghcr.io/orangespyderman/weatherproxy")
        self.assertEqual(proxmox.CONFIG_PATH, "/config/weather-proxy.yaml")
        self.assertEqual(proxmox.DATA_DIR, "/data")
        with mock.patch.object(proxmox, "run") as run:
            proxmox.push_file(123, Path("config"), proxmox.CONFIG_PATH, True)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--user") + 1], "weatherproxy")
        self.assertIn(":8090/api/v1/health", proxmox.PROBE_URL)

    def test_calendar_proxy_configuration_omits_upstream_credentials(self):
        calendar = module("install_server")
        answers = {"weather_service": "weatherproxy", "weather_proxy_url": "http://proxy:8090",
                   "weather_proxy_location": "landry", "weather_proxy_token": "private-token",
                   "port": 8080, "refresh_minutes": 180, "num_hourly_forecasts": 6,
                   "metric": True, "netatmo_enabled": False, "location": "Landry, FR",
                   "mqtt_weather_enabled": False, "mqtt_weather_broker": "", "mqtt_weather_port": 1883,
                   "mqtt_weather_base_topic": "", "mqtt_diagnostics_enabled": False,
                   "mqtt_diagnostics_broker": "", "mqtt_diagnostics_port": 1883, "mqtt_diagnostics_topic": "",
                   "weather_api_key": "", "google_api_key": "google", "google_staticmaps_mapid": "map"}
        text = calendar.render_config(answers, "docker")
        self.assertIn('url: "http://proxy:8090"', text)
        self.assertIn('location: "landry"', text)
        self.assertNotIn("private-token", text)
        self.assertIn("WEATHER_PROXY_TOKEN", calendar.render_env(answers, False))


    @unittest.skipUnless(importlib.util.find_spec("yaml") and importlib.util.find_spec("requests"), "Migration integration needs PyYAML and requests")
    def test_installer_migration_prepares_deployment_without_switching_calendar(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "calendar.yaml"
            original = json.dumps({"location": "Landry, FR", "weather": {"service": "openweathermapv4", "apikey": "private-key"}})
            source.write_text(original)
            target = directory / "proxy"
            with mock.patch.object(installer.subprocess, "run"), mock.patch("sys.stdout"):
                installer.main(["--mode", "oci", "--migrate-calendar", str(source), "--install-dir", str(target),
                                "--latitude", "45.57", "--longitude", "6.74", "--timezone", "Europe/Paris",
                                "--proxy-url", "http://proxy:8090"])
            self.assertEqual(source.read_text(), original)
            self.assertTrue((target / "weather-proxy.yaml").is_file())
            self.assertTrue((target / "weather.env").is_file())
            self.assertTrue((target / "migration/calendar-proxy.yaml").is_file())
            self.assertNotIn("private-key", (target / "weather-proxy.yaml").read_text())


if __name__ == "__main__":
    unittest.main()

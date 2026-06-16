import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import configuration


class ConfigurationPathTests(unittest.TestCase):
    def test_env_config_file_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = pathlib.Path(temporary_dir) / "custom.yaml"
            config_path.write_text("server: {}\n", encoding="utf-8")

            with mock.patch.dict(
                configuration.os.environ,
                {"INKPLATE_CONFIG_FILE": str(config_path)},
            ):
                self.assertEqual(configuration.resolve_config_path(), config_path)

    def test_uses_config_directory_path_by_default(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = (
                pathlib.Path(temporary_dir)
                / "server"
                / "config"
                / "config.yaml"
            )
            config_path.parent.mkdir(parents=True)
            config_path.write_text("server: {}\n", encoding="utf-8")

            with (
                mock.patch.dict(configuration.os.environ, {}, clear=True),
                mock.patch.object(
                    configuration,
                    "DEFAULT_CONFIG_DIR_PATH",
                    config_path,
                ),
            ):
                self.assertEqual(configuration.resolve_config_path(), config_path)

    def test_root_server_config_yaml_is_not_a_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root_config = pathlib.Path(temporary_dir) / "server" / "config.yaml"
            root_config.parent.mkdir(parents=True)
            root_config.write_text("server: {}\n", encoding="utf-8")
            missing_config_dir_path = (
                pathlib.Path(temporary_dir)
                / "server"
                / "config"
                / "config.yaml"
            )

            with (
                mock.patch.dict(configuration.os.environ, {}, clear=True),
                mock.patch.object(
                    configuration,
                    "DEFAULT_CONFIG_DIR_PATH",
                    missing_config_dir_path,
                ),
                mock.patch("configuration.sys.stderr"),
                self.assertRaises(SystemExit),
            ):
                configuration.resolve_config_path()


if __name__ == "__main__":
    unittest.main()

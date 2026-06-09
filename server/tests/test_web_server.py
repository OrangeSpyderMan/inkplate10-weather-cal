import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import web_server


class WebServerTests(unittest.TestCase):
    def test_builds_gunicorn_command_from_server_port(self):
        argv = web_server.gunicorn_argv({"server": {"port": 9090}})

        self.assertEqual(argv[0:3], [sys.executable, "-m", "gunicorn"])
        self.assertIn("0.0.0.0:9090", argv)
        self.assertEqual(argv[-1], "web:app")

    @mock.patch("web_server.os.execv")
    @mock.patch("web_server.export_output_profiles")
    @mock.patch(
        "web_server.load_config",
        return_value=(pathlib.Path("config.yaml"), {"server": {"enabled": True}}),
    )
    def test_execs_gunicorn_for_enabled_server(
        self,
        load_config,
        export_profiles,
        execv,
    ):
        web_server.main()

        export_profiles.assert_called_once()
        execv.assert_called_once()
        self.assertEqual(execv.call_args.args[0], sys.executable)

    @mock.patch("web_server.os.execv")
    @mock.patch(
        "web_server.load_config",
        return_value=(pathlib.Path("config.yaml"), {"server": {"enabled": False}}),
    )
    def test_does_not_start_gunicorn_for_disabled_server(
        self,
        load_config,
        execv,
    ):
        self.assertEqual(web_server.main(), 0)
        execv.assert_not_called()

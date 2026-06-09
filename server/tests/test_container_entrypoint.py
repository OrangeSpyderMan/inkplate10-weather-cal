import pathlib
import sys
import unittest
from unittest import mock


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import container_entrypoint


class ContainerEntrypointTests(unittest.TestCase):
    @mock.patch("container_entrypoint.terminate")
    @mock.patch("container_entrypoint.signal.signal")
    @mock.patch("container_entrypoint.subprocess.Popen")
    def test_diagnostics_failure_stops_container(
        self,
        popen,
        signal_handler,
        terminate,
    ):
        producer = mock.Mock()
        producer.poll.return_value = None
        web = mock.Mock()
        web.poll.return_value = None
        diagnostics = mock.Mock()
        diagnostics.poll.return_value = 2
        popen.side_effect = [producer, web, diagnostics]

        self.assertEqual(container_entrypoint.main(), 2)
        terminate.assert_called_once_with([producer, web, diagnostics])

    @mock.patch("container_entrypoint.signal.signal")
    @mock.patch("container_entrypoint.subprocess.Popen")
    def test_disabled_diagnostics_does_not_stop_container(
        self,
        popen,
        signal_handler,
    ):
        producer = mock.Mock()
        producer.poll.return_value = 0
        web = mock.Mock()
        web.poll.return_value = None
        web.wait.return_value = 0
        diagnostics = mock.Mock()
        diagnostics.poll.return_value = 0
        popen.side_effect = [producer, web, diagnostics]

        self.assertEqual(container_entrypoint.main(), 0)
        web.wait.assert_called_once_with()

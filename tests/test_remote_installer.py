import importlib.util
import pathlib
import tarfile
import tempfile
import types
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "bin" / "install_remote.py"
SPEC = importlib.util.spec_from_file_location("install_remote", INSTALLER_PATH)
install_remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_remote)


def arguments(**overrides):
    values = {
        "target": "root@pve1",
        "mode": "proxmox",
        "dry_run": False,
        "remote_dry_run": False,
        "answers": None,
        "non_interactive": False,
        "yes": False,
        "port": None,
        "identity": None,
        "ssh_option": [],
        "tag": None,
        "ctid": None,
        "storage": None,
        "bridge": None,
        "hostname": None,
        "disk_gb": None,
        "memory": None,
        "cores": None,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


class RemoteInstallerTests(unittest.TestCase):
    def test_builds_ssh_command_without_disabling_host_key_checks(self):
        args = arguments(
            port=2222,
            identity=pathlib.Path("/tmp/test-key"),
            ssh_option=["ServerAliveInterval=30"],
        )

        self.assertEqual(
            install_remote.ssh_base_command(args),
            [
                "ssh",
                "-p",
                "2222",
                "-i",
                "/tmp/test-key",
                "-o",
                "ServerAliveInterval=30",
                "root@pve1",
            ],
        )

    def test_forwards_proxmox_arguments_and_remote_answers_path(self):
        args = arguments(
            answers=pathlib.Path("answers.json"),
            non_interactive=True,
            remote_dry_run=True,
            yes=True,
            tag="v3.1.1",
            ctid=123,
            storage="local-zfs",
            bridge="vmbr1",
        )

        self.assertEqual(
            install_remote.remote_installer_args(args),
            [
                "./bin/install_proxmox",
                "--tag",
                "v3.1.1",
                "--ctid",
                "123",
                "--storage",
                "local-zfs",
                "--bridge",
                "vmbr1",
                "--yes",
                "--answers",
                ".remote/answers.json",
                "--non-interactive",
                "--dry-run",
            ],
        )

    def test_rejects_proxmox_options_in_systemd_mode(self):
        args = arguments(mode="systemd", tag="main")

        with self.assertRaisesRegex(SystemExit, "only valid in Proxmox mode"):
            install_remote.validate_args(args)

    def test_tracked_bundle_files_exclude_local_secrets_and_generated_config(self):
        tracked = set(install_remote.tracked_files())

        self.assertNotIn(pathlib.Path(".env"), tracked)
        self.assertNotIn(
            pathlib.Path("server/config/config.yaml"),
            tracked,
        )
        self.assertIn(pathlib.Path("server/server.py"), tracked)

    def test_quotes_remote_workspace_and_arguments(self):
        command = install_remote.build_remote_command(
            "/tmp/inkplate-install.test",
            ["sudo", "-H"],
            ["./bin/install_proxmox", "--hostname", "weather host"],
        )

        self.assertIn("cd /tmp/inkplate-install.test", command)
        self.assertIn("'weather host'", command)
        self.assertIn("sudo -H ./bin/install_proxmox", command)

    @mock.patch.object(install_remote, "tracked_files")
    def test_bundle_contains_tracked_files_and_protected_answers(
        self,
        tracked_files,
    ):
        tracked_files.return_value = [pathlib.Path("README.md")]
        with tempfile.TemporaryDirectory() as temporary_dir:
            answers = pathlib.Path(temporary_dir) / "answers.json"
            answers.write_text('{"secret": "value"}', encoding="utf-8")

            bundle = install_remote.create_bundle(answers)
            try:
                with tarfile.open(bundle, "r:gz") as archive:
                    names = archive.getnames()
                    answer_info = archive.getmember(
                        install_remote.REMOTE_ANSWERS
                    )
                    answer_data = archive.extractfile(answer_info).read()
            finally:
                bundle.unlink(missing_ok=True)

        self.assertEqual(names, ["README.md", ".remote/answers.json"])
        self.assertEqual(answer_info.mode, 0o600)
        self.assertEqual(answer_data, b'{"secret": "value"}')

    @mock.patch.object(install_remote.subprocess, "run")
    def test_interactive_non_root_proxmox_offers_sudo(self, run):
        run.return_value = mock.Mock(returncode=0)

        prefix = install_remote.remote_privilege_prefix(
            ["ssh", "admin@pve1"],
            "proxmox",
            "1000",
            non_interactive=False,
        )

        self.assertEqual(prefix, ["sudo", "-H"])
        self.assertEqual(
            run.call_args.args[0][-1],
            "command -v sudo >/dev/null",
        )

    @mock.patch.object(install_remote.subprocess, "run")
    def test_non_interactive_non_root_proxmox_requires_passwordless_sudo(
        self,
        run,
    ):
        run.return_value = mock.Mock(returncode=0)

        prefix = install_remote.remote_privilege_prefix(
            ["ssh", "admin@pve1"],
            "proxmox",
            "1000",
            non_interactive=True,
        )

        self.assertEqual(prefix, ["sudo", "-H"])
        self.assertIn("sudo -n true", run.call_args.args[0][-1])
        run.assert_called_once()

    @mock.patch.object(install_remote, "remote_output")
    def test_interactive_sudo_defers_proxmox_tool_check_to_installer(
        self,
        remote_output,
    ):
        install_remote.check_remote_requirements(
            ["ssh", "admin@pve1"],
            "proxmox",
            "1000",
            ["sudo", "-H"],
            non_interactive=False,
        )

        remote_output.assert_called_once()
        self.assertIn("python3 tar", remote_output.call_args.args[1])
        self.assertNotIn("pct", remote_output.call_args.args[1])

    @mock.patch.object(install_remote, "remote_output")
    def test_non_interactive_sudo_checks_proxmox_tools_as_root(
        self,
        remote_output,
    ):
        install_remote.check_remote_requirements(
            ["ssh", "admin@pve1"],
            "proxmox",
            "1000",
            ["sudo", "-H"],
            non_interactive=True,
        )

        self.assertEqual(remote_output.call_count, 2)
        self.assertIn(
            "sudo -H sh -c",
            remote_output.call_args_list[1].args[1],
        )
        self.assertIn("pct pveversion", remote_output.call_args_list[1].args[1])

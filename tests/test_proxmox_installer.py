import importlib.util
import pathlib
import types
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "bin" / "install_proxmox.py"
SPEC = importlib.util.spec_from_file_location("install_proxmox", INSTALLER_PATH)
install_proxmox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_proxmox)


class ProxmoxInstallerTests(unittest.TestCase):
    def test_parses_supported_proxmox_versions(self):
        self.assertEqual(
            install_proxmox.parse_proxmox_versions(
                "pve-manager: 9.1.2\npve-container: 6.0.15\n"
            ),
            (9, (6, 0, 15)),
        )

    def test_sorts_release_tags_before_branch_and_miscellaneous_tags(self):
        self.assertEqual(
            install_proxmox.sort_tags(
                ["next", "v1.2.0", "main", "test", "v1.10.0"]
            ),
            ["v1.10.0", "v1.2.0", "main", "next", "test"],
        )

    def test_archive_name_includes_tag_and_digest(self):
        self.assertEqual(
            install_proxmox.archive_filename(
                "v1.2.3",
                "sha256:0123456789abcdef",
            ),
            "inkplate-weather-v1.2.3-0123456789ab.tar",
        )

    def test_rejects_requested_tag_missing_from_registry(self):
        with self.assertRaisesRegex(SystemExit, "not available"):
            install_proxmox.choose_tag(["main", "next"], "v9.9.9")

    @mock.patch.object(install_proxmox, "run")
    def test_create_uses_unprivileged_dhcp_container(self, run):
        args = types.SimpleNamespace(
            hostname="inkplate-weather",
            storage="local-lvm",
            disk_gb=8,
            memory=1024,
            cores=2,
            bridge="vmbr0",
            dry_run=True,
        )

        install_proxmox.create_container(
            123,
            "local:vztmpl/inkplate.tar",
            args,
            "main",
            "sha256:abc",
        )

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["pct", "create", "123", "local:vztmpl/inkplate.tar"])
        self.assertIn("name=eth0,bridge=vmbr0,ip=dhcp,type=veth", command)
        self.assertEqual(
            command[command.index("--unprivileged") + 1],
            "1",
        )
        self.assertEqual(
            command[command.index("--onboot") + 1],
            "1",
        )

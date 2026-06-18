import importlib.util
import io
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
    def test_keyboard_interrupt_exits_cleanly(self):
        stderr = io.StringIO()

        with mock.patch("sys.stderr", stderr):
            result = install_proxmox.run_cli(
                mock.Mock(side_effect=KeyboardInterrupt)
            )

        self.assertEqual(result, 130)
        self.assertEqual(stderr.getvalue(), "\nProxmox installer cancelled.\n")

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

    def test_parses_active_rootdir_storage(self):
        self.assertEqual(
            install_proxmox.parse_storage_status(
                "Name       Type     Status     Total Used Available %\n"
                "local      dir      active  1000000  100    999900 1%\n"
                "slow       dir      inactive 1000000  100    999900 1%\n"
                "local-lvm  lvmthin  active  2000000  200   1999800 1%\n"
            ),
            [
                ("local", "dir, 999900 KiB available"),
                ("local-lvm", "lvmthin, 1999800 KiB available"),
            ],
        )

    @mock.patch.object(install_proxmox, "run")
    def test_create_uses_unprivileged_dhcp_container(self, run):
        args = types.SimpleNamespace(
            hostname="inkplate-weather",
            disk_gb=8,
            memory=1024,
            cores=2,
            bridge="vmbr0",
            dry_run=True,
        )
        storage_plan = install_proxmox.StoragePlan(
            root_storage="local-lvm",
            separate_mounts=False,
        )

        install_proxmox.create_container(
            123,
            "local:vztmpl/inkplate.tar",
            args,
            storage_plan,
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

    @mock.patch.object(install_proxmox, "run")
    def test_create_can_add_separate_data_and_config_mounts(self, run):
        args = types.SimpleNamespace(
            hostname="inkplate-weather",
            disk_gb=8,
            memory=1024,
            cores=2,
            bridge="vmbr0",
            dry_run=True,
        )
        storage_plan = install_proxmox.StoragePlan(
            root_storage="fast",
            separate_mounts=True,
            data_storage="data-store",
            config_storage="config-store",
            data_disk_gb=16,
            config_disk_gb=1,
        )

        install_proxmox.create_container(
            123,
            "local:vztmpl/inkplate.tar",
            args,
            storage_plan,
            "main",
            "sha256:abc",
        )

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--rootfs") + 1], "fast:8")
        self.assertEqual(
            command[command.index("--mp0") + 1],
            "data-store:16,mp=/srv/inkplate/server/data",
        )
        self.assertEqual(
            command[command.index("--mp1") + 1],
            "config-store:1,mp=/srv/inkplate/server/config",
        )

    def test_sets_mount_option_without_duplicating_existing_option(self):
        self.assertEqual(
            install_proxmox.with_mount_option(
                "local-lvm:vm-123-disk-1,mp=/srv/inkplate/server/config,ro=0",
                "ro",
                "1",
            ),
            "local-lvm:vm-123-disk-1,mp=/srv/inkplate/server/config,ro=1",
        )

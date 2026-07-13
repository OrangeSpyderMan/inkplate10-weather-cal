import importlib.util
import hashlib
import io
import json
import os
import pathlib
import pty
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOYER_PATH = REPO_ROOT / "bin" / "deploy_proxmox_oci.py"
SPEC = importlib.util.spec_from_file_location("deploy_proxmox_oci", DEPLOYER_PATH)
deployer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployer)


def write_oci_archive(path, *, media_type=None, architecture="amd64"):
    config = json.dumps(
        {
            "architecture": architecture,
            "os": "linux",
            "config": {
                "User": "inkplate",
                "Cmd": [deployer.legacy.OCI_ENTRYPOINT],
            },
        },
        separators=(",", ":"),
    ).encode()
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": deployer.OCI_MANIFEST_MEDIA_TYPE,
            "config": {
                "mediaType": deployer.OCI_CONFIG_MEDIA_TYPE,
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": media_type or deployer.OCI_MANIFEST_MEDIA_TYPE,
                    "digest": manifest_digest,
                    "size": len(manifest),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, content in (
            ("index.json", index),
            (f"blobs/sha256/{manifest_digest[7:]}", manifest),
            (f"blobs/sha256/{config_digest[7:]}", config),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


class ProxmoxOciDeployerTests(unittest.TestCase):
    @mock.patch.object(deployer.subprocess, "run")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    def test_whiptail_draws_on_tty_while_capturing_only_answer(self, tty_open, run):
        def emit_answer(*args, **kwargs):
            os.write(kwargs["pass_fds"][0], b"main\n")
            return types.SimpleNamespace(returncode=0, stdout=None)

        run.side_effect = emit_answer
        input_context = mock.MagicMock(name="tty_input_context")
        output_context = mock.MagicMock(name="tty_output_context")
        input_terminal = input_context.__enter__.return_value
        output_terminal = output_context.__enter__.return_value
        tty_open.side_effect = [input_context, output_context]
        ui = deployer.PromptUI(enabled=False)
        ui.enabled = True

        result = ui._run("--menu", "Image", "10", "70", "2")

        self.assertEqual(
            tty_open.call_args_list,
            [
                mock.call("/dev/tty", "r", encoding="utf-8"),
                mock.call("/dev/tty", "w", encoding="utf-8"),
            ],
        )
        self.assertIs(run.call_args.kwargs["stdin"], input_terminal)
        self.assertIs(run.call_args.kwargs["stdout"], output_terminal)
        self.assertIs(run.call_args.kwargs["stderr"], output_terminal)
        self.assertEqual(len(run.call_args.kwargs["pass_fds"]), 1)
        self.assertIn(
            str(run.call_args.kwargs["pass_fds"][0]),
            run.call_args.args[0],
        )
        self.assertEqual(result.stdout, "main\n")

    def test_tui_can_open_real_controlling_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_whiptail = pathlib.Path(directory) / "whiptail"
            fake_whiptail.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "args = sys.argv[1:]\n"
                "fd = int(args[args.index('--output-fd') + 1])\n"
                "os.write(1, b'VISIBLE-TUI\\n')\n"
                "os.write(fd, b'main\\n')\n",
                encoding="utf-8",
            )
            fake_whiptail.chmod(0o755)
            pid, terminal = pty.fork()
            if pid == 0:
                try:
                    os.environ["PATH"] = f"{directory}:{os.environ['PATH']}"
                    ui = deployer.PromptUI(enabled=False)
                    ui.enabled = True
                    result = ui._run("--menu", "Image", "10", "70", "2")
                    if result.stdout != "main\n":
                        os._exit(2)
                except BaseException:
                    os._exit(1)
                os._exit(0)

            output = bytearray()
            while True:
                try:
                    chunk = os.read(terminal, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            _, status = os.waitpid(pid, 0)
            os.close(terminal)

        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertIn(b"VISIBLE-TUI", output)

    @mock.patch.object(deployer.subprocess, "run")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    def test_tui_cancel_aborts_cleanly(self, tty_open, run):
        run.return_value = types.SimpleNamespace(returncode=255, stdout=None)
        ui = deployer.PromptUI(enabled=False)
        ui.enabled = True

        with self.assertRaises(KeyboardInterrupt):
            ui._run("--menu", "Image", "10", "70", "2")

    def test_deployer_starts_with_standard_library_only(self):
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(DEPLOYER_PATH), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Proxmox VE 9.1+", result.stdout)

    def test_answers_file_must_not_be_group_or_world_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            answers = pathlib.Path(directory) / "answers.json"
            answers.write_text("{}", encoding="utf-8")
            answers.chmod(0o644)

            with self.assertRaisesRegex(SystemExit, "chmod 600"):
                deployer.validate_answers_permissions(answers)

            answers.chmod(0o600)
            deployer.validate_answers_permissions(answers)

    def test_parses_supported_pve_and_container_versions(self):
        self.assertEqual(
            deployer.parse_versions(
                "pve-manager: 9.2.1\npve-container: 6.1.6\n"
                "lxc-pve: 7.0.0-2\n"
            ),
            ((9, 2), (6, 1, 6), (7, 0, 0, 2)),
        )

    @mock.patch.object(deployer.os, "geteuid", return_value=0)
    @mock.patch.object(deployer.shutil, "which", return_value="/usr/bin/fake")
    @mock.patch.object(deployer.subprocess, "run")
    def test_preflight_rejects_oci_runtime_that_ignores_image_user(
        self,
        run,
        which,
        geteuid,
    ):
        run.return_value = types.SimpleNamespace(
            stdout=(
                "pve-manager: 9.1.1\n"
                "pve-container: 6.0.19\n"
                "lxc-pve: 6.0.5-4\n"
            )
        )

        with self.assertRaisesRegex(SystemExit, "pve-container 6.1.0"):
            deployer.validate_host(dry_run=False)

    @mock.patch.object(deployer.os, "geteuid", return_value=0)
    @mock.patch.object(deployer.shutil, "which", return_value="/usr/bin/fake")
    @mock.patch.object(deployer.subprocess, "run")
    def test_preflight_rejects_lxc_without_unprivileged_group_fix(
        self,
        run,
        which,
        geteuid,
    ):
        run.return_value = types.SimpleNamespace(
            stdout=(
                "pve-manager: 9.1.1\n"
                "pve-container: 6.1.0\n"
                "lxc-pve: 6.0.5-3\n"
            )
        )

        with self.assertRaisesRegex(SystemExit, "lxc-pve 6.0.5-4"):
            deployer.validate_host(dry_run=False)

    @mock.patch.object(deployer.subprocess, "run")
    def test_runtime_acceptance_requires_non_root_pid1_and_read_only_config(
        self,
        run,
    ):
        run.return_value = types.SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        deployer.verify_runtime_acceptance(123, require_read_only_config=True)

        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["pct", "exec", "123", "--", "python3"])
        self.assertIn("/proc/1", command[-1])
        self.assertIn("errno.EROFS", command[-1])
        compile(command[-1], "<runtime-acceptance-probe>", "exec")

    @mock.patch.object(deployer.subprocess, "run")
    def test_runtime_acceptance_rejects_root_pid1(self, run):
        run.return_value = types.SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="PID 1 uid 0, expected 1000",
        )

        with self.assertRaisesRegex(SystemExit, "OCI security acceptance"):
            deployer.verify_runtime_acceptance(
                123,
                require_read_only_config=False,
            )

    def test_rejects_invalid_hostname_and_bridge_before_pct(self):
        with self.assertRaisesRegex(SystemExit, "DNS hostname"):
            deployer.validate_hostname("bad-host-")
        with self.assertRaisesRegex(SystemExit, "bridge name"):
            deployer.validate_bridge("../vmbr0", dry_run=True)

    def test_rejects_malformed_registry_digest(self):
        with self.assertRaisesRegex(SystemExit, "invalid OCI digest"):
            deployer.validate_digest("sha256:not-a-digest", dry_run=False)

    def test_selects_host_manifest_digest_from_multiarch_index(self):
        amd64 = "sha256:" + "a" * 64
        arm64 = "sha256:" + "b" * 64
        manifest = (
            "{\"schemaVersion\":2,\"manifests\":["
            f"{{\"digest\":\"{amd64}\",\"platform\":{{\"os\":\"linux\",\"architecture\":\"amd64\"}}}},"
            f"{{\"digest\":\"{arm64}\",\"platform\":{{\"os\":\"linux\",\"architecture\":\"arm64\"}}}}"
            "]}"
        ).encode()

        self.assertEqual(
            deployer.resolve_manifest_digest(manifest, "arm64"),
            arm64,
        )

    @mock.patch.object(deployer, "host_oci_architecture", return_value="amd64")
    @mock.patch.object(deployer.subprocess, "run")
    def test_validates_required_published_image_contract(self, run, architecture):
        run.return_value = types.SimpleNamespace(
            returncode=0,
            stdout=(
                '{"architecture":"amd64","os":"linux","config":{'
                '"User":"inkplate","WorkingDir":"/srv/inkplate",'
                '"Cmd":["/srv/inkplate/server/container_entrypoint.py"]}}'
            ),
            stderr="",
        )

        deployer.validate_image_config("sha256:" + "a" * 64, dry_run=False)

        self.assertEqual(
            run.call_args.args[0],
            [
                "skopeo", "inspect", "--config",
                "docker://ghcr.io/orangespyderman/inkplate10-weather-cal@sha256:"
                + "a" * 64,
            ],
        )

    def test_accepts_proxmox_compatible_oci_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "inkplate.tar"
            write_oci_archive(archive)

            deployer.verify_archive(archive)

    def test_rejects_preserved_docker_manifest_in_oci_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "inkplate.tar"
            write_oci_archive(
                archive,
                media_type="application/vnd.docker.distribution.manifest.v2+json",
            )

            with self.assertRaisesRegex(SystemExit, "Proxmox-compatible"):
                deployer.verify_archive(archive)

    def test_rejects_archive_for_different_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "inkplate.tar"
            write_oci_archive(archive, architecture="arm64")

            with self.assertRaisesRegex(SystemExit, "expected linux/amd64"):
                with mock.patch.object(
                    deployer, "host_oci_architecture", return_value="amd64"
                ):
                    deployer.verify_archive(archive)

    @mock.patch.object(deployer.legacy, "run")
    def test_pull_uses_valid_skopeo_oci_archive_command(self, run):
        digest = "sha256:" + "a" * 64
        deployer.pull_image(
            "v4.0.0",
            digest,
            pathlib.Path("/not-present/inkplate.tar"),
            dry_run=True,
        )

        self.assertEqual(
            run.call_args.args[0],
            [
                "skopeo",
                "copy",
                "--retry-times",
                "3",
                "--format",
                "oci",
                f"docker://ghcr.io/orangespyderman/inkplate10-weather-cal@{digest}",
                "oci-archive:/not-present/inkplate.tar",
            ],
        )

    @mock.patch.object(deployer.legacy, "run")
    def test_failed_pull_removes_partial_archive(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["skopeo", "copy"])
        digest = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "inkplate.tar"

            with self.assertRaises(subprocess.CalledProcessError):
                deployer.pull_image("main", digest, archive, dry_run=False)

            self.assertFalse(archive.exists())
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    @mock.patch.object(deployer, "verify_archive")
    @mock.patch.object(deployer.legacy, "run")
    def test_incompatible_cached_archive_is_replaced_atomically(self, run, verify):
        verify.side_effect = [SystemExit("ERROR: incompatible OCI archive"), None]

        def write_partial(command, dry_run=False):
            target = pathlib.Path(command[-1].removeprefix("oci-archive:"))
            target.write_bytes(b"converted OCI archive")

        run.side_effect = write_partial
        digest = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "inkplate.tar"
            archive.write_bytes(b"preserved Docker manifest archive")

            deployer.pull_image("next", digest, archive, dry_run=False)

            self.assertEqual(archive.read_bytes(), b"converted OCI archive")
            self.assertEqual(verify.call_count, 2)

    def test_explicit_invalid_numeric_value_is_not_replaced_by_default(self):
        args = types.SimpleNamespace(
            ctid=None,
            storage="root-store",
            template_storage="template-store",
            separate_mounts=False,
            data_storage=None,
            config_storage=None,
            hostname=None,
            bridge=None,
            disk_gb=0,
            data_disk_gb=None,
            config_disk_gb=None,
            memory=None,
            cores=None,
        )
        ui = mock.Mock()

        deployer.configure_deployment_args(
            args,
            "default",
            ui,
            [("root-store", "root storage")],
            [("template-store", "template storage")],
        )

        self.assertEqual(args.disk_gb, 0)
        with self.assertRaisesRegex(SystemExit, "disk-gb"):
            deployer.validate_deployment_arguments(args)

    def test_default_setup_uses_measured_oci_resource_footprint(self):
        args = types.SimpleNamespace(
            ctid=None,
            storage="root-store",
            template_storage="template-store",
            separate_mounts=True,
            data_storage="root-store",
            config_storage="root-store",
            hostname=None,
            bridge=None,
            disk_gb=None,
            data_disk_gb=None,
            config_disk_gb=None,
            memory=None,
            cores=None,
        )

        deployer.configure_deployment_args(
            args,
            "default",
            mock.Mock(),
            [("root-store", "root storage")],
            [("template-store", "template storage")],
        )
        deployer.validate_deployment_arguments(args)

        self.assertEqual(args.cores, 1)
        self.assertEqual(args.memory, 256)
        self.assertEqual(args.disk_gb, 1)
        self.assertEqual(args.data_disk_gb, 1)
        self.assertEqual(args.config_disk_gb, 1)

    def test_memory_floor_is_lower_than_recommended_default(self):
        args = types.SimpleNamespace(
            ctid=None,
            disk_gb=1,
            data_disk_gb=1,
            config_disk_gb=1,
            memory=128,
            cores=1,
            data_storage=None,
            config_storage=None,
            separate_mounts=False,
        )

        deployer.validate_deployment_arguments(args)
        args.memory = 127
        with self.assertRaisesRegex(SystemExit, "at least 128 MiB"):
            deployer.validate_deployment_arguments(args)

    @mock.patch.object(deployer.install_server, "collect_answers", return_value={})
    def test_oci_configuration_defaults_to_ipv6_wildcard(self, collect):
        deployer.collect_configuration_answers(mock.Mock())

        collect.assert_called_once_with(
            {}, {"server.host": "::"}, mode="systemd"
        )

    @mock.patch.object(deployer.legacy, "run")
    def test_create_uses_unprivileged_container_and_persistent_mounts(self, run):
        args = types.SimpleNamespace(
            hostname="inkplate-weather",
            disk_gb=8,
            memory=1024,
            cores=2,
            bridge="vmbr0",
            dry_run=True,
        )
        plan = deployer.legacy.StoragePlan(
            root_storage="root-store",
            separate_mounts=True,
            data_storage="data-store",
            config_storage="config-store",
            data_disk_gb=4,
            config_disk_gb=1,
        )

        deployer.create_container(
            123,
            "local:vztmpl/inkplate.tar",
            args,
            plan,
            "main",
            "sha256:abc",
        )

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["pct", "create", "123", "local:vztmpl/inkplate.tar"])
        self.assertEqual(command[command.index("--unprivileged") + 1], "1")
        self.assertEqual(command[command.index("--onboot") + 1], "1")
        self.assertEqual(command[command.index("--swap") + 1], "256")
        self.assertIn(
            "name=eth0,bridge=vmbr0,ip=dhcp,type=veth",
            command,
        )
        self.assertNotIn("ip6=auto", " ".join(command))
        self.assertIn(
            "data-store:4,mp=/srv/inkplate/server/data",
            [item.removesuffix(",backup=1") for item in command],
        )
        self.assertIn(
            "config-store:1,mp=/srv/inkplate/server/config",
            [item.removesuffix(",backup=1") for item in command],
        )
        self.assertIn("data-store:4,mp=/srv/inkplate/server/data,backup=1", command)
        self.assertIn("config-store:1,mp=/srv/inkplate/server/config,backup=1", command)
        self.assertNotIn("experimental", " ".join(command).lower())

    def test_container_notes_use_branded_html_and_escape_provenance(self):
        description = deployer.container_description(
            "next'><script>alert(1)</script>",
            "sha256:abc&def",
        )

        self.assertIn("<div align='center'>", description)
        self.assertIn("weathercal-icon-192.png", description)
        self.assertIn(">GitHub</a>", description)
        self.assertIn(">Documentation</a>", description)
        self.assertIn(">Container image</a>", description)
        self.assertIn(">Issues</a>", description)
        self.assertIn("sha256:abc&amp;def", description)
        self.assertNotIn("<script>", description)
        self.assertNotIn("\\n", description)

    @mock.patch.object(deployer.subprocess, "run")
    def test_readiness_probe_uses_ipv6_loopback_for_wildcard(self, run):
        run.return_value = types.SimpleNamespace(returncode=0)

        deployer.wait_until_ready(123, "::", 9090)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["pct", "exec", "123", "--"])
        self.assertIn("HTTPConnection('::1',9090", command[-1])
        compile(command[-1], "<ipv6-readiness-probe>", "exec")

    @mock.patch.object(deployer, "set_config_mount_read_only")
    @mock.patch.object(deployer, "verify_application_storage")
    @mock.patch.object(deployer.legacy, "push_file")
    @mock.patch.object(deployer, "wait_for_container_exec")
    @mock.patch.object(deployer.legacy, "run")
    def test_bootstrap_sets_ownership_and_restores_entrypoint(
        self,
        run,
        wait_for_exec,
        push_file,
        verify_storage,
        set_read_only,
    ):
        plan = deployer.legacy.StoragePlan(
            root_storage="root-store",
            separate_mounts=True,
            data_storage="data-store",
            config_storage="config-store",
            data_disk_gb=4,
            config_disk_gb=1,
        )
        config = pathlib.Path("/tmp/config.yaml")
        env = pathlib.Path("/tmp/weather.env")

        deployer.bootstrap_configuration(123, config, env, plan, dry_run=False)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands[0],
            ["pct", "set", "123", "--entrypoint", "/bin/sleep infinity"],
        )
        self.assertIn(
            [
                "pct", "exec", "123", "--", "chown", "inkplate:inkplate",
                "/srv/inkplate/server/config", "/srv/inkplate/server/data",
            ],
            commands,
        )
        self.assertEqual(
            commands[-2],
            [
                "pct", "set", "123", "--entrypoint",
                "/srv/inkplate/server/container_entrypoint.py",
            ],
        )
        self.assertEqual(commands[-1], ["pct", "start", "123"])
        wait_for_exec.assert_called_once_with(123, False)
        self.assertEqual(push_file.call_count, 2)
        verify_storage.assert_called_once_with(123, False)
        set_read_only.assert_called_once_with(123, plan, False)

    @mock.patch.object(deployer.legacy, "run")
    def test_rollback_stops_and_purges_only_new_container(self, run):
        deployer.rollback_container(123)

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["pct", "stop", "123"],
                ["pct", "destroy", "123", "--purge", "1"],
            ],
        )
        self.assertTrue(all(call.kwargs["check"] is False for call in run.call_args_list))

    def test_bootstrap_supports_local_and_remote_one_line_modes(self):
        script = (REPO_ROOT / "bin" / "deploy_proxmox_oci").read_text(
            encoding="utf-8"
        )

        self.assertIn('if [[ "${1:-}" == "--remote" ]]', script)
        self.assertIn('raw_prefix="https://raw.githubusercontent.com/${repo}/"', script)
        self.assertIn('base_url="${raw_prefix}${source_ref}"', script)
        self.assertIn('remote_script="curl -fsSL $quoted_source"', script)
        self.assertIn("if (( $# > 0 )); then", script)
        self.assertIn("sudo -H bash -s -- $quoted_source", script)
        self.assertIn("archive/${source_ref}.tar.gz", script)
        self.assertNotIn("INKPLATE_INSTALL_REF", script)
        self.assertIn("sys.version_info >= (3, 10)", script)
        self.assertIn("python3 -I -S bin/deploy_proxmox_oci.py", script)

    def test_bash_c_without_source_url_fails_clearly(self):
        bootstrap = REPO_ROOT / "bin" / "deploy_proxmox_oci"
        result = subprocess.run(
            ["bash", "-c", bootstrap.read_text(encoding="utf-8"), "--"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("BASH_SOURCE", result.stderr)
        self.assertIn("installer source URL was not retained", result.stderr)

    def test_bootstrap_derives_source_archive_from_launcher_url_not_image_tag(self):
        bootstrap = REPO_ROOT / "bin" / "deploy_proxmox_oci"
        source_url = (
            "https://raw.githubusercontent.com/OrangeSpyderMan/"
            "inkplate10-weather-cal/feature/source-test/bin/deploy_proxmox_oci"
        )
        with tempfile.TemporaryDirectory() as directory:
            directory_path = pathlib.Path(directory)
            capture = directory_path / "curl-args"
            fake_python = directory_path / "python3"
            fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_python.chmod(0o755)
            fake_curl = directory_path / "curl"
            fake_curl.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\nexit 22\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{directory}:/usr/bin:/bin"
            environment["CAPTURE"] = str(capture)

            result = subprocess.run(
                [
                    "bash", "-c", bootstrap.read_text(encoding="utf-8"),
                    source_url, "--tag", "main",
                ],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            arguments = capture.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 22)
        self.assertIn("inkplate10-weather-cal@feature/source-test", result.stdout)
        self.assertIn(
            "https://github.com/OrangeSpyderMan/inkplate10-weather-cal/"
            "archive/feature/source-test.tar.gz",
            arguments,
        )
        self.assertNotIn("archive/main.tar.gz", arguments)

    def test_documentation_has_stable_and_next_image_oneliners(self):
        documentation = (REPO_ROOT / "server" / "README.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "installer_url=https://raw.githubusercontent.com/OrangeSpyderMan/"
            "inkplate10-weather-cal/next/bin/deploy_proxmox_oci",
            documentation,
        )
        self.assertIn('bash -c "$(curl -fsSL "$installer_url")" "$installer_url"', documentation)
        self.assertIn('"$installer_url" --remote root@pve1 --tag next', documentation)
        self.assertIn('"$installer_url" --tag main', documentation)
        self.assertNotIn("INKPLATE_INSTALL_REF", documentation)

    def test_remote_bootstrap_without_options_passes_no_empty_argument(self):
        bootstrap = REPO_ROOT / "bin" / "deploy_proxmox_oci"
        with tempfile.TemporaryDirectory() as directory:
            directory_path = pathlib.Path(directory)
            capture = directory_path / "ssh-args"
            fake_ssh = directory_path / "ssh"
            fake_ssh.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\n",
                encoding="utf-8",
            )
            fake_ssh.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{directory}:{env['PATH']}"
            env["CAPTURE"] = str(capture)
            source_url = (
                "https://raw.githubusercontent.com/OrangeSpyderMan/"
                "inkplate10-weather-cal/v4.0.0/bin/deploy_proxmox_oci"
            )

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    bootstrap.read_text(encoding="utf-8"),
                    source_url,
                    "--remote",
                    "root@pve1",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(arguments[:2], ["-t", "root@pve1"])
            self.assertIn(f"curl -fsSL {source_url} | bash -s -- {source_url};", arguments[2])
            self.assertNotIn("INKPLATE_INSTALL_REF", arguments[2])

    def test_remote_bootstrap_rejects_option_like_target(self):
        bootstrap = REPO_ROOT / "bin" / "deploy_proxmox_oci"
        source_url = (
            "https://raw.githubusercontent.com/OrangeSpyderMan/"
            "inkplate10-weather-cal/next/bin/deploy_proxmox_oci"
        )

        result = subprocess.run(
            [
                "bash",
                "-c",
                bootstrap.read_text(encoding="utf-8"),
                source_url,
                "--remote",
                "-oProxyCommand=anything",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid SSH target", result.stderr)

    def test_complete_noninteractive_workflow_reaches_ready_container(self):
        digest = "sha256:" + "a" * 64
        arguments = [
            str(DEPLOYER_PATH),
            "--non-interactive",
            "--answers", str(REPO_ROOT / "bin" / "install_server.answers.example.json"),
            "--yes",
            "--tag", "main",
            "--storage", "root-store",
            "--template-storage", "template-store",
            "--separate-mounts",
            "--data-storage", "data-store",
            "--config-storage", "config-store",
        ]

        def storage(content, dry_run):
            if content == "vztmpl":
                return [("template-store", "template storage")]
            return [
                ("root-store", "root storage"),
                ("data-store", "data storage"),
                ("config-store", "config storage"),
            ]

        with (
            mock.patch.object(sys, "argv", arguments),
            mock.patch.object(deployer, "validate_answers_permissions"),
            mock.patch.object(deployer, "validate_host"),
            mock.patch.object(deployer, "ensure_dependencies"),
            mock.patch.object(deployer, "available_storage", side_effect=storage),
            mock.patch.object(deployer, "validate_bridge"),
            mock.patch.object(deployer.legacy, "available_tags", return_value=["main"]),
            mock.patch.object(deployer.legacy, "next_ctid", return_value=222),
            mock.patch.object(deployer.legacy, "ensure_unused_ctid"),
            mock.patch.object(deployer, "image_digest", return_value=digest),
            mock.patch.object(deployer, "validate_image_config"),
            mock.patch.object(
                deployer.legacy,
                "template_archive_path",
                return_value=pathlib.Path("/cache/inkplate.tar"),
            ),
            mock.patch.object(deployer, "pull_image") as pull_image,
            mock.patch.object(deployer, "create_container") as create_container,
            mock.patch.object(deployer, "bootstrap_configuration") as bootstrap,
            mock.patch.object(deployer, "wait_until_ready") as wait_ready,
            mock.patch.object(deployer, "verify_runtime_acceptance") as verify_runtime,
            mock.patch.object(deployer.legacy, "container_address", return_value="192.0.2.10"),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            result = deployer.main()

        self.assertEqual(result, 0)
        pull_image.assert_called_once_with(
            "main", digest, pathlib.Path("/cache/inkplate.tar"), False
        )
        self.assertEqual(create_container.call_args.args[0], 222)
        self.assertEqual(create_container.call_args.args[1], "template-store:vztmpl/inkplate-weather-main-aaaaaaaaaaaa.tar")
        bootstrap.assert_called_once()
        wait_ready.assert_called_once_with(222, "0.0.0.0", 8080)
        verify_runtime.assert_called_once_with(222, True)
        self.assertIn("Deployment completed successfully.", stdout.getvalue())
        self.assertIn("http://192.0.2.10:8080/status", stdout.getvalue())

    def test_cli_process_completes_against_stateful_pve_command_surface(self):
        raw_manifest = '{"schemaVersion":2,"config":{"digest":"sha256:fixture"}}'
        digest = "sha256:" + deployer.hashlib.sha256(raw_manifest.encode()).hexdigest()
        image_config = (
            '{"architecture":"amd64","os":"linux","config":{'
            '"User":"inkplate","Cmd":['
            '"/srv/inkplate/server/container_entrypoint.py"]}}'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            cache = root / "cache"
            cache.mkdir()
            log = root / "commands.log"
            answers = root / "answers.json"
            answers.write_text(
                (REPO_ROOT / "bin" / "install_server.answers.example.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            answers.chmod(0o600)

            dispatcher = fake_bin / "fake-pve-command"
            dispatcher.write_text(
                f"#!{sys.executable}\n"
                "import hashlib, io, json, os, pathlib, sys, tarfile\n"
                "name = pathlib.Path(sys.argv[0]).name\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['FAKE_PVE_LOG'], 'a', encoding='utf-8') as f:\n"
                "    f.write(name + ' ' + ' '.join(args) + '\\n')\n"
                "if name == 'pveversion':\n"
                "    print('pve-manager: 9.2.1')\n"
                "    print('pve-container: 6.1.10')\n"
                "    print('lxc-pve: 7.0.0-2')\n"
                "elif name == 'pvesh':\n"
                "    print('321')\n"
                "elif name == 'pvesm' and args[0] == 'status':\n"
                "    content = args[args.index('--content') + 1]\n"
                "    print('Name Type Status Total Used Available %')\n"
                "    storage = 'template-store' if content == 'vztmpl' else 'root-store'\n"
                "    print(f'{storage} dir active 100000000 100 99999900 0%')\n"
                "elif name == 'pvesm' and args[0] == 'path':\n"
                "    filename = args[1].rsplit('/', 1)[-1]\n"
                "    print(pathlib.Path(os.environ['FAKE_PVE_CACHE']) / filename)\n"
                "elif name == 'skopeo' and args[0] == 'list-tags':\n"
                "    print(json.dumps({'Tags': ['main']}))\n"
                "elif name == 'skopeo' and args[0] == 'inspect' and '--raw' in args:\n"
                "    sys.stdout.buffer.write(os.environ['FAKE_OCI_MANIFEST'].encode())\n"
                "elif name == 'skopeo' and args[0] == 'inspect' and '--config' in args:\n"
                "    print(os.environ['FAKE_OCI_CONFIG'])\n"
                "elif name == 'skopeo' and args[0] == 'inspect':\n"
                "    print(os.environ['FAKE_OCI_DIGEST'])\n"
                "elif name == 'skopeo' and args[0] == 'copy':\n"
                "    target = args[-1].removeprefix('oci-archive:')\n"
                "    config = os.environ['FAKE_OCI_CONFIG'].encode()\n"
                "    config_digest = 'sha256:' + hashlib.sha256(config).hexdigest()\n"
                "    manifest = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': config_digest, 'size': len(config)}, 'layers': []}, separators=(',', ':')).encode()\n"
                "    manifest_digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()\n"
                "    index = json.dumps({'schemaVersion': 2, 'manifests': [{'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': manifest_digest, 'size': len(manifest)}]}, separators=(',', ':')).encode()\n"
                "    with tarfile.open(target, 'w') as archive:\n"
                "        for item, content in [('index.json', index), ('blobs/sha256/' + manifest_digest[7:], manifest), ('blobs/sha256/' + config_digest[7:], config)]:\n"
                "            member = tarfile.TarInfo(item)\n"
                "            member.size = len(content)\n"
                "            archive.addfile(member, io.BytesIO(content))\n"
                "elif name == 'pct' and args[0] == 'status':\n"
                "    raise SystemExit(1)\n"
                "elif name == 'pct' and args[0] == 'config':\n"
                "    print('mp1: root-store:1,mp=/srv/inkplate/server/config,backup=1')\n"
                "elif name == 'pct' and args[0] == 'exec' and args[-2:] == ['hostname', '-I']:\n"
                "    print('192.0.2.25')\n",
                encoding="utf-8",
            )
            dispatcher.chmod(0o755)
            for command in ("pveversion", "pct", "pvesm", "pvesh", "skopeo"):
                (fake_bin / command).symlink_to(dispatcher.name)

            runner = root / "run-deployer.py"
            runner.write_text(
                "import importlib.util\n"
                f"path = {str(DEPLOYER_PATH)!r}\n"
                "spec = importlib.util.spec_from_file_location('process_deployer', path)\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(module)\n"
                "module.os.geteuid = lambda: 0\n"
                "module.validate_bridge = lambda bridge, dry_run: None\n"
                "raise SystemExit(module.run_cli())\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "FAKE_PVE_LOG": str(log),
                    "FAKE_PVE_CACHE": str(cache),
                    "FAKE_OCI_MANIFEST": raw_manifest,
                    "FAKE_OCI_CONFIG": image_config,
                    "FAKE_OCI_DIGEST": digest,
                }
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(runner),
                    "--non-interactive",
                    "--answers",
                    str(answers),
                    "--yes",
                    "--tag",
                    "main",
                    "--storage",
                    "root-store",
                    "--template-storage",
                    "template-store",
                    "--separate-mounts",
                    "--data-storage",
                    "root-store",
                    "--config-storage",
                    "root-store",
                ],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Deployment completed successfully.", result.stdout)
            self.assertIn("http://192.0.2.25:8080/status", result.stdout)
            commands = log.read_text(encoding="utf-8")
            self.assertIn("pct create 321", commands)
            self.assertIn("pct start 321", commands)
            self.assertIn("skopeo copy --retry-times 3 --format oci", commands)
            self.assertTrue(any(cache.iterdir()))

    def test_readiness_failure_rolls_back_new_container(self):
        digest = "sha256:" + "a" * 64
        arguments = [
            str(DEPLOYER_PATH),
            "--non-interactive",
            "--answers", str(REPO_ROOT / "bin" / "install_server.answers.example.json"),
            "--yes",
            "--tag", "main",
            "--storage", "root-store",
            "--template-storage", "template-store",
            "--no-separate-mounts",
        ]

        def storage(content, dry_run):
            return (
                [("template-store", "template storage")]
                if content == "vztmpl"
                else [("root-store", "root storage")]
            )

        with (
            mock.patch.object(sys, "argv", arguments),
            mock.patch.object(deployer, "validate_answers_permissions"),
            mock.patch.object(deployer, "validate_host"),
            mock.patch.object(deployer, "ensure_dependencies"),
            mock.patch.object(deployer, "available_storage", side_effect=storage),
            mock.patch.object(deployer, "validate_bridge"),
            mock.patch.object(deployer.legacy, "available_tags", return_value=["main"]),
            mock.patch.object(deployer.legacy, "next_ctid", return_value=223),
            mock.patch.object(deployer.legacy, "ensure_unused_ctid"),
            mock.patch.object(deployer, "image_digest", return_value=digest),
            mock.patch.object(deployer, "validate_image_config"),
            mock.patch.object(
                deployer.legacy,
                "template_archive_path",
                return_value=pathlib.Path("/cache/inkplate.tar"),
            ),
            mock.patch.object(deployer, "pull_image"),
            mock.patch.object(deployer, "create_container"),
            mock.patch.object(deployer, "bootstrap_configuration"),
            mock.patch.object(
                deployer,
                "wait_until_ready",
                side_effect=SystemExit("not ready"),
            ),
            mock.patch.object(deployer, "container_exists", return_value=True),
            mock.patch.object(deployer, "rollback_container") as rollback,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            with self.assertRaisesRegex(SystemExit, "not ready"):
                deployer.main()

        rollback.assert_called_once_with(223)


if __name__ == "__main__":
    unittest.main()

import hashlib
import io
import json
import os
import pathlib
import pty
import shlex
import subprocess
import tarfile
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOYER = REPO_ROOT / "bin" / "deploy_proxmox_oci"
ENTRYPOINT = "/srv/inkplate/server/container_entrypoint.py"
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"


def write_oci_archive(path, *, architecture="amd64", media_type=MANIFEST_MEDIA_TYPE):
    config = json.dumps(
        {
            "architecture": architecture,
            "os": "linux",
            "config": {"User": "inkplate", "Cmd": [ENTRYPOINT]},
        },
        separators=(",", ":"),
    ).encode()
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": MANIFEST_MEDIA_TYPE,
            "config": {
                "mediaType": CONFIG_MEDIA_TYPE,
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
                    "mediaType": media_type,
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


def shell(script, *arguments, env=None):
    return subprocess.run(
        ["bash", "-c", script, "--", *map(str, arguments)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


class StandaloneProxmoxOciDeployerTests(unittest.TestCase):
    def test_standard_helper_scripts_one_liner_needs_no_source_metadata(self):
        result = subprocess.run(
            ["bash", "-c", DEPLOYER.read_text(encoding="utf-8"), "--", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: deploy_proxmox_oci", result.stdout)
        self.assertNotIn("source URL", result.stderr)

    def test_deployer_is_a_single_bash_runtime_without_source_downloads(self):
        script = DEPLOYER.read_text(encoding="utf-8")

        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("deploy_proxmox_oci.py", script)
        self.assertNotIn("install_server.py", script)
        self.assertNotIn("INKPLATE_INSTALL_REF", script)
        self.assertNotIn("github.com/${repo}/archive", script)
        self.assertNotIn("raw.githubusercontent.com/${repo}", script)
        self.assertIn('IMAGE="ghcr.io/orangespyderman/inkplate10-weather-cal"', script)

    def test_bash_syntax_and_shellcheck(self):
        syntax = subprocess.run(
            ["bash", "-n", str(DEPLOYER)], capture_output=True, text=True
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        if pathlib.Path("/usr/bin/shellcheck").exists():
            checked = subprocess.run(
                ["shellcheck", str(DEPLOYER)], capture_output=True, text=True
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_dependency_preflight_succeeds_when_everything_is_installed(self):
        command = f"""
source {shlex.quote(str(DEPLOYER))}
command() {{
  [[ $1 == -v && $2 =~ ^(jq|skopeo|whiptail)$ ]] && return 0
  builtin command "$@"
}}
NO_TUI=0; NON_INTERACTIVE=0
ensure_dependencies
printf 'continued\\n'
"""
        result = shell(command)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[PASS] Host dependency: jq", result.stdout)
        self.assertIn("[PASS] Host dependency: skopeo", result.stdout)
        self.assertIn("[PASS] Host dependency: whiptail", result.stdout)
        self.assertTrue(result.stdout.endswith("continued\n"))

    def test_tui_uses_the_controlling_terminal_and_captures_only_the_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_whiptail = pathlib.Path(directory) / "whiptail"
            fake_whiptail.write_text(
                "#!/usr/bin/env bash\n"
                "while (($#)); do\n"
                "  if [[ $1 == --output-fd ]]; then fd=$2; break; fi\n"
                "  shift\n"
                "done\n"
                "printf 'VISIBLE-TUI\\n'\n"
                "printf 'next\\n' >&\"$fd\"\n",
                encoding="utf-8",
            )
            fake_whiptail.chmod(0o755)
            command = (
                f"source {shlex.quote(str(DEPLOYER))}; TUI=1; "
                "answer=$(prompt_choice Image main image_tag "
                "main stable next development); [[ $answer == next ]]"
            )
            pid, terminal = pty.fork()
            if pid == 0:
                os.environ["PATH"] = f"{directory}:{os.environ['PATH']}"
                os.execl("/usr/bin/bash", "bash", "-c", command, "--")

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

        self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
        self.assertIn(b"VISIBLE-TUI", output)

    def test_rejects_insecure_answers_file_and_invalid_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            answers = pathlib.Path(directory) / "answers.json"
            answers.write_text('{"metric":"perhaps"}', encoding="utf-8")
            answers.chmod(0o644)
            validate = f"source {shlex.quote(str(DEPLOYER))}; ANSWERS_FILE=$1; validate_answers"
            insecure = shell(validate, answers)
            self.assertNotEqual(insecure.returncode, 0)
            self.assertIn("chmod 600", insecure.stderr)

            answers.chmod(0o600)
            invalid_boolean = shell(
                f"source {shlex.quote(str(DEPLOYER))}; "
                "ANSWERS_FILE=$1; prompt_yes_no Metric 1 metric",
                answers,
            )

        self.assertNotEqual(invalid_boolean.returncode, 0)
        self.assertIn("must be true or false", invalid_boolean.stderr)

    def test_validates_deployment_values_before_container_creation(self):
        command = (
            f"source {shlex.quote(str(DEPLOYER))}; DRY_RUN=1; SETUP=default; "
            "HOSTNAME=bad-host-; ! (validate_deployment)"
        )
        result = shell(command)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[FAIL]", result.stderr)
        self.assertIn("invalid DNS hostname", result.stderr)

    def test_validates_static_ipv4_configuration_before_container_creation(self):
        base = (
            f"source {shlex.quote(str(DEPLOYER))}; DRY_RUN=1; SETUP=advanced; "
            "IPV4_MODE=static; IPV4_ADDRESS=$1; IPV4_GATEWAY=$2; validate_deployment"
        )
        valid = shell(base, "192.168.1.184/24", "192.168.1.1")
        invalid_address = shell(base, "192.168.1.999/24", "192.168.1.1")
        invalid_gateway = shell(base, "192.168.1.184/24", "192.168.1.999")

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("static IPv4 192.168.1.184/24 via 192.168.1.1", valid.stdout)
        self.assertNotEqual(invalid_address.returncode, 0)
        self.assertIn("valid CIDR notation", invalid_address.stderr)
        self.assertNotEqual(invalid_gateway.returncode, 0)
        self.assertIn("gateway must be a valid address", invalid_gateway.stderr)

    def test_validates_static_ipv6_configuration_before_container_creation(self):
        base = (
            f"source {shlex.quote(str(DEPLOYER))}; DRY_RUN=1; SETUP=advanced; "
            "IPV6_MODE=static; IPV6_ADDRESS=$1; IPV6_GATEWAY=$2; validate_deployment"
        )
        valid = shell(base, "2001:db8::184/64", "fe80::1")
        invalid_address = shell(base, "2001:db8::1::184/64", "fe80::1")
        invalid_prefix = shell(base, "2001:db8::184/129", "fe80::1")
        invalid_gateway = shell(base, "2001:db8::184/64", "fe80::xyz")

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("static IPv6 2001:db8::184/64 via fe80::1", valid.stdout)
        self.assertNotEqual(invalid_address.returncode, 0)
        self.assertIn("valid CIDR notation", invalid_address.stderr)
        self.assertNotEqual(invalid_prefix.returncode, 0)
        self.assertIn("valid CIDR notation", invalid_prefix.stderr)
        self.assertNotEqual(invalid_gateway.returncode, 0)
        self.assertIn("IPv6 gateway must be a valid address", invalid_gateway.stderr)

    def test_resolves_the_host_platform_digest_from_a_multiarch_index(self):
        amd64 = "sha256:" + "a" * 64
        arm64 = "sha256:" + "b" * 64
        index = json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {"digest": amd64, "platform": {"os": "linux", "architecture": "amd64"}},
                    {"digest": arm64, "platform": {"os": "linux", "architecture": "arm64"}},
                ],
            }
        )
        command = f"""
source {shlex.quote(str(DEPLOYER))}
host_architecture() {{ echo arm64; }}
skopeo() {{ printf '%s' "$INDEX"; }}
TAG=next
image_digest
"""
        environment = os.environ.copy()
        environment["INDEX"] = index
        result = shell(command, env=environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), arm64)

    def test_rejects_an_image_that_does_not_run_as_inkplate(self):
        wrong_user = json.dumps(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {"User": "root", "Cmd": [ENTRYPOINT]},
            }
        )
        command = (
            f"source {shlex.quote(str(DEPLOYER))}; "
            "host_architecture() { echo amd64; }; "
            "! (validate_image_json \"$IMAGE_CONFIG\" test-image)"
        )
        environment = os.environ.copy()
        environment["IMAGE_CONFIG"] = wrong_user
        result = shell(command, env=environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("must run as user inkplate", result.stderr)

    def test_archive_validation_accepts_expected_contract_and_rejects_wrong_arch(self):
        with tempfile.TemporaryDirectory() as directory:
            good = pathlib.Path(directory) / "good.tar"
            wrong = pathlib.Path(directory) / "wrong.tar"
            write_oci_archive(good)
            write_oci_archive(wrong, architecture="arm64")
            command = (
                f"source {shlex.quote(str(DEPLOYER))}; "
                "verify_archive \"$1\"; ! (verify_archive \"$2\")"
            )
            result = shell(command, good, wrong)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("platform does not match", result.stderr)

    def test_archive_validation_rejects_a_non_oci_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "docker-manifest.tar"
            write_oci_archive(
                archive,
                media_type="application/vnd.docker.distribution.manifest.v2+json",
            )
            command = (
                f"source {shlex.quote(str(DEPLOYER))}; ! (verify_archive \"$1\")"
            )
            result = shell(command, archive)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no Proxmox-compatible", result.stderr)

    def test_container_command_uses_host_managed_network_and_persistent_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            log = pathlib.Path(directory) / "commands"
            command = f"""
source {shlex.quote(str(DEPLOYER))}
run() {{ printf '%q ' "$@" >>"$LOG"; printf '\n' >>"$LOG"; }}
CTID=123; HOSTNAME=inkplate-weather; ROOT_STORAGE=root-store; DISK_GB=1
MEMORY_MB=256; CORES=1; BRIDGE=vmbr0; TAG=next; SEPARATE_MOUNTS=1
DATA_STORAGE=data-store; DATA_DISK_GB=1; CONFIG_STORAGE=config-store; CONFIG_DISK_GB=1
create_container template-store:vztmpl/image.tar sha256:{'a' * 64}
"""
            environment = os.environ.copy()
            environment["LOG"] = str(log)
            result = shell(command, env=environment)
            generated = log.read_text(encoding="utf-8").replace("\\,", ",")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pct create 123", generated)
        self.assertIn("name=eth0,bridge=vmbr0,ip=dhcp,type=veth", generated)
        self.assertNotIn("ip6=", generated)
        self.assertIn("data-store:1,mp=/srv/inkplate/server/data,backup=1", generated)
        self.assertIn("config-store:1,mp=/srv/inkplate/server/config,backup=1", generated)

    def test_container_command_supports_static_ipv4_and_optional_gateway(self):
        command = f"""
source {shlex.quote(str(DEPLOYER))}
run() {{ printf '%q ' "$@"; printf '\n'; }}
CTID=123; HOSTNAME=inkplate-weather; ROOT_STORAGE=root-store; DISK_GB=1
MEMORY_MB=256; CORES=1; BRIDGE=vmbr0; TAG=next; SEPARATE_MOUNTS=0
IPV4_MODE=static; IPV4_ADDRESS=192.168.1.184/24; IPV4_GATEWAY=192.168.1.1
create_container template-store:vztmpl/image.tar sha256:{'a' * 64}
"""
        result = shell(command)
        generated = result.stdout.replace("\\,", ",")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "name=eth0,bridge=vmbr0,ip=192.168.1.184/24,gw=192.168.1.1,type=veth",
            generated,
        )

    def test_container_command_supports_static_dual_stack_networking(self):
        command = f"""
source {shlex.quote(str(DEPLOYER))}
run() {{ printf '%q ' "$@"; printf '\n'; }}
CTID=123; HOSTNAME=inkplate-weather; ROOT_STORAGE=root-store; DISK_GB=1
MEMORY_MB=256; CORES=1; BRIDGE=vmbr0; TAG=next; SEPARATE_MOUNTS=0
IPV4_MODE=static; IPV4_ADDRESS=192.168.1.184/24; IPV4_GATEWAY=192.168.1.1
IPV6_MODE=static; IPV6_ADDRESS=2001:db8::184/64; IPV6_GATEWAY=fe80::1
create_container template-store:vztmpl/image.tar sha256:{'a' * 64}
"""
        result = shell(command)
        generated = result.stdout.replace("\\,", ",")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "name=eth0,bridge=vmbr0,ip=192.168.1.184/24,gw=192.168.1.1,"
            "ip6=2001:db8::184/64,gw6=fe80::1,type=veth",
            generated,
        )

    def test_failure_after_creation_rolls_back_only_the_new_container(self):
        with tempfile.TemporaryDirectory() as directory:
            log = pathlib.Path(directory) / "pct.log"
            command = f"""
source {shlex.quote(str(DEPLOYER))}
pct() {{ printf '%s\\n' "$*" >>"$LOG"; [[ $1 == config ]] && return 0; return 0; }}
CTID=105; CREATE_ATTEMPTED=1; KEEP_FAILED=0
trap cleanup EXIT
false
"""
            environment = os.environ.copy()
            environment["LOG"] = str(log)
            result = shell(command, env=environment)
            calls = log.read_text(encoding="utf-8")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stop 105", calls)
        self.assertIn("destroy 105 --purge 1", calls)

    def test_container_notes_are_html_with_links_and_real_line_breaks(self):
        digest = "sha256:" + "a" * 64
        command = (
            f"source {shlex.quote(str(DEPLOYER))}; TAG=next; "
            f"description {digest}"
        )
        result = shell(command)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<img src=", result.stdout)
        self.assertIn("github.com/OrangeSpyderMan/inkplate10-weather-cal", result.stdout)
        self.assertIn(digest, result.stdout)
        self.assertNotIn("\\n", result.stdout)

    def test_noninteractive_workflow_renders_config_and_reaches_success(self):
        digest = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            answers = root / "answers.json"
            answer_values = json.loads(
                (REPO_ROOT / "bin" / "install_server.answers.example.json").read_text(
                    encoding="utf-8"
                )
            )
            answer_values["host"] = "::"
            answers.write_text(json.dumps(answer_values), encoding="utf-8")
            answers.chmod(0o600)
            output_config = root / "config.yaml"
            output_env = root / "weather.env"
            command = f"""
source {shlex.quote(str(DEPLOYER))}
validate_host() {{ :; }}
ensure_dependencies() {{ :; }}
bridge_exists() {{ :; }}
storage_options() {{
  if [[ "$1" == vztmpl ]]; then echo 'template-store|template storage';
  else printf 'root-store|root storage\ndata-store|data storage\nconfig-store|config storage\n'; fi
}}
available_tags() {{ echo main; }}
image_digest() {{ echo {digest}; }}
validate_selected_image() {{ :; }}
next_ctid() {{ echo 321; }}
pull_image() {{ :; }}
create_container() {{ CREATE_ATTEMPTED=1; }}
bootstrap_configuration() {{ cp "$1" "$OUTPUT_CONFIG"; cp "$2" "$OUTPUT_ENV"; }}
wait_until_ready() {{ :; }}
verify_runtime() {{ :; }}
container_address() {{ echo 192.0.2.25; }}
pct() {{ [[ "$1" == config ]] && return 1; return 0; }}
pvesm() {{ [[ "$1" == path ]] && echo "$CACHE/${{2##*/}}"; }}
main --non-interactive --answers "$ANSWERS" --yes --tag main \
  --storage root-store --template-storage template-store --separate-mounts \
  --data-storage data-store --config-storage config-store
"""
            environment = os.environ.copy()
            environment.update(
                {
                    "ANSWERS": str(answers),
                    "OUTPUT_CONFIG": str(output_config),
                    "OUTPUT_ENV": str(output_env),
                    "CACHE": str(root),
                }
            )
            result = shell(command, env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = output_config.read_text(encoding="utf-8")
            secret_env = output_env.read_text(encoding="utf-8")

        self.assertIn("Deployment completed successfully.", result.stdout)
        self.assertIn("Pre-flight checks", result.stdout)
        self.assertIn("[SKIP] Interactive UI (--non-interactive selected)", result.stdout)
        self.assertIn("[PASS] Container root storage: root-store", result.stdout)
        self.assertIn("[PASS] OCI image tag is published: main", result.stdout)
        self.assertIn("[PASS] Container ID is unused: 321", result.stdout)
        self.assertIn("[PASS] Platform-specific image digest resolved:", result.stdout)
        self.assertIn("[PASS] OCI image contract:", result.stdout)
        self.assertIn("http://192.0.2.25:8080/status", result.stdout)
        self.assertIn('host: "::"', config)
        self.assertIn("renderer: pillow", config)
        self.assertIn("WEATHER_API_KEY=replace-with-weather-api-key", secret_env)
        self.assertNotIn("replace-with-weather-api-key", config)

    def test_documentation_uses_original_standard_one_liner(self):
        documentation = (REPO_ROOT / "server" / "README.md").read_text(
            encoding="utf-8"
        )
        expected = (
            'bash -c "$(curl -fsSL https://raw.githubusercontent.com/'
            'OrangeSpyderMan/inkplate10-weather-cal/next/bin/deploy_proxmox_oci)" '
            '-- --tag next'
        )

        self.assertIn(expected, documentation)
        self.assertNotIn("installer_url=", documentation)
        self.assertNotIn("INKPLATE_INSTALL_REF", documentation)


if __name__ == "__main__":
    unittest.main()

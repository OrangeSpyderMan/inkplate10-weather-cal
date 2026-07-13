#!/usr/bin/env python3
"""Guided Proxmox VE 9.1+ deployment of the published OCI image.

This is intentionally separate from the existing server and experimental
Proxmox installers while the native OCI workflow is being proven.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import html
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = REPO_ROOT / "bin" / "install_proxmox.py"
SPEC = importlib.util.spec_from_file_location("inkplate_install_proxmox", LEGACY_PATH)
legacy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(legacy)
install_server = legacy.install_server

APP_NAME = "Inkplate Weather Calendar"
IMAGE = legacy.IMAGE
MIN_PVE_VERSION = (9, 1)
MIN_CONTAINER_VERSION = (6, 1, 0)
MIN_LXC_VERSION = (6, 0, 5, 4)
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
DEFAULT_BIND_HOST = "::"
DEFAULT_ROOT_DISK_GB = 1
DEFAULT_DATA_DISK_GB = 1
DEFAULT_CONFIG_DISK_GB = 1
DEFAULT_MEMORY_MB = 256
DEFAULT_CORES = 1
DEFAULT_SWAP_MB = 256
PROJECT_URL = "https://github.com/OrangeSpyderMan/inkplate10-weather-cal"
PROJECT_ICON_URL = (
    "https://raw.githubusercontent.com/OrangeSpyderMan/"
    "inkplate10-weather-cal/main/server/views/pwa/icons/weathercal-icon-192.png"
)


class PromptUI:
    def __init__(self, enabled: bool):
        self.enabled = bool(enabled and shutil.which("whiptail") and Path("/dev/tty").exists())
        self._originals = {
            "choice": install_server.prompt_choice,
            "text": install_server.prompt_text,
            "secret": install_server.prompt_secret,
            "integer": install_server.prompt_int,
            "yes_no": install_server.prompt_yes_no,
            "ip_address": install_server.prompt_ip_address,
        }

    def _answered(self, key):
        return key is not None and key in install_server.INSTALLER_ANSWERS

    def _run(self, *args: str, allow_no: bool = False):
        answer_read_fd, answer_write_fd = os.pipe()
        with os.fdopen(answer_read_fd, "r", encoding="utf-8") as answer_output:
            try:
                with (
                    open("/dev/tty", "r", encoding="utf-8") as terminal_input,
                    open("/dev/tty", "w", encoding="utf-8") as terminal_output,
                ):
                    result = subprocess.run(
                        [
                            "whiptail",
                            "--backtitle",
                            APP_NAME,
                            *args,
                            "--output-fd",
                            str(answer_write_fd),
                        ],
                        stdin=terminal_input,
                        stdout=terminal_output,
                        stderr=terminal_output,
                        pass_fds=(answer_write_fd,),
                        text=True,
                    )
            finally:
                os.close(answer_write_fd)
            result.stdout = answer_output.read()
        if result.returncode == 0 or (allow_no and result.returncode == 1):
            return result
        raise KeyboardInterrupt

    def message(self, message: str, title: str = "Invalid value"):
        if self.enabled:
            self._run("--title", title, "--msgbox", message, "9", "72")
        else:
            print(message)

    def choice(self, label, options, default, key=None):
        if not self.enabled or self._answered(key) or install_server.NON_INTERACTIVE:
            return self._originals["choice"](label, options, default, key)
        items = [item for option in options for item in option]
        result = self._run(
            "--title", label,
            "--default-item", default,
            "--menu", label,
            "20", "78", str(min(max(len(options), 4), 12)),
            *items,
        )
        return result.stdout.strip()

    def text(self, label, default="", required=True, key=None):
        if not self.enabled or self._answered(key) or install_server.NON_INTERACTIVE:
            return self._originals["text"](label, default, required, key)
        current = str(default)
        while True:
            result = self._run(
                "--title", label,
                "--inputbox", label,
                "10", "78", current,
            )
            value = result.stdout.strip()
            if value or not required:
                return value
            self.message(f"{label} is required.")

    def secret(self, label, default="", required=True, key=None):
        if not self.enabled or self._answered(key) or install_server.NON_INTERACTIVE:
            return self._originals["secret"](label, default, required, key)
        if default and self.yes_no(f"Keep the existing value for {label}?", True):
            return default
        while True:
            result = self._run(
                "--title", label,
                "--passwordbox", label,
                "10", "78",
            )
            value = result.stdout.strip()
            if value or not required:
                return value
            self.message(f"{label} is required.")

    def integer(self, label, default, minimum, maximum, key=None):
        if not self.enabled or self._answered(key) or install_server.NON_INTERACTIVE:
            return self._originals["integer"](
                label, default, minimum, maximum, key
            )
        current = str(default)
        while True:
            result = self._run(
                "--title", label,
                "--inputbox", f"{label} ({minimum}-{maximum})",
                "10", "78", current,
            )
            current = result.stdout.strip()
            try:
                parsed = int(current)
            except ValueError:
                self.message("Enter a whole number.")
                continue
            if minimum <= parsed <= maximum:
                return parsed
            self.message(f"Enter a value from {minimum} to {maximum}.")

    def yes_no(self, label, default, key=None):
        if not self.enabled or self._answered(key) or install_server.NON_INTERACTIVE:
            return self._originals["yes_no"](label, default, key)
        flags = ["--title", label]
        if not default:
            flags.append("--defaultno")
        result = self._run(
            *flags,
            "--yesno", label,
            "10", "78",
            allow_no=True,
        )
        return result.returncode == 0

    def ip_address(self, label, default, key):
        while True:
            value = self.text(label, default=default, key=key)
            try:
                ipaddress.ip_address(value)
            except ValueError:
                if self._answered(key) or install_server.NON_INTERACTIVE:
                    raise SystemExit(f"ERROR: {key} must be an IPv4 or IPv6 address")
                self.message("Please enter an IPv4 or IPv6 address.")
                default = value
                continue
            return value


@contextmanager
def configuration_prompts(ui: PromptUI):
    replacements = {
        "prompt_choice": ui.choice,
        "prompt_text": ui.text,
        "prompt_secret": ui.secret,
        "prompt_int": ui.integer,
        "prompt_yes_no": ui.yes_no,
        "prompt_ip_address": ui.ip_address,
    }
    originals = {name: getattr(install_server, name) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(install_server, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(install_server, name, value)


def collect_configuration_answers(ui: PromptUI):
    with configuration_prompts(ui):
        return install_server.collect_answers(
            {}, {"server.host": DEFAULT_BIND_HOST}, mode="systemd"
        )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="deploy_proxmox_oci",
        description="Deploy the published Inkplate OCI image on Proxmox VE 9.1+.",
        epilog=(
            "Remote mode is provided by the Bash entry point: "
            "deploy_proxmox_oci --remote root@pve1 [OPTIONS]"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview decisions and commands without changing the host",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the final container-creation confirmation",
    )
    parser.add_argument(
        "--setup",
        choices=("default", "advanced"),
        help="select recommended defaults or the advanced resource wizard",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="use line-oriented terminal prompts instead of whiptail",
    )
    parser.add_argument(
        "--keep-failed",
        action="store_true",
        help="retain a newly created container when setup or readiness fails",
    )
    parser.add_argument("--tag", help="published release or branch image tag")
    parser.add_argument("--ctid", type=int, help="new Proxmox container ID")
    parser.add_argument(
        "--template-storage",
        help="vztmpl-capable storage used for the OCI archive cache",
    )
    parser.add_argument("--storage", help="rootdir-capable root disk storage")
    parser.add_argument(
        "--separate-mounts",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="create separate Proxmox config and data volumes",
    )
    parser.add_argument("--data-storage", help="storage for generated data")
    parser.add_argument("--config-storage", help="storage for config and secrets")
    parser.add_argument("--bridge", help="Proxmox network bridge")
    parser.add_argument("--hostname", help="container hostname")
    parser.add_argument("--disk-gb", type=int, help="root disk size in GiB")
    parser.add_argument("--data-disk-gb", type=int, help="data volume size in GiB")
    parser.add_argument(
        "--config-disk-gb",
        type=int,
        help="config volume size in GiB",
    )
    parser.add_argument("--memory", type=int, help="container memory in MiB")
    parser.add_argument("--cores", type=int, help="container CPU core count")
    parser.add_argument(
        "--answers",
        type=Path,
        help="JSON defaults or unattended configuration answers",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="disable all prompts; requires --answers",
    )
    return parser


def validate_answers_permissions(path: Path | None):
    if path is None or not path.exists():
        return
    if path.stat().st_mode & 0o077:
        raise SystemExit(
            f"ERROR: answers file {path} may contain secrets and must not be "
            "accessible by group or other users. Run: chmod 600 "
            f"{path}"
        )


def main():
    args = build_parser().parse_args()
    validate_answers_permissions(args.answers)
    install_server.configure_answers(args.answers, args.non_interactive)

    print(f"{APP_NAME} — Proxmox OCI deployer")
    print("=" * 48)
    print("Fresh PVE 9.1+ deployments only; existing CTIDs are never replaced.")
    if args.dry_run:
        print("Dry run: host changes will only be previewed.")

    validate_host(args.dry_run)
    ensure_dependencies(args)
    ui = PromptUI(enabled=not args.no_tui and not args.dry_run)

    setup = args.setup or ui.choice(
        "Setup mode",
        [
            ("default", "recommended resources and DHCP networking"),
            ("advanced", "custom CTID, resources, bridge, and disk sizes"),
        ],
        "default",
        "proxmox_oci_setup",
    )

    root_options = available_storage("rootdir", args.dry_run)
    template_options = available_storage("vztmpl", args.dry_run)
    configure_deployment_args(args, setup, ui, root_options, template_options)
    validate_deployment_arguments(args)
    validate_hostname(args.hostname)
    validate_bridge(args.bridge, args.dry_run)

    tags = legacy.available_tags(args.dry_run, args.tag)
    if not tags:
        raise SystemExit("ERROR: no supported v4 OCI image tags were found.")
    tag = args.tag or ui.choice(
        "Published OCI image",
        [(value, legacy.tag_description(value)) for value in tags],
        preferred_tag(tags),
        "proxmox_oci_tag",
    )
    if tag not in tags:
        raise SystemExit(f"ERROR: OCI tag {tag!r} is not available.")

    ctid = args.ctid if args.ctid is not None else legacy.next_ctid(args.dry_run)
    if setup == "advanced" and args.ctid is None:
        ctid = ui.integer("Container ID", ctid, 100, 999999999, "proxmox_oci_ctid")
    legacy.ensure_unused_ctid(ctid, args.dry_run)
    digest = image_digest(tag, args.dry_run)
    validate_digest(digest, args.dry_run)
    validate_image_config(digest, args.dry_run)

    plan = legacy.StoragePlan(
        root_storage=args.storage,
        separate_mounts=args.separate_mounts,
        data_storage=args.data_storage,
        config_storage=args.config_storage,
        data_disk_gb=args.data_disk_gb if args.separate_mounts else None,
        config_disk_gb=args.config_disk_gb if args.separate_mounts else None,
    )
    show_plan(args, ctid, tag, digest, plan)
    if not args.yes and not ui.yes_no(
        "Create and start this container?",
        False,
        "proxmox_oci_confirm",
    ):
        print("Container creation cancelled; no container was created.")
        return 0

    answers = collect_configuration_answers(ui)
    config_text = install_server.render_config(answers, mode="systemd")
    env_text = install_server.render_env(
        answers,
        include_optional=bool(answers["netatmo_enabled"]),
    )

    archive_name = legacy.archive_filename(tag, digest)
    archive_volume = f"{args.template_storage}:vztmpl/{archive_name}"
    archive_path = legacy.template_archive_path(archive_volume, args.dry_run)
    create_attempted = False
    try:
        with tempfile.TemporaryDirectory(prefix="inkplate-oci-") as directory:
            config_file = Path(directory) / "config.yaml"
            env_file = Path(directory) / "weather.env"
            legacy.write_generated_file(config_file, config_text, 0o600, args.dry_run)
            legacy.write_generated_file(env_file, env_text, 0o600, args.dry_run)
            pull_image(tag, digest, archive_path, args.dry_run)
            create_attempted = not args.dry_run
            create_container(ctid, archive_volume, args, plan, tag, digest)
            bootstrap_configuration(
                ctid, config_file, env_file, plan, args.dry_run
            )
        if args.dry_run:
            print("Dry run complete; no container was created.")
            return 0
        wait_until_ready(ctid, str(answers["host"]), int(answers["port"]))
        verify_runtime_acceptance(ctid, plan.separate_mounts)
    except BaseException:
        created = create_attempted and container_exists(ctid)
        if created and not args.keep_failed:
            rollback_container(ctid)
        elif created:
            print(f"Container {ctid} was retained for diagnosis.", file=sys.stderr)
        raise

    address = legacy.container_address(ctid)
    print("\nDeployment completed successfully.")
    print(f"Container: {ctid} (running)")
    url_host = f"[{address}]" if ":" in address else address
    port = int(answers["port"])
    print(f"Calendar:  http://{url_host}:{port}/calendar.png")
    print(f"Viewer:    http://{url_host}:{port}/app")
    print(f"Status:    http://{url_host}:{port}/status")
    print(f"Console:   pct console {ctid}")
    return 0


def validate_host(dry_run: bool):
    required = ("pveversion", "pct", "pvesm", "pvesh")
    if dry_run and not shutil.which("pveversion"):
        print("Would verify root access, PVE 9.1+, pct, pvesm, and pvesh.")
        return
    if os.geteuid() != 0:
        raise SystemExit("ERROR: run this deployer as root on a Proxmox VE host.")
    missing = [command for command in required if not shutil.which(command)]
    if missing:
        raise SystemExit("ERROR: missing Proxmox commands: " + ", ".join(missing))
    result = subprocess.run(
        ["pveversion", "--verbose"], capture_output=True, text=True, check=True
    )
    pve, container, lxc = parse_versions(result.stdout)
    if pve[0] != 9 or pve < MIN_PVE_VERSION:
        raise SystemExit("ERROR: native OCI containers require Proxmox VE 9.1 or newer.")
    if container < MIN_CONTAINER_VERSION:
        raise SystemExit(
            "ERROR: preserving the OCI application user requires pve-container "
            "6.1.0 or newer. Fully update this PVE host, then retry."
        )
    if lxc < MIN_LXC_VERSION:
        raise SystemExit(
            "ERROR: preserving OCI supplementary groups in an unprivileged "
            "container requires lxc-pve 6.0.5-4 or newer. Fully update this "
            "PVE host, then retry."
        )
    print(
        "Pre-flight: Proxmox VE "
        f"{pve[0]}.{pve[1]}, pve-container {'.'.join(map(str, container))}, "
        f"lxc-pve {'.'.join(map(str, lxc[:3]))}-{lxc[3]}."
    )


def parse_versions(output: str):
    manager = re.search(r"pve-manager:\s+(\d+)\.(\d+)", output)
    container = re.search(r"pve-container:\s+(\d+)\.(\d+)\.(\d+)", output)
    lxc = re.search(r"lxc-pve:\s+(\d+)\.(\d+)\.(\d+)-(\d+)", output)
    if not manager or not container or not lxc:
        raise SystemExit("ERROR: unable to determine installed Proxmox versions.")
    return (
        tuple(int(value) for value in manager.groups()),
        tuple(int(value) for value in container.groups()),
        tuple(int(value) for value in lxc.groups()),
    )


def ensure_dependencies(args):
    packages = []
    if not shutil.which("skopeo"):
        packages.append("skopeo")
    if not args.no_tui and not args.non_interactive and not shutil.which("whiptail"):
        packages.append("whiptail")
    if not packages:
        return
    print("Required host packages: " + ", ".join(packages))
    legacy.run(["apt-get", "update"], dry_run=args.dry_run)
    legacy.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *packages],
        dry_run=args.dry_run,
    )


def available_storage(content: str, dry_run: bool):
    if dry_run and not shutil.which("pvesm"):
        if content == "rootdir":
            return [("local-lvm", "LXC volume storage"), ("local", "directory storage")]
        return [("local", "container-template storage")]
    result = subprocess.run(
        ["pvesm", "status", "--content", content],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: unable to list {content} storage: {result.stderr.strip()}")
    options = legacy.parse_storage_status(result.stdout)
    if not options:
        raise SystemExit(f"ERROR: no active Proxmox storage supports {content} content.")
    return options


def select_storage(ui, label, requested, options, default, key):
    names = {name for name, _ in options}
    value = requested or ui.choice(label, options, default, key)
    if value not in names:
        raise SystemExit(
            f"ERROR: {label} must be one of: {', '.join(sorted(names))}"
        )
    return value


def configure_deployment_args(args, setup, ui, root_options, template_options):
    root_default = legacy.default_storage(root_options)
    template_default = legacy.default_storage(template_options)
    args.storage = select_storage(
        ui, "Container root storage", args.storage, root_options,
        root_default, "proxmox_oci_root_storage",
    )
    args.template_storage = select_storage(
        ui, "OCI image cache storage", args.template_storage, template_options,
        template_default, "proxmox_oci_template_storage",
    )

    if args.separate_mounts is None:
        args.separate_mounts = ui.yes_no(
            "Create separate persistent config and data volumes?",
            True,
            "proxmox_oci_separate_mounts",
        )
    if args.separate_mounts:
        args.data_storage = select_storage(
            ui, "Generated data storage", args.data_storage, root_options,
            args.storage, "proxmox_oci_data_storage",
        )
        args.config_storage = select_storage(
            ui, "Configuration storage", args.config_storage, root_options,
            args.storage, "proxmox_oci_config_storage",
        )
    elif args.data_storage or args.config_storage:
        raise SystemExit("ERROR: separate storage options require --separate-mounts.")

    args.hostname = args.hostname if args.hostname is not None else "inkplate-weather"
    args.bridge = args.bridge if args.bridge is not None else "vmbr0"
    args.disk_gb = (
        args.disk_gb if args.disk_gb is not None else DEFAULT_ROOT_DISK_GB
    )
    args.data_disk_gb = (
        args.data_disk_gb
        if args.data_disk_gb is not None
        else DEFAULT_DATA_DISK_GB
    )
    args.config_disk_gb = (
        args.config_disk_gb
        if args.config_disk_gb is not None
        else DEFAULT_CONFIG_DISK_GB
    )
    args.memory = args.memory if args.memory is not None else DEFAULT_MEMORY_MB
    args.cores = args.cores if args.cores is not None else DEFAULT_CORES
    if setup != "advanced":
        return

    args.hostname = ui.text(
        "Container hostname", args.hostname, key="proxmox_oci_hostname"
    )
    bridges = network_bridges()
    args.bridge = ui.choice(
        "Network bridge",
        [(bridge, "Proxmox Linux bridge") for bridge in bridges],
        args.bridge if args.bridge in bridges else bridges[0],
        "proxmox_oci_bridge",
    )
    args.cores = ui.integer("CPU cores", args.cores, 1, 128, "proxmox_oci_cores")
    args.memory = ui.integer("Memory (MiB)", args.memory, 256, 1048576, "proxmox_oci_memory")
    args.disk_gb = ui.integer("Root disk (GiB)", args.disk_gb, 1, 1048576, "proxmox_oci_disk_gb")
    if args.separate_mounts:
        args.data_disk_gb = ui.integer("Data disk (GiB)", args.data_disk_gb, 1, 1048576, "proxmox_oci_data_disk_gb")
        args.config_disk_gb = ui.integer("Config disk (GiB)", args.config_disk_gb, 1, 1024, "proxmox_oci_config_disk_gb")


def validate_deployment_arguments(args):
    """Validate the native OCI workflow without changing the legacy deployer."""
    if args.ctid is not None and not 100 <= args.ctid <= 999999999:
        raise SystemExit("ERROR: --ctid must be between 100 and 999999999.")
    if args.disk_gb < 1:
        raise SystemExit("ERROR: --disk-gb must be at least 1.")
    if args.data_disk_gb < 1:
        raise SystemExit("ERROR: --data-disk-gb must be at least 1.")
    if args.config_disk_gb < 1:
        raise SystemExit("ERROR: --config-disk-gb must be at least 1.")
    if args.memory < 256:
        raise SystemExit("ERROR: --memory must be at least 256 MiB.")
    if args.cores < 1:
        raise SystemExit("ERROR: --cores must be positive.")
    if (args.data_storage or args.config_storage) and not args.separate_mounts:
        raise SystemExit(
            "ERROR: separate storage options require --separate-mounts."
        )


def network_bridges():
    bridges = sorted(
        path.name
        for path in Path("/sys/class/net").glob("vmbr*")
        if (path / "bridge").exists()
    )
    return bridges or ["vmbr0"]


def validate_hostname(hostname: str):
    if len(hostname) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise SystemExit(f"ERROR: invalid DNS hostname: {hostname!r}.")


def validate_bridge(bridge: str, dry_run: bool):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", bridge):
        raise SystemExit(f"ERROR: invalid Linux bridge name: {bridge!r}.")
    bridge_path = Path("/sys/class/net", bridge)
    if dry_run and not bridge_path.exists():
        print(f"Would verify network bridge {bridge}.")
        return
    if not bridge_path.exists() or not (bridge_path / "bridge").exists():
        raise SystemExit(
            f"ERROR: Linux network bridge {bridge!r} does not exist."
        )


def preferred_tag(tags):
    return next((tag for tag in tags if tag.startswith("v")), "main" if "main" in tags else tags[0])


def host_oci_architecture() -> str:
    machine = platform.machine().lower()
    architectures = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        return architectures[machine]
    except KeyError:
        raise SystemExit(
            f"ERROR: published OCI images do not support host architecture {machine!r}."
        ) from None


def resolve_manifest_digest(raw_manifest: bytes, architecture: str) -> str:
    try:
        manifest = json.loads(raw_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: registry returned invalid OCI manifest JSON: {exc}")
    manifests = manifest.get("manifests")
    if not manifests:
        return "sha256:" + hashlib.sha256(raw_manifest).hexdigest()
    matches = [
        item
        for item in manifests
        if item.get("platform", {}).get("os") == "linux"
        and item.get("platform", {}).get("architecture") == architecture
    ]
    if not matches:
        raise SystemExit(
            f"ERROR: OCI image has no linux/{architecture} manifest."
        )
    digest = str(matches[0].get("digest", ""))
    validate_digest(digest, dry_run=False)
    return digest


def image_digest(tag: str, dry_run: bool) -> str:
    if dry_run and not shutil.which("skopeo"):
        return "sha256:<resolved-during-install>"
    result = subprocess.run(
        ["skopeo", "inspect", "--raw", f"docker://{IMAGE}:{tag}"],
        capture_output=True,
    )
    if result.returncode != 0:
        error = result.stderr.decode(errors="replace").strip()
        raise SystemExit(f"ERROR: unable to inspect OCI manifest: {error}")
    return resolve_manifest_digest(result.stdout, host_oci_architecture())


def validate_image_config(digest: str, dry_run: bool):
    if dry_run and digest == "sha256:<resolved-during-install>":
        print("Would verify the published image entrypoint, user, OS, and architecture.")
        return
    result = subprocess.run(
        ["skopeo", "inspect", "--config", f"docker://{IMAGE}@{digest}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: unable to inspect OCI image configuration: {result.stderr.strip()}"
        )
    try:
        image = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: registry returned invalid OCI config JSON: {exc}")
    validate_image_contract(image, "selected OCI image")


def validate_image_contract(image: dict, source: str):
    if not isinstance(image, dict):
        raise SystemExit(f"ERROR: {source} configuration is not a JSON object.")
    config = image.get("config") or {}
    if not isinstance(config, dict):
        raise SystemExit(f"ERROR: {source} config field is not a JSON object.")
    command = [*(config.get("Entrypoint") or []), *(config.get("Cmd") or [])]
    expected = legacy.OCI_ENTRYPOINT
    if expected not in command:
        raise SystemExit(
            f"ERROR: {source} does not start {expected}; found {command!r}."
        )
    user = str(config.get("User") or "").split(":", 1)[0]
    if user != "inkplate":
        raise SystemExit(
            f"ERROR: {source} must run as user 'inkplate'; found {user!r}."
        )
    expected_architecture = host_oci_architecture()
    if image.get("os") != "linux" or image.get("architecture") != expected_architecture:
        raise SystemExit(
            f"ERROR: {source} platform is "
            f"{image.get('os')}/{image.get('architecture')}, expected "
            f"linux/{expected_architecture}."
        )


def validate_digest(digest: str, dry_run: bool):
    if dry_run and digest == "sha256:<resolved-during-install>":
        return
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit(f"ERROR: registry returned an invalid OCI digest: {digest!r}")


def archive_json(archive: tarfile.TarFile, members: dict, name: str, label: str):
    member = members.get(name)
    if member is None or not member.isfile():
        raise SystemExit(f"ERROR: OCI archive has no regular {label} file ({name}).")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise SystemExit(f"ERROR: unable to read {label} from OCI archive.")
    raw = extracted.read()
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: OCI archive contains invalid {label} JSON: {exc}")


def descriptor_blob(archive, members, descriptor, label):
    if not isinstance(descriptor, dict):
        raise SystemExit(f"ERROR: OCI archive has an invalid {label} descriptor.")
    digest = str(descriptor.get("digest", ""))
    validate_digest(digest, dry_run=False)
    name = f"blobs/sha256/{digest.removeprefix('sha256:')}"
    value, raw = archive_json(archive, members, name, label)
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise SystemExit(
            f"ERROR: OCI archive {label} digest mismatch: expected {digest}, "
            f"found {actual}."
        )
    expected_size = descriptor.get("size")
    if not isinstance(expected_size, int) or expected_size != len(raw):
        raise SystemExit(
            f"ERROR: OCI archive {label} size mismatch: expected "
            f"{expected_size!r}, found {len(raw)}."
        )
    return value


def verify_archive(archive_path: Path):
    """Validate the exact OCI layout contract consumed by PVE's importer."""
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            members = {}
            for member in archive.getmembers():
                name = member.name.removeprefix("./")
                if name in members:
                    raise SystemExit(
                        f"ERROR: OCI archive contains duplicate member {name!r}."
                    )
                members[name] = member
            index, _ = archive_json(archive, members, "index.json", "image index")
            if not isinstance(index, dict):
                raise SystemExit("ERROR: OCI archive image index is not a JSON object.")
            if index.get("schemaVersion") != 2:
                raise SystemExit("ERROR: OCI archive image index is not schema version 2.")
            architecture = host_oci_architecture()
            manifests = index.get("manifests")
            if not isinstance(manifests, list):
                manifests = []
            compatible = [
                item for item in manifests
                if isinstance(item, dict)
                and item.get("mediaType") == OCI_MANIFEST_MEDIA_TYPE
                and (
                    not item.get("platform")
                    or (
                        isinstance(item["platform"], dict)
                        and item["platform"].get("os", "linux") == "linux"
                        and item["platform"].get("architecture") == architecture
                    )
                )
            ]
            if not compatible:
                media_types = sorted({
                    str(item.get("mediaType")) for item in manifests
                    if isinstance(item, dict)
                })
                raise SystemExit(
                    "ERROR: OCI archive has no Proxmox-compatible "
                    f"linux/{architecture} image manifest; index media types: "
                    f"{media_types!r}."
                )
            manifest = descriptor_blob(
                archive, members, compatible[0], "image manifest"
            )
            if not isinstance(manifest, dict):
                raise SystemExit(
                    "ERROR: OCI archive image manifest is not a JSON object."
                )
            if manifest.get("schemaVersion") != 2:
                raise SystemExit(
                    "ERROR: OCI archive image manifest is not schema version 2."
                )
            config_descriptor = manifest.get("config")
            if not isinstance(config_descriptor, dict) or (
                config_descriptor.get("mediaType") != OCI_CONFIG_MEDIA_TYPE
            ):
                found = (
                    config_descriptor.get("mediaType")
                    if isinstance(config_descriptor, dict)
                    else None
                )
                raise SystemExit(
                    "ERROR: OCI archive has unsupported image config media type "
                    f"{found!r}."
                )
            image = descriptor_blob(
                archive, members, config_descriptor, "image configuration"
            )
            validate_image_contract(image, "cached OCI image")
    except (OSError, tarfile.TarError) as exc:
        raise SystemExit(f"ERROR: unable to read OCI archive {archive_path}: {exc}")


def pull_image(tag: str, digest: str, archive_path: Path, dry_run: bool):
    if archive_path.exists():
        if dry_run:
            print(f"Would validate cached OCI archive: {archive_path}")
            return
        try:
            verify_archive(archive_path)
        except SystemExit as exc:
            print(f"{exc}\nReplacing incompatible cached archive: {archive_path}")
        else:
            print(f"Using cached OCI archive: {archive_path}")
            return
    source = f"docker://{IMAGE}:{tag}"
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        source = f"docker://{IMAGE}@{digest}"
    if dry_run:
        legacy.run(
            [
                "skopeo", "copy", "--retry-times", "3",
                "--format", "oci",
                source,
                f"oci-archive:{archive_path}",
            ],
            dry_run=True,
        )
        return

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial_name = tempfile.mkstemp(
        prefix=f".{archive_path.name}.",
        suffix=".partial",
        dir=archive_path.parent,
    )
    os.close(descriptor)
    partial_path = Path(partial_name)
    partial_path.unlink()
    try:
        legacy.run(
            [
                "skopeo", "copy", "--retry-times", "3",
                "--format", "oci",
                source,
                f"oci-archive:{partial_path}",
            ]
        )
        verify_archive(partial_path)
        os.replace(partial_path, archive_path)
    finally:
        partial_path.unlink(missing_ok=True)


def container_description(tag: str, digest: str) -> str:
    image_reference = html.escape(f"{IMAGE}:{tag}", quote=True)
    safe_digest = html.escape(digest, quote=True)
    return f"""<div align='center'>
  <a href='{PROJECT_URL}' target='_blank' rel='noopener noreferrer'>
    <img src='{PROJECT_ICON_URL}' alt='Inkplate Weather Calendar logo' style='width:96px;height:96px;'/>
  </a>

  <h2 style='font-size:24px;margin:16px 0 8px;'>Inkplate Weather Calendar</h2>
  <p style='margin:8px 0 16px;'>Weather forecasts and calendars for Inkplate e-paper displays.</p>

  <span style='margin:0 10px;'>
    <i class='fa fa-github fa-fw'></i>
    <a href='{PROJECT_URL}' target='_blank' rel='noopener noreferrer'>GitHub</a>
  </span>
  <span style='margin:0 10px;'>
    <i class='fa fa-book fa-fw'></i>
    <a href='{PROJECT_URL}/blob/main/server/README.md' target='_blank' rel='noopener noreferrer'>Documentation</a>
  </span>
  <span style='margin:0 10px;'>
    <i class='fa fa-cube fa-fw'></i>
    <a href='{PROJECT_URL}/pkgs/container/inkplate10-weather-cal' target='_blank' rel='noopener noreferrer'>Container image</a>
  </span>
  <span style='margin:0 10px;'>
    <i class='fa fa-exclamation-circle fa-fw'></i>
    <a href='{PROJECT_URL}/issues' target='_blank' rel='noopener noreferrer'>Issues</a>
  </span>

  <p style='margin:16px 0 0;font-size:12px;'>
    <strong>Image:</strong> <code>{image_reference}</code><br/>
    <strong>Digest:</strong> <code>{safe_digest}</code>
  </p>
</div>"""


def create_container(ctid, archive_volume, args, plan, tag, digest):
    command = [
        "pct", "create", str(ctid), archive_volume,
        "--hostname", args.hostname,
        "--rootfs", f"{plan.root_storage}:{args.disk_gb}",
        "--memory", str(args.memory),
        "--cores", str(args.cores),
        "--swap", str(DEFAULT_SWAP_MB),
        "--net0",
        f"name=eth0,bridge={args.bridge},ip=dhcp,ip6=auto,type=veth",
        "--unprivileged", "1",
        "--onboot", "1",
        "--description", container_description(tag, digest),
    ]
    if plan.separate_mounts:
        command.extend(
            [
                "--mp0", f"{plan.data_storage}:{plan.data_disk_gb},mp={legacy.DATA_DIR},backup=1",
                "--mp1", f"{plan.config_storage}:{plan.config_disk_gb},mp={legacy.CONFIG_DIR},backup=1",
            ]
        )
    legacy.run(command, dry_run=args.dry_run)


def wait_for_container_exec(ctid: int, dry_run: bool):
    if dry_run:
        print(f"Would wait for container {ctid} to accept pct exec commands.")
        return
    for _ in range(20):
        result = subprocess.run(
            ["pct", "exec", str(ctid), "--", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise SystemExit(f"ERROR: container {ctid} did not become available for setup.")


def wait_until_ready(ctid: int, host: str, port: int):
    address = ipaddress.ip_address(host)
    if address.is_unspecified:
        probe_host = "::1" if address.version == 6 else "127.0.0.1"
    else:
        probe_host = host
    probe = (
        "import http.client,sys; "
        f"c=http.client.HTTPConnection({probe_host!r},{port},timeout=5); "
        "c.request('GET','/api/v1/ready'); "
        "r=c.getresponse(); r.read(); "
        "sys.exit(0 if r.status == 200 else r.status)"
    )
    for _ in range(36):
        result = subprocess.run(
            ["pct", "exec", str(ctid), "--", "python3", "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(5)
    raise SystemExit(
        f"ERROR: container {ctid} did not become ready on {host}:{port}. "
        f"Inspect with: pct console {ctid}"
    )


def bootstrap_configuration(ctid, config_file, env_file, plan, dry_run):
    legacy.run(
        ["pct", "set", str(ctid), "--entrypoint", "/bin/sleep infinity"],
        dry_run=dry_run,
    )
    legacy.run(["pct", "start", str(ctid)], dry_run=dry_run)
    wait_for_container_exec(ctid, dry_run)
    try:
        legacy.run(
            [
                "pct", "exec", str(ctid), "--", "mkdir", "-p",
                legacy.CONFIG_DIR, legacy.DATA_DIR,
            ],
            dry_run=dry_run,
        )
        legacy.run(
            [
                "pct", "exec", str(ctid), "--", "chown", "inkplate:inkplate",
                legacy.CONFIG_DIR, legacy.DATA_DIR,
            ],
            dry_run=dry_run,
        )
        legacy.push_file(ctid, config_file, legacy.CONFIG_PATH, dry_run)
        legacy.push_file(ctid, env_file, legacy.ENV_PATH, dry_run)
        verify_application_storage(ctid, dry_run)
    finally:
        legacy.run(["pct", "stop", str(ctid)], dry_run=dry_run, check=False)
        try:
            if plan.separate_mounts:
                set_config_mount_read_only(ctid, plan, dry_run)
        finally:
            legacy.run(
                ["pct", "set", str(ctid), "--entrypoint", legacy.OCI_ENTRYPOINT],
                dry_run=dry_run,
            )
    legacy.run(["pct", "start", str(ctid)], dry_run=dry_run)


def set_config_mount_read_only(ctid: int, plan, dry_run: bool):
    if dry_run:
        mount = (
            f"{plan.config_storage}:{plan.config_disk_gb},"
            f"mp={legacy.CONFIG_DIR},backup=1,ro=1"
        )
    else:
        mount = legacy.config_mount_value(ctid, plan, dry_run=False)
    legacy.run(
        ["pct", "set", str(ctid), "--mp1", mount],
        dry_run=dry_run,
    )


def verify_application_storage(ctid: int, dry_run: bool):
    probe = (
        "import os,pwd,pathlib; "
        "u=pwd.getpwnam('inkplate'); os.setgroups([]); os.setgid(u.pw_gid); "
        "os.setuid(u.pw_uid); "
        "pathlib.Path('/srv/inkplate/server/config/config.yaml').read_bytes(); "
        "p=pathlib.Path('/srv/inkplate/server/data/.installer-write-test'); "
        "p.write_text('ok',encoding='utf-8'); p.unlink()"
    )
    legacy.run(
        ["pct", "exec", str(ctid), "--", "python3", "-c", probe],
        dry_run=dry_run,
    )


def verify_runtime_acceptance(ctid: int, require_read_only_config: bool):
    probe = (
        "import errno,os,pathlib,pwd,sys; "
        "u=pwd.getpwnam('inkplate'); actual=os.stat('/proc/1').st_uid; "
        "actual == u.pw_uid or sys.exit(f'PID 1 uid {actual}, expected {u.pw_uid}'); "
    )
    if require_read_only_config:
        probe += (
            "p=pathlib.Path('/srv/inkplate/server/config/.installer-ro-test'); "
            "\ntry: p.write_text('unexpected',encoding='utf-8')"
            "\nexcept OSError as e:"
            "\n if e.errno != errno.EROFS: raise"
            "\nelse: p.unlink(missing_ok=True); sys.exit('config mount is writable')"
        )
    result = subprocess.run(
        ["pct", "exec", str(ctid), "--", "python3", "-c", probe],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "probe failed"
        raise SystemExit(
            "ERROR: running container failed the OCI security acceptance check: "
            f"{detail}. Ensure the PVE host is fully updated."
        )
    print("Runtime verification: application user and config protection are correct.")


def show_plan(args, ctid, tag, digest, plan):
    print("\nDeployment plan")
    print("---------------")
    print(f"Image: {IMAGE}:{tag}")
    print(f"Digest: {digest}")
    print(f"Platform: linux/{host_oci_architecture()}")
    print(f"Container: {ctid} ({args.hostname}), unprivileged, on-boot")
    print(f"Network: {args.bridge}, IPv4 DHCP, IPv6 SLAAC")
    core_label = "core" if args.cores == 1 else "cores"
    print(f"Resources: {args.cores} {core_label}, {args.memory} MiB RAM")
    print(f"OCI cache: {args.template_storage}")
    print(f"Root disk: {plan.root_storage}:{args.disk_gb} GiB")
    if plan.separate_mounts:
        print(f"Data: {plan.data_storage}:{plan.data_disk_gb} GiB -> {legacy.DATA_DIR} (rw)")
        print(f"Config: {plan.config_storage}:{plan.config_disk_gb} GiB -> {legacy.CONFIG_DIR} (ro after setup)")
    else:
        print("Config/data: stored on the root disk")


def rollback_container(ctid: int):
    print(f"Deployment failed; removing newly created container {ctid}.", file=sys.stderr)
    legacy.run(["pct", "stop", str(ctid)], check=False)
    legacy.run(["pct", "destroy", str(ctid), "--purge", "1"], check=False)


def container_exists(ctid: int):
    result = subprocess.run(
        ["pct", "config", str(ctid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def run_cli(main_func=main):
    def cancel(signum, frame):
        raise KeyboardInterrupt

    previous_handlers = {}
    for signum in (signal.SIGHUP, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, cancel)
    try:
        return main_func()
    except KeyboardInterrupt:
        print("\nOCI deployment cancelled.", file=sys.stderr)
        return 130
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: command failed with exit status {exc.returncode}.", file=sys.stderr)
        return exc.returncode or 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(run_cli())

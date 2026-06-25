#!/usr/bin/env python3
"""Experimental Proxmox VE 9 OCI/LXC installer."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "bin" / "install_server.py"
SPEC = importlib.util.spec_from_file_location("install_server", INSTALLER_PATH)
install_server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(install_server)

IMAGE = "ghcr.io/orangespyderman/inkplate10-weather-cal"
TEMPLATE_STORAGE = "local"
OCI_ENTRYPOINT = "/srv/inkplate/server/container_entrypoint.py"
CONFIG_DIR = "/srv/inkplate/server/config"
DATA_DIR = "/srv/inkplate/server/data"
CONFIG_PATH = f"{CONFIG_DIR}/config.yaml"
ENV_PATH = f"{CONFIG_DIR}/weather.env"


class StoragePlan(NamedTuple):
    root_storage: str
    separate_mounts: bool
    data_storage: str | None = None
    config_storage: str | None = None
    data_disk_gb: int | None = None
    config_disk_gb: int | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Experimentally deploy the published OCI image as a Proxmox VE 9 "
            "LXC application container."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm container creation without an additional prompt",
    )
    parser.add_argument("--tag", help="OCI image tag; prompts from registry tags")
    parser.add_argument("--ctid", type=int, help="new Proxmox container ID")
    parser.add_argument(
        "--storage",
        help="Proxmox storage for the container root filesystem",
    )
    parser.add_argument(
        "--separate-mounts",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "create separate mounts for /srv/inkplate/server/data and "
            "/srv/inkplate/server/config"
        ),
    )
    parser.add_argument(
        "--data-storage",
        help=f"Proxmox storage for the read-write {DATA_DIR} mount",
    )
    parser.add_argument(
        "--config-storage",
        help=f"Proxmox storage for the read-only {CONFIG_DIR} mount",
    )
    parser.add_argument("--bridge", default="vmbr0")
    parser.add_argument("--hostname", default="inkplate-weather")
    parser.add_argument("--disk-gb", type=int, default=8)
    parser.add_argument("--data-disk-gb", type=int, default=4)
    parser.add_argument("--config-disk-gb", type=int, default=1)
    parser.add_argument("--memory", type=int, default=1024)
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()

    if args.non_interactive and args.answers is None:
        raise SystemExit("ERROR: --non-interactive requires --answers PATH.")
    install_server.configure_answers(args.answers, args.non_interactive)

    print("Experimental Proxmox VE 9 OCI/LXC installer")
    print("-------------------------------------------")
    print("Press Ctrl-C at any time to cancel cleanly.")
    print("Fresh installations only. Existing CTs are never replaced.")
    if args.dry_run:
        print("Dry run: commands and generated files will only be previewed.")
    print()

    validate_arguments(args)
    validate_proxmox(args.dry_run)
    print("Discovering Proxmox storage usable for LXC volumes.")
    storage_options = available_storage(args.dry_run)
    storage_plan = choose_storage_plan(args, storage_options)
    validate_target(storage_plan, args.bridge, args.dry_run)
    ensure_skopeo(args.dry_run)

    tags = available_tags(args.dry_run, args.tag)
    tag = choose_tag(tags, args.tag)
    ctid = args.ctid or next_ctid(args.dry_run)
    ensure_unused_ctid(ctid, args.dry_run)
    digest = image_digest(tag, args.dry_run)

    print()
    print("Deployment")
    print("----------")
    print(f"Image: {IMAGE}:{tag}")
    print(f"Digest: {digest}")
    print(f"CTID: {ctid}")
    print(f"Hostname: {args.hostname}")
    print(f"Root storage: {storage_plan.root_storage}:{args.disk_gb} GB")
    if storage_plan.separate_mounts:
        print(f"Data mount: {storage_plan.data_storage}:{args.data_disk_gb} GB -> {DATA_DIR} (rw)")
        print(
            f"Config mount: {storage_plan.config_storage}:{args.config_disk_gb} GB "
            f"-> {CONFIG_DIR} (ro after bootstrap)"
        )
    else:
        print("Data/config storage: container root filesystem")
    print(f"Network: {args.bridge}, DHCP")
    print(f"Resources: {args.cores} cores, {args.memory} MiB")
    print()

    confirmed = args.yes or install_server.prompt_yes_no(
        "Create and start this experimental Proxmox container?",
        default=False,
        key="proxmox_confirm",
    )
    if not confirmed:
        print("No changes made.")
        return 0

    config_answers = install_server.collect_answers(
        {},
        {},
        mode="systemd",
    )
    config_text = install_server.render_config(config_answers, mode="systemd")
    env_text = install_server.render_env(
        config_answers,
        include_optional=bool(config_answers["netatmo_enabled"]),
    )

    archive_name = archive_filename(tag, digest)
    archive_volume = f"{TEMPLATE_STORAGE}:vztmpl/{archive_name}"
    archive_path = template_archive_path(archive_volume, args.dry_run)

    with tempfile.TemporaryDirectory(prefix="inkplate-proxmox-") as temp_dir:
        temp = Path(temp_dir)
        config_file = temp / "config.yaml"
        env_file = temp / "weather.env"
        write_generated_file(config_file, config_text, 0o600, args.dry_run)
        write_generated_file(env_file, env_text, 0o600, args.dry_run)

        print("Preparing OCI image archive and creating the LXC container.")
        pull_image(tag, archive_path, args.dry_run)
        create_container(ctid, archive_volume, args, storage_plan, tag, digest)
        print("Bootstrapping generated config and secrets inside the container.")
        bootstrap_configuration(
            ctid,
            config_file,
            env_file,
            storage_plan,
            args.dry_run,
        )

    if args.dry_run:
        print()
        print("Dry run complete; no Proxmox container was created.")
        return 0

    wait_until_ready(ctid)
    address = container_address(ctid)
    print()
    print(f"Container {ctid} is ready.")
    print(f"Calendar: http://{address}:8080/calendar.png")
    print(f"Viewer:   http://{address}:8080/app")
    print(f"Logs:     pct console {ctid}")
    return 0


def validate_arguments(args) -> None:
    if args.ctid is not None and not 100 <= args.ctid <= 999999999:
        raise SystemExit("ERROR: --ctid must be between 100 and 999999999.")
    if args.disk_gb < 4:
        raise SystemExit("ERROR: --disk-gb must be at least 4.")
    if args.data_disk_gb < 1:
        raise SystemExit("ERROR: --data-disk-gb must be at least 1.")
    if args.config_disk_gb < 1:
        raise SystemExit("ERROR: --config-disk-gb must be at least 1.")
    if args.memory < 512:
        raise SystemExit("ERROR: --memory must be at least 512 MiB.")
    if args.cores < 1:
        raise SystemExit("ERROR: --cores must be positive.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", args.hostname):
        raise SystemExit("ERROR: --hostname contains invalid characters.")
    if (args.data_storage or args.config_storage) and args.separate_mounts is False:
        raise SystemExit(
            "ERROR: --data-storage/--config-storage cannot be used with "
            "--no-separate-mounts."
        )


def validate_proxmox(dry_run: bool) -> None:
    if dry_run and not shutil.which("pveversion"):
        print("Would check: Proxmox VE 9, root access, pct, pvesm, and pvesh.")
        return
    if os.geteuid() != 0:
        raise SystemExit("ERROR: run the Proxmox installer as root.")
    for command in ("pveversion", "pct", "pvesm", "pvesh"):
        if not shutil.which(command):
            raise SystemExit(f"ERROR: {command} was not found; run on a Proxmox host.")
    result = subprocess.run(
        ["pveversion", "--verbose"],
        capture_output=True,
        text=True,
        check=True,
    )
    manager_version, container_version = parse_proxmox_versions(result.stdout)
    if manager_version != 9:
        raise SystemExit(
            "ERROR: this experimental installer currently requires Proxmox VE 9.x."
        )
    if container_version < (6, 0, 15):
        raise SystemExit(
            "ERROR: OCI import requires pve-container 6.0.15 or newer. "
            "Update this Proxmox VE 9 host first."
        )
    print(
        "Proxmox check: VE 9 with pve-container "
        f"{'.'.join(str(part) for part in container_version)}."
    )


def parse_proxmox_versions(output: str) -> tuple[int, tuple[int, int, int]]:
    manager_match = re.search(r"pve-manager:\s+(\d+)\.", output)
    container_match = re.search(
        r"pve-container:\s+(\d+)\.(\d+)\.(\d+)",
        output,
    )
    if not manager_match:
        raise SystemExit("ERROR: unable to determine the pve-manager version.")
    if not container_match:
        raise SystemExit("ERROR: unable to determine the pve-container version.")
    container_version = tuple(int(part) for part in container_match.groups())
    return int(manager_match.group(1)), container_version


def ensure_skopeo(dry_run: bool) -> None:
    if shutil.which("skopeo"):
        return
    if dry_run:
        print("Would install required package: skopeo")
        return
    if not install_server.prompt_yes_no(
        "Install the required skopeo package with apt?",
        default=True,
        key="install_skopeo",
    ):
        raise SystemExit("ERROR: skopeo is required to inspect and pull OCI images.")
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "--no-install-recommends", "skopeo"])


def available_storage(dry_run: bool) -> list[tuple[str, str]]:
    if dry_run and not shutil.which("pvesm"):
        print("Would list Proxmox storage with rootdir content support.")
        return [
            ("local-lvm", "LXC volume storage"),
            ("local", "directory storage"),
        ]
    result = subprocess.run(
        ["pvesm", "status", "--content", "rootdir"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "ERROR: unable to list Proxmox storage with rootdir support: "
            f"{result.stderr.strip()}"
        )
    options = parse_storage_status(result.stdout)
    if not options:
        raise SystemExit("ERROR: no Proxmox storage with rootdir content support found.")
    print("Available LXC storage:")
    for name, description in options:
        print(f"  - {name}: {description}")
    return options


def parse_storage_status(output: str) -> list[tuple[str, str]]:
    options = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[0].lower() == "name":
            continue
        name, storage_type, status = fields[:3]
        if status != "active":
            continue
        available = fields[5]
        options.append((name, f"{storage_type}, {available} KiB available"))
    return options


def choose_storage_plan(args, storage_options: list[tuple[str, str]]) -> StoragePlan:
    names = {name for name, _ in storage_options}
    default = default_storage(storage_options)
    root_storage = args.storage or install_server.prompt_choice(
        "Root filesystem storage",
        storage_options,
        default=default,
        key="proxmox_root_storage",
    )
    require_storage_choice(root_storage, names, "--storage")

    if args.separate_mounts is not None:
        separate_mounts = args.separate_mounts
    elif args.data_storage or args.config_storage:
        separate_mounts = True
    else:
        separate_mounts = install_server.prompt_yes_no(
            "Create separate Proxmox mounts for config and generated data?",
            default=True,
            key="proxmox_separate_mounts",
        )

    if not separate_mounts:
        return StoragePlan(root_storage=root_storage, separate_mounts=False)

    data_storage = args.data_storage or install_server.prompt_choice(
        f"Data mount storage ({DATA_DIR})",
        storage_options,
        default=root_storage,
        key="proxmox_data_storage",
    )
    config_storage = args.config_storage or install_server.prompt_choice(
        f"Config mount storage ({CONFIG_DIR})",
        storage_options,
        default=root_storage,
        key="proxmox_config_storage",
    )
    require_storage_choice(data_storage, names, "--data-storage")
    require_storage_choice(config_storage, names, "--config-storage")
    return StoragePlan(
        root_storage=root_storage,
        separate_mounts=True,
        data_storage=data_storage,
        config_storage=config_storage,
        data_disk_gb=args.data_disk_gb,
        config_disk_gb=args.config_disk_gb,
    )


def default_storage(storage_options: list[tuple[str, str]]) -> str:
    names = [name for name, _ in storage_options]
    for candidate in ("local-lvm", "local-zfs", "local"):
        if candidate in names:
            return candidate
    return names[0]


def require_storage_choice(storage: str, names: set[str], option: str) -> None:
    if storage not in names:
        raise SystemExit(
            f"ERROR: {option} must be one of the available LXC storages: "
            f"{', '.join(sorted(names))}"
        )


def validate_target(storage_plan: StoragePlan, bridge: str, dry_run: bool) -> None:
    if dry_run and not shutil.which("pvesm"):
        print(f"Would check network bridge {bridge}.")
        return
    if not Path("/sys/class/net", bridge).exists():
        raise SystemExit(f"ERROR: network bridge {bridge!r} does not exist.")


def available_tags(
    dry_run: bool,
    requested: str | None = None,
) -> list[str]:
    if dry_run and not shutil.which("skopeo"):
        print(f"Would query available tags for {IMAGE}.")
        defaults = ["main", "next"]
        return list(dict.fromkeys([requested, *defaults])) if requested else defaults
    result = subprocess.run(
        ["skopeo", "list-tags", f"docker://{IMAGE}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: unable to list OCI tags: {result.stderr.strip()}")
    tags = json.loads(result.stdout).get("Tags", [])
    if not tags:
        raise SystemExit("ERROR: the OCI registry returned no image tags.")
    return sort_tags(tag for tag in tags if supported_v4_tag(tag))


def sort_tags(tags) -> list[str]:
    def version_key(tag: str):
        match = re.fullmatch(
            r"v(\d+)\.(\d+)\.(\d+)",
            tag,
        )
        return tuple(int(part) for part in match.groups()) if match else None

    tags = list(tags)
    versions = sorted(
        (tag for tag in tags if version_key(tag) is not None),
        key=lambda tag: version_key(tag),
        reverse=True,
    )
    branches = [
        tag
        for branch in ("main", "next")
        for tag in tags
        if tag == branch
    ]
    others = sorted(set(tags) - set(versions) - set(branches))
    return versions + branches + others


def supported_v4_tag(tag: str) -> bool:
    if tag in {"main", "next"}:
        return True
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    return match is not None and int(match.group(1)) >= 4


def choose_tag(tags: list[str], requested: str | None) -> str:
    if requested:
        if requested not in tags:
            raise SystemExit(
                f"ERROR: OCI tag {requested!r} is not available. "
                f"Available tags: {', '.join(tags)}"
            )
        return requested
    options = [(tag, tag_description(tag)) for tag in tags]
    default = next((tag for tag in tags if tag.startswith("v")), None)
    default = default or ("main" if "main" in tags else tags[0])
    return install_server.prompt_choice(
        "OCI image tag",
        options,
        default=default,
        key="proxmox_tag",
    )


def tag_description(tag: str) -> str:
    if tag == "main":
        return "stable branch image"
    if tag == "next":
        return "integration branch image"
    if tag.startswith("v"):
        return "versioned release image"
    return "published image"


def next_ctid(dry_run: bool) -> int:
    if dry_run and not shutil.which("pvesh"):
        print("Would request the next available CTID from Proxmox.")
        return 999
    result = subprocess.run(
        ["pvesh", "get", "/cluster/nextid", "--output-format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(json.loads(result.stdout))


def ensure_unused_ctid(ctid: int, dry_run: bool) -> None:
    if dry_run and not shutil.which("pct"):
        print(f"Would verify that CTID {ctid} is unused.")
        return
    result = subprocess.run(
        ["pct", "status", str(ctid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        raise SystemExit(f"ERROR: CTID {ctid} already exists; refusing to replace it.")


def image_digest(tag: str, dry_run: bool) -> str:
    if dry_run and not shutil.which("skopeo"):
        return "sha256:<resolved-during-install>"
    result = subprocess.run(
        [
            "skopeo",
            "inspect",
            "--format",
            "{{.Digest}}",
            f"docker://{IMAGE}:{tag}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: unable to inspect OCI image: {result.stderr.strip()}")
    return result.stdout.strip()


def archive_filename(tag: str, digest: str) -> str:
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]", "-", tag)
    digest_suffix = digest.removeprefix("sha256:")[:12]
    if not re.fullmatch(r"[0-9a-f]{12}", digest_suffix):
        digest_suffix = "unresolved"
    return f"inkplate-weather-{safe_tag}-{digest_suffix}.tar"


def template_archive_path(volume: str, dry_run: bool) -> Path:
    if dry_run and not shutil.which("pvesm"):
        path = Path("/var/lib/vz/template/cache") / volume.rsplit("/", 1)[-1]
        print(f"Would resolve template volume {volume} to {path}.")
        return path
    result = subprocess.run(
        ["pvesm", "path", volume],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "ERROR: unable to resolve Proxmox template storage "
            f"{volume}: {result.stderr.strip()}"
        )
    return Path(result.stdout.strip())


def write_generated_file(path: Path, text: str, mode: int, dry_run: bool) -> None:
    if dry_run:
        print(f"Would generate protected temporary file: {path.name}")
        return
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def pull_image(tag: str, archive_path: Path, dry_run: bool) -> None:
    if archive_path.exists():
        print(f"Using existing OCI archive: {archive_path}")
        return
    archive_path.parent.mkdir(parents=True, exist_ok=True) if not dry_run else None
    run(
        [
            "skopeo",
            "copy",
            "--retry-times",
            "3",
            f"docker://{IMAGE}:{tag}",
            f"oci-archive:{archive_path}",
        ],
        dry_run=dry_run,
    )


def create_container(
    ctid: int,
    archive_volume: str,
    args,
    storage_plan: StoragePlan,
    tag: str,
    digest: str,
) -> None:
    description = (
        "Inkplate Weather Calendar experimental OCI deployment\\n"
        f"Image: {IMAGE}:{tag}\\nDigest: {digest}"
    )
    command = [
        "pct",
        "create",
        str(ctid),
        archive_volume,
        "--hostname",
        args.hostname,
        "--rootfs",
        f"{storage_plan.root_storage}:{args.disk_gb}",
        "--memory",
        str(args.memory),
        "--cores",
        str(args.cores),
        "--swap",
        "512",
        "--net0",
        f"name=eth0,bridge={args.bridge},ip=dhcp,type=veth",
        "--unprivileged",
        "1",
        "--onboot",
        "1",
        "--description",
        description,
    ]
    if storage_plan.separate_mounts:
        command.extend(
            [
                "--mp0",
                f"{storage_plan.data_storage}:{storage_plan.data_disk_gb},mp={DATA_DIR}",
                "--mp1",
                f"{storage_plan.config_storage}:{storage_plan.config_disk_gb},mp={CONFIG_DIR}",
            ]
        )
    run(command, dry_run=args.dry_run)


def bootstrap_configuration(
    ctid: int,
    config_file: Path,
    env_file: Path,
    storage_plan: StoragePlan,
    dry_run: bool,
) -> None:
    run(["pct", "set", str(ctid), "--entrypoint", "/bin/sleep infinity"], dry_run)
    run(["pct", "start", str(ctid)], dry_run)
    if not dry_run:
        time.sleep(2)
    try:
        run(
            [
                "pct",
                "exec",
                str(ctid),
                "--",
                "mkdir",
                "-p",
                "/srv/inkplate/server/config",
                "/srv/inkplate/server/data",
            ],
            dry_run,
        )
        push_file(ctid, config_file, CONFIG_PATH, dry_run)
        push_file(ctid, env_file, ENV_PATH, dry_run)
    finally:
        run(["pct", "stop", str(ctid)], dry_run, check=False)
        if storage_plan.separate_mounts:
            set_config_mount_read_only(ctid, storage_plan, dry_run)
        run(
            ["pct", "set", str(ctid), "--entrypoint", OCI_ENTRYPOINT],
            dry_run,
        )
    run(["pct", "start", str(ctid)], dry_run)


def set_config_mount_read_only(
    ctid: int,
    storage_plan: StoragePlan,
    dry_run: bool,
) -> None:
    mount = config_mount_value(ctid, storage_plan, dry_run)
    run(["pct", "set", str(ctid), "--mp1", mount], dry_run)


def config_mount_value(
    ctid: int,
    storage_plan: StoragePlan,
    dry_run: bool,
) -> str:
    fallback = f"{storage_plan.config_storage}:{storage_plan.config_disk_gb},mp={CONFIG_DIR}"
    if dry_run:
        return with_mount_option(fallback, "ro", "1")
    result = subprocess.run(
        ["pct", "config", str(ctid)],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("mp1:"):
            return with_mount_option(line.split(":", 1)[1].strip(), "ro", "1")
    raise SystemExit(f"ERROR: unable to find config mount mp1 for container {ctid}.")


def with_mount_option(mount: str, key: str, value: str) -> str:
    parts = [
        part
        for part in mount.split(",")
        if part and part.split("=", 1)[0] != key
    ]
    parts.append(f"{key}={value}")
    return ",".join(parts)


def push_file(ctid: int, source: Path, destination: str, dry_run: bool) -> None:
    run(
        [
            "pct",
            "push",
            str(ctid),
            str(source),
            destination,
            "--user",
            "inkplate",
            "--group",
            "inkplate",
            "--perms",
            "0600",
        ],
        dry_run,
    )


def wait_until_ready(ctid: int) -> None:
    probe = (
        "import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:8080/api/v1/ready', timeout=5)"
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
        f"ERROR: container {ctid} did not become ready. Inspect with: pct console {ctid}"
    )


def container_address(ctid: int) -> str:
    result = subprocess.run(
        ["pct", "exec", str(ctid), "--", "hostname", "-I"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return f"<CT-{ctid}-IP>"
    return result.stdout.split()[0]


def run(cmd: list[str], dry_run: bool = False, check: bool = True):
    print(f"{'Would run' if dry_run else 'Running'}: {' '.join(cmd)}")
    if dry_run:
        return None
    return subprocess.run(cmd, check=check, text=True)


def run_cli(main_func=main) -> int:
    try:
        return main_func()
    except KeyboardInterrupt:
        print("\nProxmox installer cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(run_cli())

#!/usr/bin/env python3
"""Deploy the server installers to a remote host over SSH."""

from __future__ import annotations

import argparse
import io
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REMOTE_ANSWERS = ".remote/answers.json"
SUPPORTED_MODES = ("proxmox", "systemd")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(args)

    ssh = ssh_base_command(args)
    installer_args = remote_installer_args(args)

    print("Inkplate remote installer")
    print("--------------------------")
    print(f"Target: {args.target}")
    print(f"Mode: {args.mode}")
    print(f"Remote command: {shlex.join(installer_args)}")
    if args.dry_run:
        print("Dry run: no SSH connection or remote changes will be made.")
        print(f"Would create a temporary directory on {args.target}.")
        print("Would stream a bundle containing Git-tracked repository files.")
        if args.answers:
            print("Would include the answers file with mode 0600.")
        print(f"Would run: {shlex.join([*ssh, '<remote installer command>'])}")
        return 0

    validate_local_checkout()
    remote_uid = remote_output(ssh, "id -u").strip()
    privilege_prefix = remote_privilege_prefix(
        ssh,
        args.mode,
        remote_uid,
        non_interactive=args.non_interactive,
    )
    check_remote_requirements(
        ssh,
        args.mode,
        remote_uid,
        privilege_prefix,
        non_interactive=args.non_interactive,
    )
    remote_dir = remote_output(ssh, "mktemp -d /tmp/inkplate-install.XXXXXXXX").strip()
    if not remote_dir.startswith("/tmp/inkplate-install."):
        raise SystemExit(
            f"ERROR: unexpected remote temporary directory: {remote_dir!r}"
        )

    print(f"Remote workspace: {args.target}:{remote_dir}")
    try:
        bundle = create_bundle(args.answers)
        upload_bundle(ssh, remote_dir, bundle)
        command = build_remote_command(
            remote_dir,
            privilege_prefix,
            installer_args,
        )
        result = run_remote_installer(
            ssh,
            command,
            interactive=not args.non_interactive,
        )
        if result.returncode != 0:
            print(
                f"Remote installer failed; workspace retained at {remote_dir}.",
                file=sys.stderr,
            )
            return result.returncode
        cleanup_remote(ssh, remote_dir)
    except BaseException:
        print(
            f"Remote deployment did not complete; workspace retained at {remote_dir}.",
            file=sys.stderr,
        )
        raise

    print("Remote installation completed successfully.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Upload a protected temporary bundle and run the Proxmox or "
            "systemd installer on a remote SSH host."
        )
    )
    parser.add_argument("target", help="SSH target, for example root@pve1")
    parser.add_argument("--mode", choices=SUPPORTED_MODES, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview locally without opening an SSH connection",
    )
    parser.add_argument(
        "--remote-dry-run",
        action="store_true",
        help="connect and run the selected installer in dry-run mode",
    )
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--identity", type=Path)
    parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        metavar="OPTION",
        help="additional ssh -o option; may be repeated",
    )
    parser.add_argument("--tag")
    parser.add_argument("--ctid", type=int)
    parser.add_argument("--storage")
    parser.add_argument("--separate-mounts", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--data-storage")
    parser.add_argument("--config-storage")
    parser.add_argument("--bridge")
    parser.add_argument("--hostname")
    parser.add_argument("--disk-gb", type=int)
    parser.add_argument("--data-disk-gb", type=int)
    parser.add_argument("--config-disk-gb", type=int)
    parser.add_argument("--memory", type=int)
    parser.add_argument("--cores", type=int)
    return parser


def validate_args(args) -> None:
    if args.non_interactive and args.answers is None:
        raise SystemExit("ERROR: --non-interactive requires --answers PATH.")
    if args.dry_run and args.remote_dry_run:
        raise SystemExit("ERROR: use either --dry-run or --remote-dry-run, not both.")
    if args.answers is not None and not args.answers.is_file():
        raise SystemExit(f"ERROR: answers file not found: {args.answers}")
    if args.port is not None and not 1 <= args.port <= 65535:
        raise SystemExit("ERROR: --port must be from 1 to 65535.")
    if args.identity is not None and not args.identity.is_file():
        raise SystemExit(f"ERROR: SSH identity file not found: {args.identity}")
    proxmox_only = (
        "tag",
        "ctid",
        "storage",
        "separate_mounts",
        "data_storage",
        "config_storage",
        "bridge",
        "hostname",
        "disk_gb",
        "data_disk_gb",
        "config_disk_gb",
        "memory",
        "cores",
    )
    if args.mode != "proxmox":
        used = [
            name
            for name in proxmox_only
            if getattr(args, name, None) is not None
        ]
        if args.yes:
            used.append("yes")
        if used:
            options = ", ".join("--" + name.replace("_", "-") for name in used)
            raise SystemExit(
                f"ERROR: these options are only valid in Proxmox mode: {options}"
            )


def validate_local_checkout() -> None:
    required = (
        REPO_ROOT / "bin" / "install_server.py",
        REPO_ROOT / "bin" / "install_proxmox.py",
        REPO_ROOT / "server" / "server.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(
            "ERROR: run from a complete repository checkout; missing: "
            + ", ".join(missing)
        )


def ssh_base_command(args) -> list[str]:
    command = ["ssh"]
    if args.port is not None:
        command.extend(["-p", str(args.port)])
    if args.identity is not None:
        command.extend(["-i", str(args.identity)])
    for option in args.ssh_option:
        command.extend(["-o", option])
    command.append(args.target)
    return command


def remote_installer_args(args) -> list[str]:
    if args.mode == "systemd":
        command = ["./bin/install_server", "--mode", "systemd"]
    else:
        command = ["./bin/install_proxmox"]
        mappings = (
            ("tag", "--tag"),
            ("ctid", "--ctid"),
            ("storage", "--storage"),
            ("data_storage", "--data-storage"),
            ("config_storage", "--config-storage"),
            ("bridge", "--bridge"),
            ("hostname", "--hostname"),
            ("disk_gb", "--disk-gb"),
            ("data_disk_gb", "--data-disk-gb"),
            ("config_disk_gb", "--config-disk-gb"),
            ("memory", "--memory"),
            ("cores", "--cores"),
        )
        for attribute, option in mappings:
            value = getattr(args, attribute, None)
            if value is not None:
                command.extend([option, str(value)])
        separate_mounts = getattr(args, "separate_mounts", None)
        if separate_mounts is True:
            command.append("--separate-mounts")
        elif separate_mounts is False:
            command.append("--no-separate-mounts")
        if args.yes:
            command.append("--yes")
    if args.answers is not None:
        command.extend(["--answers", REMOTE_ANSWERS])
    if args.non_interactive:
        command.append("--non-interactive")
    if args.remote_dry_run:
        command.append("--dry-run")
    return command


def remote_output(ssh: list[str], command: str) -> str:
    result = subprocess.run(
        [*ssh, command],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"ERROR: remote command failed: {detail}")
    return result.stdout


def remote_privilege_prefix(
    ssh: list[str],
    mode: str,
    remote_uid: str,
    non_interactive: bool,
) -> list[str]:
    if mode != "proxmox" or remote_uid == "0":
        return []
    sudo_check = "command -v sudo >/dev/null"
    if non_interactive:
        sudo_check += " && sudo -n true"
    result = subprocess.run(
        [*ssh, sudo_check],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        requirement = (
            "non-interactive sudo"
            if non_interactive
            else "sudo access"
        )
        raise SystemExit(
            "ERROR: Proxmox mode requires root. Log in as root or configure "
            f"{requirement} for the remote account."
        )
    return ["sudo", "-H"]


def check_remote_requirements(
    ssh: list[str],
    mode: str,
    remote_uid: str,
    privilege_prefix: list[str],
    non_interactive: bool,
) -> None:
    commands = ["python3", "tar"]
    if mode == "proxmox" and remote_uid == "0":
        commands.extend(["pct", "pveversion"])
    remote_output(ssh, requirement_script(commands))

    if (
        mode == "proxmox"
        and remote_uid != "0"
        and non_interactive
    ):
        elevated_script = requirement_script(["pct", "pveversion"])
        command = shlex.join(
            [*privilege_prefix, "sh", "-c", elevated_script]
        )
        remote_output(ssh, command)


def requirement_script(commands: list[str]) -> str:
    quoted = " ".join(shlex.quote(command) for command in commands)
    return (
        "set -eu; "
        f"for command in {quoted}; do "
        'command -v "$command" >/dev/null || '
        '{ echo "missing remote command: $command" >&2; exit 1; }; '
        "done"
    )


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    files = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        path = REPO_ROOT / relative
        if path.is_file() or path.is_symlink():
            files.append(relative)
    return files


def create_bundle(answers: Path | None) -> Path:
    temporary = tempfile.NamedTemporaryFile(
        prefix="inkplate-remote-",
        suffix=".tar.gz",
        delete=False,
    )
    temporary.close()
    bundle = Path(temporary.name)
    try:
        with tarfile.open(bundle, "w:gz") as archive:
            for relative in tracked_files():
                archive.add(
                    REPO_ROOT / relative,
                    arcname=relative.as_posix(),
                    recursive=False,
                )
            if answers is not None:
                data = answers.read_bytes()
                info = tarfile.TarInfo(REMOTE_ANSWERS)
                info.size = len(data)
                info.mode = 0o600
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
        return bundle
    except BaseException:
        bundle.unlink(missing_ok=True)
        raise


def upload_bundle(ssh: list[str], remote_dir: str, bundle: Path) -> None:
    command = (
        f"umask 077 && tar -xzf - -C {shlex.quote(remote_dir)} "
        f"&& chmod 0700 {shlex.quote(remote_dir)}"
    )
    print(f"Uploading protected deployment bundle to {remote_dir}.")
    try:
        with bundle.open("rb") as source:
            result = subprocess.run([*ssh, command], stdin=source)
        if result.returncode != 0:
            raise SystemExit("ERROR: failed to upload the remote deployment bundle.")
    finally:
        bundle.unlink(missing_ok=True)


def build_remote_command(
    remote_dir: str,
    privilege_prefix: list[str],
    installer_args: list[str],
) -> str:
    command = [*privilege_prefix, *installer_args]
    return (
        f"cd {shlex.quote(remote_dir)} && "
        f"chmod 0700 bin/install_server bin/install_proxmox && "
        f"{shlex.join(command)}"
    )


def run_remote_installer(
    ssh: list[str],
    command: str,
    interactive: bool,
) -> subprocess.CompletedProcess:
    ssh_command = [*ssh]
    if interactive:
        ssh_command.insert(-1, "-t")
    ssh_command.append(command)
    print("Starting remote installer.")
    return subprocess.run(ssh_command)


def cleanup_remote(ssh: list[str], remote_dir: str) -> None:
    command = f"rm -rf -- {shlex.quote(remote_dir)}"
    result = subprocess.run(
        [*ssh, command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        print(
            f"WARNING: unable to remove remote workspace {remote_dir}.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())

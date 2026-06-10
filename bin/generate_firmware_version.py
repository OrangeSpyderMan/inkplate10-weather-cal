#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path


def c_string(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def git(*args):
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def detected_version():
    exact_tags = git("tag", "--points-at", "HEAD", "--sort=-version:refname")
    if exact_tags:
        return exact_tags.splitlines()[0]

    release_tags = git("tag", "--sort=-version:refname")
    release = release_tags.splitlines()[0] if release_tags else "v0.0.0"
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = ".dirty" if git("status", "--porcelain", "--untracked-files=no") else ""
    return f"{release}+g{commit}{dirty}"


def main():
    parser = argparse.ArgumentParser(
        description="Generate the firmware build version header."
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("version", nargs="?", default="")
    args = parser.parse_args()
    version = args.version or detected_version()

    content = (
        "#ifndef FIRMWARE_VERSION_H\n"
        "#define FIRMWARE_VERSION_H\n\n"
        f'#define FIRMWARE_VERSION "{c_string(version)}"\n\n'
        "#endif\n"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists() or args.output.read_text() != content:
        args.output.write_text(content)


if __name__ == "__main__":
    main()

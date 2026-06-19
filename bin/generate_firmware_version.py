#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from build_version import detected_version


def c_string(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


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

#!/usr/bin/env python3

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Generate a C++ header containing Inkplate firmware YAML."
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    config_text = args.config.read_text()
    if not config_text.strip():
        raise ValueError("Firmware config is empty")

    delimiter = "INKPLATE_CONFIG"
    if f"){delimiter}\"" in config_text:
        raise ValueError("Firmware config contains the raw-string delimiter")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "#ifndef EMBEDDED_CONFIG_H\n"
        "#define EMBEDDED_CONFIG_H\n\n"
        "static const char EMBEDDED_CONFIG_YAML[] = "
        f'R"{delimiter}({config_text}){delimiter}";\n\n'
        "#endif\n"
    )


if __name__ == "__main__":
    main()

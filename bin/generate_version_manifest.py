#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from build_version import generate_version_manifest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="Generate the shared server and firmware version manifest."
    )
    parser.add_argument("--version", default="")
    parser.add_argument("--build-date")
    args = parser.parse_args()

    try:
        manifest = generate_version_manifest(
            REPO_ROOT,
            version=args.version or None,
            build_date=args.build_date,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from None
    print(manifest["version"])


if __name__ == "__main__":
    main()

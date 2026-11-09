#!/usr/bin/env python3
"""Build versioned wheel/sdist without changing the checkout."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="4.2.0.dev0")
    parser.add_argument("--outdir", type=Path, default=Path("dist/weatherproxy"))
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    from packaging.version import Version
    version = str(Version(version))
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="weather-proxy-build-") as temporary:
        stage = Path(temporary)
        text = (root / "pyproject.toml").read_text()
        text = re.sub(r'^version = "[^"]+"$', f'version = "{version}"', text, count=1, flags=re.MULTILINE)
        (stage / "pyproject.toml").write_text(text)
        (stage / "README.md").write_text((root / "docs/weather-proxy.md").read_text())
        shutil.copy2(root / "LICENSE", stage / "LICENSE")
        for package in ("weather", "weather_proxy"):
            shutil.copytree(root / "server" / package, stage / "server" / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        subprocess.run([sys.executable, "-m", "build", "--outdir", str(args.outdir.resolve()), str(stage)], check=True)
        # A deployable source bundle includes bootstrap installers and examples,
        # while the Python sdist remains a small installable package.
        for relative in ("bin/install_weather_proxy.py", "bin/install_proxmox.py", "bin/install_server.py",
                         "server/output_profiles.py", "server/build_version.py", "Dockerfile.weatherproxy",
                         "docker-compose.weatherproxy.yml", "weather-proxy.example.yaml",
                         "docs/weather-proxy.md", "docs/weather-proxy-design.md"):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, destination)
        bundle_name = f"weatherproxy-deployment-{version}"
        with tarfile.open(args.outdir.resolve() / f"{bundle_name}.tar.gz", "w:gz") as archive:
            for relative in ("pyproject.toml", "README.md", "LICENSE", "bin", "docs", "Dockerfile.weatherproxy",
                             "docker-compose.weatherproxy.yml", "weather-proxy.example.yaml", "server/weather",
                             "server/weather_proxy", "server/output_profiles.py", "server/build_version.py"):
                archive.add(stage / relative, arcname=f"{bundle_name}/{relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

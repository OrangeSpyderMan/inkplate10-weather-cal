import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


VERSION_MANIFEST_FILENAME = ".version.json"


def git(*args, cwd=None):
    checkout = Path(cwd or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={checkout}", *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=checkout,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def detected_version(cwd=None):
    exact = git(
        "describe",
        "--tags",
        "--exact-match",
        "--match",
        "v[0-9]*",
        "HEAD",
        cwd=cwd,
    )
    if exact:
        return exact

    release = git(
        "describe",
        "--tags",
        "--abbrev=0",
        "--match",
        "v[0-9]*",
        "HEAD",
        cwd=cwd,
    ) or "v0.0.0"
    commit = git("rev-parse", "--short", "HEAD", cwd=cwd) or "unknown"
    dirty = (
        ".dirty"
        if git("status", "--porcelain", "--untracked-files=no", cwd=cwd)
        else ""
    )
    return f"{release}+g{commit}{dirty}"


def version_manifest(cwd=None, version=None, build_date=None):
    revision = git("rev-parse", "--short", "HEAD", cwd=cwd)
    if not revision:
        raise ValueError(
            "cannot resolve the Git revision for version metadata"
        )
    return {
        "version": version or detected_version(cwd=cwd),
        "revision": revision,
        "build_date": build_date or datetime.now(timezone.utc).isoformat(),
    }


def read_version_manifest(root):
    path = Path(root) / VERSION_MANIFEST_FILENAME
    try:
        with path.open(encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def write_version_manifest(root, manifest):
    path = Path(root) / VERSION_MANIFEST_FILENAME
    content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


def generate_version_manifest(root, version=None, build_date=None):
    manifest = version_manifest(
        cwd=root,
        version=version,
        build_date=build_date,
    )
    write_version_manifest(root, manifest)
    return manifest

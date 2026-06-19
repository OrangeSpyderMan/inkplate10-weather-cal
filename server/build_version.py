import subprocess


def git(*args, cwd=None):
    try:
        result = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
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

import json
import os
import tempfile
import time
from pathlib import Path


DEFAULT_OUTPUT_PROFILE = "inkplate10-portrait"
DEFAULT_TEMPORARY_FILE_MAX_AGE_SECONDS = 24 * 60 * 60


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root)

    @property
    def snapshot_path(self):
        return self.root / "weather.json"

    def output_path(self, profile, filename):
        return self.root / "outputs" / profile / filename

    def write_snapshot(self, snapshot):
        self.write_json(self.snapshot_path, snapshot.to_payload())

    def cleanup_stale_temporary_files(
        self,
        max_age_seconds=DEFAULT_TEMPORARY_FILE_MAX_AGE_SECONDS,
        now=None,
    ):
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        if not self.root.exists():
            return []

        cutoff = (time.time() if now is None else now) - max_age_seconds
        removed = []
        for path in self.root.rglob(".*"):
            if (
                path.is_file()
                and _is_temporary_artifact(path)
                and path.stat().st_mtime < cutoff
            ):
                path.unlink()
                removed.append(path)
        return removed

    @staticmethod
    def write_json(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(value, output)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise


def _is_temporary_artifact(path):
    return ".tmp" in path.name

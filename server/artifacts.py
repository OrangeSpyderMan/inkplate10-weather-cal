import json
import hashlib
import os
import tempfile
import time
from pathlib import Path

DEFAULT_TEMPORARY_FILE_MAX_AGE_SECONDS = 24 * 60 * 60


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root)

    @property
    def snapshot_path(self):
        return self.root / "weather.json"

    @property
    def ready_path(self):
        return self.root / "ready.json"

    def output_path(self, profile, filename):
        return self.root / "outputs" / profile / filename

    def write_snapshot(self, snapshot):
        self.write_json(self.snapshot_path, snapshot.to_payload())

    def write_ready(self, snapshot, profiles):
        outputs = {}
        for profile in profiles.values():
            output_path = self.output_path(profile.name, profile.filename)
            outputs[profile.name] = {
                "path": str(output_path.relative_to(self.root)),
                "signature": self.file_signature(output_path),
                "sha256": self.file_sha256(output_path),
                "renderer": profile.renderer,
            }

        self.write_json(
            self.ready_path,
            {
                "generated_at": snapshot.generated_at.isoformat(),
                "snapshot": {
                    "path": self.snapshot_path.name,
                    "signature": self.file_signature(self.snapshot_path),
                    "sha256": self.file_sha256(self.snapshot_path),
                },
                "outputs": outputs,
            },
        )

    def output_status(self, profiles):
        status = {name: False for name in profiles}
        try:
            with self.ready_path.open(encoding="utf-8") as ready_file:
                ready = json.load(ready_file)
            snapshot = ready["snapshot"]
            snapshot_matches = (
                snapshot["signature"]
                == self.file_signature(self.root / snapshot["path"])
                and snapshot["sha256"]
                == self.file_sha256(self.root / snapshot["path"])
            )
            for name, profile in profiles.items():
                output = ready["outputs"][name]
                expected_path = self.output_path(name, profile.filename)
                status[name] = (
                    snapshot_matches
                    and self.root / output["path"] == expected_path
                    and output["signature"] == self.file_signature(expected_path)
                    and output["sha256"] == self.file_sha256(expected_path)
                )
        except (KeyError, OSError, TypeError, json.JSONDecodeError):
            pass
        return status

    def producer_cycle_complete(self, profiles):
        return all(self.output_status(profiles).values())

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

    @staticmethod
    def file_signature(path):
        stat = Path(path).stat()
        return {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }

    @staticmethod
    def file_sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as artifact:
            for block in iter(lambda: artifact.read(64 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


def _is_temporary_artifact(path):
    return ".tmp" in path.name

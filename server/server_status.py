from datetime import datetime, timezone
from pathlib import Path

from build_version import read_version_manifest


STATUS_SCHEMA_VERSION = "1.0"


def utc_now():
    return datetime.now(timezone.utc)


def isoformat(value):
    return value.isoformat() if value is not None else None


def runtime_metadata(base_dir=None):
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parents[1]
    manifest = read_version_manifest(root)

    return {
        "version": manifest.get("version", "unknown"),
        "revision": manifest.get("revision", "unknown"),
        "build_date": manifest.get("build_date", "unknown"),
    }


class ServerStatus:
    def __init__(
        self,
        store,
        *,
        mode,
        refresh_seconds,
        forecast_provider,
        realtime_provider,
        profiles,
        mqtt_publisher=None,
        metadata=None,
        now=utc_now,
    ):
        self.store = store
        self.profiles = profiles
        self.mqtt_publisher = mqtt_publisher
        self.now = now
        self.payload = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "updated_at": None,
            "runtime": metadata or runtime_metadata(),
            "producer": {
                "state": "starting",
                "mode": mode,
                "refresh_interval_seconds": refresh_seconds,
                "cycle_started_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "next_refresh_at": None,
            },
            "providers": {
                "forecast": forecast_provider,
                "realtime": realtime_provider,
            },
            "weather": {
                "generated_at": None,
            },
            "readiness": {
                "snapshot": False,
                "outputs": {name: False for name in profiles},
                "producer_cycle_complete": False,
            },
            "mqtt": {
                "enabled": mqtt_publisher is not None,
                "last_publish_at": None,
                "last_publish_success": None,
                "last_error": None,
            },
            "error": None,
        }

    def transition(
        self,
        state,
        *,
        cycle_started_at=None,
        success_at=None,
        failure_at=None,
        next_refresh_at=None,
        weather_generated_at=None,
        error=None,
    ):
        now = self.now()
        producer = self.payload["producer"]
        producer["state"] = state
        if cycle_started_at is not None:
            producer["cycle_started_at"] = isoformat(cycle_started_at)
        if success_at is not None:
            producer["last_success_at"] = isoformat(success_at)
        if failure_at is not None:
            producer["last_failure_at"] = isoformat(failure_at)
        producer["next_refresh_at"] = isoformat(next_refresh_at)
        if weather_generated_at is not None:
            self.payload["weather"]["generated_at"] = isoformat(
                weather_generated_at
            )
        self.payload["error"] = error
        self._refresh_readiness()
        self.payload["updated_at"] = isoformat(now)
        self._write_and_publish(now)
        return self.payload

    def _refresh_readiness(self):
        outputs = self.store.output_status(self.profiles)
        snapshot = self.store.snapshot_path.is_file()
        self.payload["readiness"] = {
            "snapshot": snapshot,
            "outputs": outputs,
            "producer_cycle_complete": snapshot and all(outputs.values()),
        }

    def _write_and_publish(self, now):
        mqtt_status = self.payload["mqtt"]
        if self.mqtt_publisher is None:
            self.store.write_status(self.payload)
            return

        mqtt_status["last_publish_at"] = isoformat(now)
        mqtt_status["last_publish_success"] = True
        mqtt_status["last_error"] = None
        self.store.write_status(self.payload)
        result = self.mqtt_publisher.publish_server_status(self.payload)
        if not result["success"]:
            mqtt_status["last_publish_success"] = False
            mqtt_status["last_error"] = result["error"]
            self.store.write_status(self.payload)


def sanitized_error(stage, exc, timestamp=None):
    return {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
        "timestamp": isoformat(timestamp or utc_now()),
    }

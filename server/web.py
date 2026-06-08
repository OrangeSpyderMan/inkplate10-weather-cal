import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from artifacts import ArtifactStore, DEFAULT_OUTPUT_PROFILE


SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SERVER_DIR / "data"
DEFAULT_PWA_DIR = SERVER_DIR / "views" / "pwa"


def create_app(data_dir=None, pwa_dir=None, legacy_calendar_served=None):
    app = Flask(__name__)
    app.config["ARTIFACT_STORE"] = ArtifactStore(
        data_dir or os.environ.get("INKPLATE_DATA_DIR", DEFAULT_DATA_DIR)
    )
    app.config["PWA_DIR"] = Path(pwa_dir or DEFAULT_PWA_DIR)
    app.config["LEGACY_CALENDAR_SERVED"] = legacy_calendar_served
    register_routes(app)
    return app


def register_routes(app):
    @app.get("/api/v1/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/v1/ready")
    def ready():
        store = _store(app)
        snapshot_exists = store.snapshot_path.is_file()
        output_exists = _calendar_path(store).is_file()
        status = "ready" if snapshot_exists and output_exists else "not_ready"
        response = {
            "status": status,
            "snapshot": snapshot_exists,
            "outputs": {DEFAULT_OUTPUT_PROFILE: output_exists},
        }
        return jsonify(response), 200 if status == "ready" else 503

    @app.get("/api/v1/weather")
    def weather():
        path = _store(app).snapshot_path
        if not path.is_file():
            return jsonify({"error": "weather snapshot is not available"}), 503

        try:
            with path.open(encoding="utf-8") as snapshot_file:
                payload = json.load(snapshot_file)
        except (OSError, json.JSONDecodeError):
            logging.getLogger("server").exception(
                "Failed to read weather snapshot from %s", path
            )
            return jsonify({"error": "weather snapshot is not available"}), 503

        response = jsonify(payload)
        stat = path.stat()
        response.last_modified = datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        )
        response.cache_control.no_cache = True
        response.set_etag(
            f"{stat.st_mtime_ns}-{stat.st_size}",
            weak=True,
        )
        return response.make_conditional(request)

    @app.get(f"/outputs/{DEFAULT_OUTPUT_PROFILE}/calendar.png")
    def calendar_output():
        return _send_calendar(app, as_attachment=False)

    @app.get("/calendar.png")
    def legacy_calendar_output():
        response = _send_calendar(app, as_attachment=True)
        callback = app.config.get("LEGACY_CALENDAR_SERVED")
        if callback is not None:
            callback()
        return response

    @app.get("/")
    @app.get("/app")
    @app.get("/app/")
    @app.get("/app/index.html")
    def pwa():
        return send_from_directory(app.config["PWA_DIR"], "index.html")

    @app.get("/app.css")
    def pwa_css():
        return send_from_directory(app.config["PWA_DIR"], "app.css")

    @app.get("/app.js")
    def pwa_js():
        return send_from_directory(app.config["PWA_DIR"], "app.js")

    @app.get("/manifest.webmanifest")
    def pwa_manifest():
        return send_from_directory(
            app.config["PWA_DIR"],
            "manifest.webmanifest",
            mimetype="application/manifest+json",
        )

    @app.get("/sw.js")
    def pwa_service_worker():
        return send_from_directory(
            app.config["PWA_DIR"],
            "sw.js",
            mimetype="application/javascript",
        )

    @app.get("/icons/<path:filename>")
    def pwa_icon(filename):
        return send_from_directory(app.config["PWA_DIR"] / "icons", filename)

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.config["PWA_DIR"] / "icons",
            "weathercal-favicon.ico",
            mimetype="image/x-icon",
        )

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def apple_touch_icon():
        return send_from_directory(
            app.config["PWA_DIR"] / "icons",
            "weathercal-icon-192.png",
            mimetype="image/png",
        )

    @app.get("/app/calendar.png")
    def legacy_pwa_calendar_output():
        return _send_calendar(app, as_attachment=False)


def _store(app):
    return app.config["ARTIFACT_STORE"]


def _calendar_path(store):
    return store.output_path(DEFAULT_OUTPUT_PROFILE, "calendar.png")


def _send_calendar(app, as_attachment):
    path = _calendar_path(_store(app))
    if not path.is_file():
        logging.getLogger("server").error("%s: no such file exists", path)
        abort(404)

    return send_file(
        path,
        mimetype="image/png",
        as_attachment=as_attachment,
        download_name="calendar.png" if as_attachment else None,
        max_age=0,
        conditional=True,
    )
